import { useSyncExternalStore } from "react";
import { workerClient } from "../services/workerClient";
import type { DiscoveryGetStatusResponse, Envelope } from "../types/generated";

export type DiscoveryLiveRun = {
  sourceId: string;
  processed: number;
  refsTotal: number;
  newJobs: number;
  updatedJobs: number;
};

export type DiscoverySnapshot = {
  status: DiscoveryGetStatusResponse | null;
  live: Record<string, DiscoveryLiveRun>;
  finishedBoards: number;
  finishedPostings: number;
  lastResult: Record<string, unknown> | null;
  revision: number;
  error: string | null;
};

const EMPTY: DiscoverySnapshot = {
  status: null,
  live: {},
  finishedBoards: 0,
  finishedPostings: 0,
  lastResult: null,
  revision: 0,
  error: null,
};

let snapshot: DiscoverySnapshot = EMPTY;
let listeners = new Set<() => void>();
let started = false;
let stopEvents: (() => void) | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let refreshing: Promise<void> | null = null;

function emit(next: Partial<DiscoverySnapshot>): void {
  snapshot = { ...snapshot, ...next };
  for (const listener of listeners) listener();
}

function active(status: DiscoveryGetStatusResponse | null): boolean {
  return Boolean(
    status?.running ||
      (status?.active_crawls ?? 0) > 0 ||
      (status?.initial_pending ?? 0) > 0 ||
      Object.keys(snapshot.live).length > 0,
  );
}

function schedule(): void {
  if (timer !== null) clearTimeout(timer);
  const delay = workerClient.isPreview && active(snapshot.status)
    ? 5000
    : active(snapshot.status)
      ? 2000
      : 12_000;
  timer = setTimeout(() => {
    timer = null;
    void refreshDiscoveryStatus().finally(schedule);
  }, delay);
}

export function refreshDiscoveryStatus(): Promise<void> {
  if (refreshing) return refreshing;
  refreshing = workerClient
    .getDiscoveryStatus()
    .then((status) => {
      emit({ status, error: null });
    })
    .catch((exc: unknown) => {
      emit({ error: exc instanceof Error ? exc.message : String(exc) });
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

function handleEvent(event: Envelope): void {
  const data = (event.data ?? {}) as Record<string, unknown>;
  if (event.topic === "crawl.started") {
    const runId = String(data.run_id ?? "");
    if (!runId) return;
    emit({
      live: {
        ...snapshot.live,
        [runId]: {
          sourceId: String(data.source_id ?? ""),
          processed: 0,
          refsTotal: 0,
          newJobs: 0,
          updatedJobs: 0,
        },
      },
    });
    void refreshDiscoveryStatus().finally(schedule);
  } else if (event.topic === "crawl.progress" || event.topic === "discovery.progress") {
    const runId = String(data.run_id ?? "");
    if (!runId) return;
    const current = snapshot.live[runId];
    const refsTotal =
      Number(data.refs_total ?? data.refs_discovered ?? current?.refsTotal ?? 0) || 0;
    emit({
      live: {
        ...snapshot.live,
        [runId]: {
          sourceId: current?.sourceId ?? String(data.source_id ?? ""),
          processed: Number(data.processed ?? current?.processed ?? 0) || 0,
          refsTotal,
          newJobs: Number(data.new ?? current?.newJobs ?? 0) || 0,
          updatedJobs: Number(data.updated ?? current?.updatedJobs ?? 0) || 0,
        },
      },
    });
  } else if (event.topic === "crawl.completed" || event.topic === "crawl.failed") {
    const runId = String(data.run_id ?? "");
    const next = { ...snapshot.live };
    delete next[runId];
    const stats = (data.stats ?? {}) as Record<string, unknown>;
    const discovered = Number(stats.new ?? stats.discovered ?? 0) || 0;
    emit({
      live: next,
      finishedBoards: snapshot.finishedBoards + 1,
      finishedPostings: snapshot.finishedPostings + discovered,
      revision: snapshot.revision + 1,
    });
    void refreshDiscoveryStatus().finally(schedule);
  } else if (event.topic === "discovery.pass_started") {
    emit({ finishedBoards: 0, finishedPostings: 0 });
  } else if (event.topic === "discovery.pass_completed") {
    emit({
      lastResult: data,
      finishedBoards: 0,
      finishedPostings: 0,
      revision: snapshot.revision + 1,
    });
    void refreshDiscoveryStatus().finally(schedule);
  } else if (
    event.topic === "discovery.source_completed" ||
    event.topic === "discovery.posting_verified"
  ) {
    emit({ revision: snapshot.revision + 1 });
  }
}

function start(): void {
  if (started) return;
  started = true;
  try {
    void workerClient
      .onEvent(handleEvent)
      .then((stop) => {
        stopEvents = stop;
      })
      .catch(() => undefined);
  } catch {
    // A bridge without events still polls.
  }
  void refreshDiscoveryStatus().finally(schedule);
}

export function useDiscoveryStatus(): DiscoverySnapshot {
  start();
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => snapshot,
    () => EMPTY,
  );
}

/** Test seam: the module store is shared across renders by design. */
export function resetDiscoveryStatusForTests(): void {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  stopEvents?.();
  stopEvents = null;
  started = false;
  listeners = new Set();
  snapshot = EMPTY;
}

export function discoveryProgressPercent(value: DiscoverySnapshot): number {
  const total = value.status?.initial_total ?? 0;
  const done = Math.min(total, value.status?.initial_done ?? 0);
  return total > 0 ? Math.round((done / total) * 100) : 0;
}

export function activeCrawlCount(value: DiscoverySnapshot): number {
  return Math.max(
    Number(value.status?.active_crawls ?? 0),
    Object.keys(value.live).length,
  );
}

export function discoveredCount(value: DiscoverySnapshot): number {
  // The server count already includes jobs written by in-flight crawls, so the
  // live per-run counters must not be added on top of it.
  return Number(value.status?.postings_found ?? 0);
}
