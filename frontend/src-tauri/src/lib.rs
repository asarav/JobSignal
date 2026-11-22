//! Lodestar Tauri shell and Python worker supervisor.
//!
//! The Rust layer is a transport boundary, not a business layer. It:
//!
//! - spawns the Python worker (`python -m app.worker.main --data-dir <path>`),
//! - forwards `worker_request` / `worker_shutdown` from the webview,
//! - streams worker events on `worker://event`,
//! - enforces the allowlist generated from `schemas/ipc/methods.json`,
//! - restarts a crashed worker with exponential backoff (max 5 restarts in
//!   5 minutes).
//!
//! It never opens SQLite and never interprets payloads.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{mpsc, oneshot, watch, Mutex};

mod allowlist {
    include!(concat!(env!("OUT_DIR"), "/allowlist.rs"));
}

pub use allowlist::{ALLOWED_METHODS, FORBIDDEN_PREFIXES, METHOD_TIMEOUTS, PROTOCOL_VERSION};

/// Maximum NDJSON line accepted from the worker (8 MB, spec §15.1).
pub const MAX_LINE_BYTES: usize = 8 * 1024 * 1024;
/// How long a request waits for `worker.ready` / a worker restart.
const READY_TIMEOUT: Duration = Duration::from_secs(15);
/// Fallback response timeout for a method without its own entry.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_RESTARTS: usize = 5;
const RESTART_WINDOW: Duration = Duration::from_secs(300);
const BASE_BACKOFF_SECS: u64 = 2;
const MAX_BACKOFF_SECS: u64 = 60;

/// Exponential backoff for the n-th consecutive restart (1-based).
pub fn backoff_delay(attempt: usize) -> Duration {
    let exponent = attempt.saturating_sub(1).min(5) as u32;
    let seconds = BASE_BACKOFF_SECS
        .saturating_mul(1u64 << exponent)
        .min(MAX_BACKOFF_SECS);
    Duration::from_secs(seconds)
}

/// How long the shell waits for one method's response.
///
/// A crawl, a discovery pass or a matching run is not bounded by a single HTTP
/// request, so the wait is per method (declared in the generated catalogue).
/// Timing out while the worker kept going used to report a failure for work
/// that was still running.
pub fn request_timeout(method: &str) -> Duration {
    let seconds = METHOD_TIMEOUTS
        .iter()
        .find(|(name, _)| *name == method)
        .map(|(_, seconds)| *seconds)
        .unwrap_or(REQUEST_TIMEOUT.as_secs());
    Duration::from_secs(seconds.max(1))
}

/// True when `method` may be forwarded to the worker.
pub fn is_allowed(method: &str) -> bool {
    ALLOWED_METHODS.contains(&method)
        && !FORBIDDEN_PREFIXES
            .iter()
            .any(|prefix| method.starts_with(prefix))
}

/// Sliding-window restart budget: at most `max_restarts` restarts inside
/// `window`. Time is passed in so the policy is deterministic under test.
pub struct RestartPolicy {
    max_restarts: usize,
    window: Duration,
    history: Vec<Instant>,
}

impl RestartPolicy {
    pub fn new(max_restarts: usize, window: Duration) -> Self {
        Self {
            max_restarts,
            window,
            history: Vec::new(),
        }
    }

    /// Records an attempt at `now` and returns its 1-based ordinal, or `None`
    /// when the budget is exhausted.
    pub fn attempt(&mut self, now: Instant) -> Option<usize> {
        self.history.retain(|instant| now.duration_since(*instant) < self.window);
        if self.history.len() >= self.max_restarts {
            return None;
        }
        self.history.push(now);
        Some(self.history.len())
    }
}

/// The process-wide restart budget from the spec: 5 restarts per 5 minutes.
pub fn default_restart_policy() -> RestartPolicy {
    RestartPolicy::new(MAX_RESTARTS, RESTART_WINDOW)
}

fn error_envelope(request_id: Option<&str>, code: &str, message: &str) -> Value {
    json!({
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "ok": false,
        "error": {
            "code": code,
            "message": message,
            "retryable": false,
            "details": {}
        }
    })
}

/// Shared handle to the supervisor. Cheap to clone; Tauri manages one.
#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<SupervisorInner>,
}

