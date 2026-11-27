import { useEffect, useRef, useSyncExternalStore } from "react";
import { workerClient } from "../services/workerClient";
import type { Envelope } from "../types/generated";

const TOPICS = new Set([
  "crawl.completed",
  "discovery.pass_completed",
  "discovery.source_completed",
  "discovery.posting_verified",
  "matching.completed",
  "matching.updated",
]);

let revision = 0;
let listeners = new Set<() => void>();
let started = false;
let unsubscribe: (() => void) | null = null;
let debounce: ReturnType<typeof setTimeout> | null = null;
let poll: ReturnType<typeof setTimeout> | null = null;
let pollingFallback = false;
let fallbackWasActive = false;
let fallbackSignature = "";
let focusBound = false;

function emit(): void {
  revision += 1;
  for (const listener of listeners) listener();
}

function bumpSoon(): void {
  if (debounce !== null) clearTimeout(debounce);
  debounce = setTimeout(() => {
    debounce = null;
    emit();
  }, 500);
}

function handleEvent(event: Envelope): void {
  const topic = event.topic ?? "";
  if (TOPICS.has(topic) || topic.startsWith("matching.")) bumpSoon();
}

function bindFocus(): void {
  if (focusBound || typeof window === "undefined") return;
  focusBound = true;
  const bump = () => bumpSoon();
  window.addEventListener("focus", bump);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") bump();
  });
}

function startPollingFallback(): void {
  if (pollingFallback) return;
  pollingFallback = true;
  void checkDiscovery();
}

function scheduleFallback(): void {
  if (poll !== null) clearTimeout(poll);
  poll = setTimeout(() => {
    poll = null;
    void checkDiscovery();
  }, 5000);
}

async function checkDiscovery(): Promise<void> {
  try {
    const status = await workerClient.getDiscoveryStatus();
    const active = Boolean(
      status.running ||
        (status.active_crawls ?? 0) > 0 ||
        (status.initial_pending ?? 0) > 0,
    );
    const signature = [
      status.postings_found,
      status.last_crawl_at,
      status.last_pass_at,
      status.initial_done,
    ].join("|");
    const changed = fallbackSignature !== "" && signature !== fallbackSignature;
    // Re-fetch while a crawl runs, and once more when it finishes so the last
    // jobs are not stranded behind the idle transition. A short crawl that
    // starts and ends between polls is caught by its changed status signature.
    if (active || fallbackWasActive || changed) emit();
    fallbackWasActive = active;
    fallbackSignature = signature;
    scheduleFallback();
  } catch {
    scheduleFallback();
  }
}

function start(): void {
  if (started) return;
  started = true;
  bindFocus();
  if (workerClient.isPreview) startPollingFallback();
  try {
    void workerClient
      .onEvent(handleEvent)
      .then((stop) => {
        unsubscribe = stop;
      })
      .catch(() => startPollingFallback());
  } catch {
    // A bridge without events still needs to keep the view current.
    startPollingFallback();
  }
}

/**
 * Re-fetch when the worker reports new data, or when the window regains focus.
 *
 * One shared subscription and one debounced revision serve every screen, so a
 * completed crawl does not create a listener per component.
 */
export function useLiveRefresh(callback: () => void): void {
  start();
  const current = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => revision,
    () => 0,
  );
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  const seen = useRef(current);

  useEffect(() => {
    if (current === seen.current) return;
    seen.current = current;
    callbackRef.current();
  }, [current]);
}

export function resetLiveRefreshForTests(): void {
  if (debounce !== null) clearTimeout(debounce);
  if (poll !== null) clearTimeout(poll);
  debounce = null;
  poll = null;
  pollingFallback = false;
  fallbackWasActive = false;
  fallbackSignature = "";
  unsubscribe?.();
  unsubscribe = null;
  started = false;
  focusBound = false;
  revision = 0;
  listeners = new Set();
}
