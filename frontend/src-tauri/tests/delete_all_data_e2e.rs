//! §18.2 end to end: typed confirmation, worker exit, path-scoped wipe,
//! restart, and a fresh schema-18 database, against the frozen sidecar.
//!
//! Run with a built sidecar:
//!   python scripts/build_worker.py
//!   cargo test --test delete_all_data_e2e -- --nocapture

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use lodestar_lib::Supervisor;
use serde_json::json;

fn frozen_worker() -> Option<PathBuf> {
    let directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("binaries");
    let mut candidates: Vec<PathBuf> = std::fs::read_dir(directory)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.is_file()
                && path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("lodestar-worker"))
        })
        .collect();
    candidates.sort();
    candidates.into_iter().next()
}

async fn wait_until<F>(timeout: Duration, mut condition: F) -> bool
where
    F: FnMut() -> bool,
{
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if condition() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    condition()
}

#[cfg(windows)]
fn process_is_alive(pid: u32) -> bool {
    let filter = format!("PID eq {pid}");
    let output = std::process::Command::new("tasklist")
        .args(["/FI", &filter, "/NH", "/FO", "CSV"])
        .output();
    match output {
        Ok(output) => String::from_utf8_lossy(&output.stdout).contains(&format!("\"{pid}\"")),
        Err(_) => false,
    }
}

#[cfg(unix)]
fn process_is_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .is_ok_and(|status| status.success())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn delete_all_data_end_to_end_against_a_real_worker() {
    let Some(worker) = frozen_worker() else {
        eprintln!("skipping delete-all-data e2e: no frozen worker in binaries/");
        return;
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = std::fs::metadata(&worker).unwrap().permissions();
        permissions.set_mode(0o755);
        std::fs::set_permissions(&worker, permissions).unwrap();
    }

    let root = std::env::temp_dir().join(format!("lodestar-delete-e2e-{}", uuid::Uuid::new_v4()));
    let data_dir = root.join("data");
    let sibling = root.join("sibling");
    std::fs::create_dir_all(data_dir.join("nested")).unwrap();
    std::fs::create_dir_all(sibling.join("deeper")).unwrap();
    std::fs::write(sibling.join("survive.txt"), b"outside-data").unwrap();
    std::fs::write(sibling.join("deeper/keep.txt"), b"nested-outside").unwrap();

    let supervisor = Supervisor::headless_for_test(data_dir.clone(), worker);
    let loop_task = supervisor.clone().spawn_for_test();

    assert!(
        tokio::time::timeout(Duration::from_secs(90), supervisor.wait_ready_for_test())
            .await
            .unwrap_or(false),
        "initial worker never became ready"
    );
    let database = data_dir.join("lodestar.sqlite");
    assert!(wait_until(Duration::from_secs(30), || database.is_file()).await);
    let initial = supervisor
        .request_for_test(
            "system.hello",
            json!({"protocol_version": 1, "ui_version": "delete-e2e"}),
        )
        .await;
    assert_eq!(initial["ok"], true, "{initial}");
    assert_eq!(initial["result"]["schema_version"], 18);
    let old_pid = supervisor.last_child_pid_for_test();
    assert_ne!(old_pid, 0);

    // Decoys placed after the live database exists, inside the data dir.
    std::fs::write(data_dir.join("decoy.txt"), b"must disappear").unwrap();
    std::fs::write(data_dir.join("nested/decoy.txt"), b"must disappear").unwrap();

    for wrong in ["", "delete", "Delete", "DELETE!", "yes"] {
        assert!(
            supervisor.delete_all_data_for_test(wrong).await.is_err(),
            "{wrong:?} must be rejected"
        );
        assert_eq!(
            supervisor.last_child_pid_for_test(),
            old_pid,
            "a rejected confirmation must not restart the worker"
        );
        assert!(data_dir.join("decoy.txt").is_file());
        assert!(database.is_file());
        assert!(sibling.join("survive.txt").is_file());
        let hello = supervisor
            .request_for_test(
                "system.hello",
                json!({"protocol_version": 1, "ui_version": "delete-e2e"}),
            )
            .await;
        assert_eq!(hello["ok"], true, "worker stopped by a rejected request");
    }

    supervisor
        .delete_all_data_for_test("DELETE")
        .await
        .expect("confirmed delete failed");

    assert!(
        wait_until(Duration::from_secs(30), || !process_is_alive(old_pid)).await,
        "the original worker process {old_pid} did not exit"
    );
    assert!(
        wait_until(Duration::from_secs(60), || {
            let pid = supervisor.last_child_pid_for_test();
            pid != 0 && pid != old_pid
        })
        .await,
        "the worker was not restarted"
    );
    let new_pid = supervisor.last_child_pid_for_test();
    assert_ne!(new_pid, old_pid);
    assert!(!process_is_alive(old_pid));

    assert!(data_dir.is_dir(), "the data directory itself must survive");
    assert!(!data_dir.join("decoy.txt").exists());
    assert!(!data_dir.join("nested").exists());
    assert_eq!(
        std::fs::read(sibling.join("survive.txt")).unwrap(),
        b"outside-data"
    );
    assert_eq!(
        std::fs::read(sibling.join("deeper/keep.txt")).unwrap(),
        b"nested-outside"
    );

    assert!(
        tokio::time::timeout(Duration::from_secs(90), supervisor.wait_ready_for_test())
            .await
            .unwrap_or(false),
        "restarted worker never became ready"
    );
    assert!(wait_until(Duration::from_secs(30), || database.is_file()).await);
    let fresh = supervisor
        .request_for_test(
            "system.hello",
            json!({"protocol_version": 1, "ui_version": "delete-e2e"}),
        )
        .await;
    assert_eq!(fresh["ok"], true, "{fresh}");
    assert_eq!(fresh["result"]["schema_version"], 18);
    eprintln!(
        "delete-all-data e2e: worker {old_pid} exited, {new_pid} restarted, \
         data dir wiped, sibling intact, schema 18"
    );

    supervisor.shutdown_for_test().await;
    let _ = tokio::time::timeout(Duration::from_secs(20), loop_task).await;
    std::fs::remove_dir_all(root).ok();
}