struct SupervisorInner {
    app: AppHandle,
    data_dir: PathBuf,
    allowlist: HashSet<&'static str>,
    /// Writer channel for the currently running worker, if any.
    outbound: Mutex<Option<mpsc::Sender<Vec<u8>>>>,
    /// Requests awaiting a response, keyed by request id.
    pending: Mutex<std::collections::HashMap<String, oneshot::Sender<Value>>>,
    ready: watch::Sender<bool>,
    stopping: AtomicBool,
    restart_policy: Mutex<RestartPolicy>,
}

impl Supervisor {
    fn new(app: AppHandle, data_dir: PathBuf) -> Self {
        let (ready, _) = watch::channel(false);
        Self {
            inner: Arc::new(SupervisorInner {
                app,
                data_dir,
                allowlist: ALLOWED_METHODS.iter().copied().collect(),
                outbound: Mutex::new(None),
                pending: Mutex::new(std::collections::HashMap::new()),
                ready,
                stopping: AtomicBool::new(false),
                restart_policy: Mutex::new(default_restart_policy()),
            }),
        }
    }

    /// Directory the worker stores its data in; openable files must live here.
    pub fn data_dir(&self) -> &Path {
        &self.inner.data_dir
    }

    /// Spawn the supervisor loop on the Tauri runtime.
    fn start(self) {
        tauri::async_runtime::spawn(async move { self.supervise().await });
    }

    async fn supervise(&self) {
        loop {
            if self.inner.stopping.load(Ordering::SeqCst) {
                break;
            }
            let reason = self.run_once().await;
            self.inner.ready.send_replace(false);
            if self.inner.stopping.load(Ordering::SeqCst) {
                break;
            }
            log::warn!("worker exited ({reason}); evaluating restart policy");

            let attempt = self.record_restart().await;
            match attempt {
                Some(n) => {
                    let delay = backoff_delay(n);
                    log::warn!(
                        "restarting worker in {:?} (restart {} of {} within {:?})",
                        delay,
                        n,
                        MAX_RESTARTS,
                        RESTART_WINDOW
                    );
                    tokio::time::sleep(delay).await;
                }
                None => {
                    log::error!(
                        "worker crash loop: {} restarts within {:?}; giving up",
                        MAX_RESTARTS,
                        RESTART_WINDOW
                    );
                    break;
                }
            }
        }
        self.inner.ready.send_replace(false);
    }

    /// Records a restart and returns its 1-based ordinal, or `None` when the
    /// restart budget is exhausted.
    async fn record_restart(&self) -> Option<usize> {
        self.inner.restart_policy.lock().await.attempt(Instant::now())
    }

    /// Runs one worker process until it exits.
    async fn run_once(&self) -> String {
        let mut child = match spawn_worker(&self.inner.data_dir) {
            Ok(child) => child,
            Err(error) => return format!("spawn failed: {error}"),
        };
        let stdin = match child.stdin.take() {
            Some(stdin) => stdin,
            None => return "worker stdin unavailable".to_string(),
        };
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => return "worker stdout unavailable".to_string(),
        };
        if let Some(stderr) = child.stderr.take() {
            tape_stderr(stderr);
        }

        let (tx, rx) = mpsc::channel::<Vec<u8>>(64);
        *self.inner.outbound.lock().await = Some(tx);
        let writer = tauri::async_runtime::spawn(write_loop(stdin, rx));

        let exit_reason = self.read_loop(stdout).await;

        *self.inner.outbound.lock().await = None;
        drop(writer);
        // Pending requests are dropped so callers observe worker unavailability.
        self.inner.pending.lock().await.clear();
        let _ = child.kill().await;
        let _ = child.wait().await;
        exit_reason
    }

    async fn read_loop(&self, stdout: ChildStdout) -> String {
        let mut lines = BufReader::new(stdout).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => {
                    if line.len() > MAX_LINE_BYTES {
                        log::warn!("dropping worker line larger than {MAX_LINE_BYTES} bytes");
                        continue;
                    }
                    if line.trim().is_empty() {
                        continue;
                    }
                    let value: Value = match serde_json::from_str(&line) {
                        Ok(value) => value,
                        Err(error) => {
                            log::warn!("invalid worker NDJSON: {error}");
                            continue;
                        }
                    };
                    self.handle_worker_value(value).await;
                }
                Ok(None) => return "stdout closed".to_string(),
                Err(error) => return format!("stdout read error: {error}"),
            }
        }
    }

    async fn handle_worker_value(&self, value: Value) {
        match value.get("type").and_then(Value::as_str) {
            Some("response") => {
                let id = value
                    .get("id")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                if let Some(id) = id {
                    if let Some(sender) = self.inner.pending.lock().await.remove(&id) {
                        let _ = sender.send(value);
                    }
                }
            }
            Some("event") => {
                if value.get("topic").and_then(Value::as_str) == Some("worker.ready") {
                    self.inner.ready.send_replace(true);
                }
                if let Err(error) = self.inner.app.emit("worker://event", &value) {
                    log::warn!("failed to emit worker event: {error}");
                }
            }
            _ => {}
        }
    }

    async fn wait_ready(&self) -> bool {
        if *self.inner.ready.borrow() {
            return true;
        }
        let mut receiver = self.inner.ready.subscribe();
        tokio::time::timeout(READY_TIMEOUT, async {
            loop {
                if *receiver.borrow_and_update() {
                    return true;
                }
                if receiver.changed().await.is_err() {
                    return false;
                }
            }
        })
        .await
        .unwrap_or(false)
    }

    /// Send one request envelope and return the worker's response envelope.
    async fn request(&self, method: &str, params: Value) -> Value {
        let request_id = uuid::Uuid::new_v4().to_string();

        if !self.inner.allowlist.contains(method) || !is_allowed(method) {
            return error_envelope(
                Some(&request_id),
                "not_found",
                &format!("method not allowed: {method}"),
            );
        }
        if !self.wait_ready().await {
            return error_envelope(
                Some(&request_id),
                "internal.unexpected",
                "worker is not available",
            );
        }

        let Some(sender) = self.inner.outbound.lock().await.clone() else {
            return error_envelope(
                Some(&request_id),
                "internal.unexpected",
                "worker is not running",
            );
        };

        let envelope = json!({
            "v": PROTOCOL_VERSION,
            "type": "request",
            "id": request_id,
            "method": method,
            "params": params,
            "meta": {},
        });
        let mut line = match serde_json::to_vec(&envelope) {
            Ok(line) => line,
            Err(error) => {
                return error_envelope(
                    Some(&request_id),
                    "internal.unexpected",
                    &format!("failed to encode request: {error}"),
                )
            }
        };
        line.push(b'\n');

        let (tx, rx) = oneshot::channel();
        self.inner
            .pending
            .lock()
            .await
            .insert(request_id.clone(), tx);
        if sender.send(line).await.is_err() {
            self.inner.pending.lock().await.remove(&request_id);
            return error_envelope(
                Some(&request_id),
                "internal.unexpected",
                "worker stdin is closed",
            );
        }

        match tokio::time::timeout(request_timeout(method), rx).await {
            Ok(Ok(value)) => value,
            Ok(Err(_)) => error_envelope(
                Some(&request_id),
                "internal.unexpected",
                "worker restarted before responding",
            ),
            Err(_) => {
                self.inner.pending.lock().await.remove(&request_id);
                error_envelope(Some(&request_id), "task.timeout", "worker request timed out")
            }
        }
    }

    async fn shutdown(&self) {
        // Mark stopping first so the supervisor never restarts a worker that
        // exits because we asked it to.
        self.inner.stopping.store(true, Ordering::SeqCst);
        let _ = self.request("system.shutdown", json!({})).await;
        *self.inner.outbound.lock().await = None;
    }
}

impl SupervisorInner {
    // Reserved for future per-supervisor helpers.
}

async fn write_loop(mut stdin: ChildStdin, mut receiver: mpsc::Receiver<Vec<u8>>) {
    while let Some(line) = receiver.recv().await {
        if let Err(error) = stdin.write_all(&line).await {
            log::warn!("worker stdin write failed: {error}");
            break;
        }
        if let Err(error) = stdin.flush().await {
            log::warn!("worker stdin flush failed: {error}");
            break;
        }
    }
}

fn tape_stderr(stderr: tokio::process::ChildStderr) {
    tauri::async_runtime::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            // The worker already emits JSON lines; pass them through to the
            // Rust log so the desktop log file is the single sink.
            log::info!("worker: {line}");
        }
    });
}

/// Locates the Python source tree for development runs.
///
/// In a packaged build the worker ships next to the binary, so there is no
/// repository root; the installed package is used instead. During `tauri dev`
/// the shell's cwd is `frontend/src-tauri`, so the supervisor must add the
/// repository root to `PYTHONPATH` explicitly.
pub fn repo_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("LODESTAR_REPO_ROOT") {
        let path = PathBuf::from(explicit);
        return path.is_dir().then_some(path);
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest.parent()?.parent()?.to_path_buf();
    root.join("app").is_dir().then_some(root)
}

fn spawn_worker(data_dir: &Path) -> std::io::Result<Child> {
    let python = std::env::var("LODESTAR_PYTHON").unwrap_or_else(|_| "python".to_string());
    let mut command = Command::new(&python);
    command
        .arg("-m")
        .arg("app.worker.main")
        .arg("--data-dir")
        .arg(data_dir)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);

    if let Some(root) = repo_root() {
        let mut paths = vec![root.clone()];
        if let Ok(existing) = std::env::var("PYTHONPATH") {
            if !existing.is_empty() {
                paths.push(PathBuf::from(existing));
            }
        }
        if let Ok(joined) = std::env::join_paths(paths) {
            command.env("PYTHONPATH", joined);
        }
        command.current_dir(&root);
        log::info!(
            "spawning worker: {python} -m app.worker.main --data-dir {} (cwd {})",
            data_dir.display(),
            root.display()
        );
    } else {
        log::info!(
            "spawning worker: {python} -m app.worker.main --data-dir {}",
            data_dir.display()
        );
    }
    command.spawn()
}

fn data_dir(app: &AppHandle) -> PathBuf {
    if let Ok(explicit) = std::env::var("LODESTAR_DATA_DIR") {
        return PathBuf::from(explicit);
    }
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("worker")
}

/// Resolves `candidate` and refuses anything outside `root` (resume copies
/// live under the worker data dir, so no other path may be opened).
fn resolve_inside(root: &Path, candidate: &Path) -> Result<PathBuf, String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("data directory unavailable: {error}"))?;
    let resolved = candidate
        .canonicalize()
        .map_err(|error| format!("file unavailable: {error}"))?;
    if !resolved.starts_with(&root) {
        return Err("refusing to open a file outside the app data directory".to_string());
    }
    Ok(resolved)
}

/// Opens a stored resume copy in the OS default application.
#[tauri::command]
fn open_original_file(state: State<'_, Supervisor>, path: String) -> Result<(), String> {
    let resolved = resolve_inside(state.data_dir(), Path::new(&path))?;
    tauri_plugin_opener::open_path(resolved.to_string_lossy().to_string(), None::<&str>)
        .map_err(|error| format!("could not open the file: {error}"))
}

#[tauri::command]
async fn worker_request(
    state: State<'_, Supervisor>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    Ok(state.request(&method, params.unwrap_or_else(|| json!({}))).await)
}

#[tauri::command]
async fn worker_shutdown(state: State<'_, Supervisor>) -> Result<Value, String> {
    state.shutdown().await;
    Ok(json!({ "ok": true }))
}

/// Build and run the Tauri application.
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let directory = data_dir(&handle);
            if let Err(error) = std::fs::create_dir_all(&directory) {
                log::error!("failed to create data dir {}: {error}", directory.display());
            }
            let supervisor = Supervisor::new(handle, directory);
            supervisor.clone().start();
            app.manage(supervisor);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_request,
            worker_shutdown,
            open_original_file
        ])
        .build(tauri::generate_context!())
        .expect("error while building Lodestar")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(supervisor) = app.try_state::<Supervisor>() {
                    supervisor.inner.stopping.store(true, Ordering::SeqCst);
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allowlist_matches_schema_registry() {
        assert!(is_allowed("system.hello"));
        assert!(is_allowed("diagnostics.sleep"));
        assert!(!is_allowed("system.unknown"));
    }

    /// The permanent invariant, not a milestone snapshot. Nothing that could
    /// submit, click or script a page may ever be forwarded (§13.5); which
    /// milestone's methods exist is the Python catalogue's business, and this
    /// allowlist already mirrors it because both are generated from
    /// `schemas/ipc/methods.json`.
    #[test]
    fn submission_capable_methods_are_never_allowed() {
        for method in [
            "browser.submit",
            "browser.click",
            "browser.press",
            "browser.evaluate",
            "browser.type",
        ] {
            assert!(
                !is_allowed(method),
                "{method} must never be forwarded: the user submits, not the app"
            );
        }
        for prefix in FORBIDDEN_PREFIXES {
            assert!(
                !ALLOWED_METHODS.iter().any(|method| method.starts_with(prefix)),
                "allowlist must not contain {prefix}*, found a generated entry"
            );
        }
        // A declared method of this build is reachable; a typo is not.
        assert!(is_allowed("applications.create"));
        assert!(!is_allowed("applications.does_not_exist"));
    }

    #[test]
    fn backoff_is_exponential_and_capped() {
        assert_eq!(backoff_delay(1), Duration::from_secs(2));
        assert_eq!(backoff_delay(2), Duration::from_secs(4));
        assert_eq!(backoff_delay(3), Duration::from_secs(8));
        assert_eq!(backoff_delay(4), Duration::from_secs(16));
        assert_eq!(backoff_delay(5), Duration::from_secs(32));
        assert_eq!(backoff_delay(6), Duration::from_secs(60));
        assert_eq!(backoff_delay(99), Duration::from_secs(60));
    }

    #[test]
    fn protocol_version_is_one() {
        assert_eq!(PROTOCOL_VERSION, 1);
    }

    #[test]
    fn restart_budget_allows_five_then_stops() {
        let mut policy = default_restart_policy();
        let start = Instant::now();
        for attempt in 1..=5 {
            assert_eq!(policy.attempt(start), Some(attempt));
        }
        // The sixth crash inside the window is fatal: the supervisor gives up.
        assert_eq!(policy.attempt(start), None);
        assert_eq!(policy.attempt(start + Duration::from_secs(10)), None);
    }

    #[test]
    fn restart_window_slides() {
        let mut policy = RestartPolicy::new(2, Duration::from_secs(300));
        let start = Instant::now();
        assert_eq!(policy.attempt(start), Some(1));
        assert_eq!(policy.attempt(start + Duration::from_secs(60)), Some(2));
        // Third crash inside the window is refused.
        assert_eq!(policy.attempt(start + Duration::from_secs(120)), None);
        // Once the first attempt ages out, a new restart is allowed again.
        assert_eq!(policy.attempt(start + Duration::from_secs(301)), Some(2));
    }

    #[test]
    fn empty_and_unknown_methods_are_never_allowed() {
        for method in ["", " ", "SYSTEM.HELLO", "system.hello ", "system.hello\n"] {
            assert!(!is_allowed(method), "{method:?} must not be forwarded");
        }
        // The allowlist is generated from the schema registry; it must not
        // contain blanks or duplicates.
        assert!(ALLOWED_METHODS.len() > 0);
        let unique: std::collections::HashSet<&&str> = ALLOWED_METHODS.iter().collect();
        assert_eq!(unique.len(), ALLOWED_METHODS.len());
        assert!(ALLOWED_METHODS.iter().all(|method| !method.trim().is_empty()));
    }

    #[test]
    fn open_validation_rejects_paths_outside_the_data_dir() {
        let base = std::env::temp_dir().join(format!("lodestar-open-{}", uuid::Uuid::new_v4()));
        let root = base.join("worker");
        let outside = base.join("outside.txt");
        std::fs::create_dir_all(root.join("resumes")).unwrap();
        std::fs::write(root.join("resumes").join("cv.txt"), b"x").unwrap();
        std::fs::write(&outside, b"x").unwrap();

        assert!(resolve_inside(&root, &root.join("resumes").join("cv.txt")).is_ok());
        assert!(resolve_inside(&root, &outside).is_err());
        assert!(resolve_inside(&root, &root.join("missing.txt")).is_err());

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn repo_root_contains_the_python_package() {
        // The dev supervisor resolves the worker module from here; if this
        // breaks, `tauri dev` spawns a worker that cannot import `app`.
        let root = repo_root().expect("repository root should be discoverable");
        assert!(root.join("app").join("worker").join("main.py").is_file());
    }
}
