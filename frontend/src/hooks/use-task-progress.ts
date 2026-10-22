"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Optional sub-progress; older backends only send progress/message/status. */
export type ProgressEvent = {
  progress?: number;
  message?: string;
  status?: string;
  ts?: number;
  stage?: string;
  stage_progress?: number;
  eta_seconds?: number | null;
  elapsed_seconds?: number;
  waiting?: boolean;
};

export type ProgressSource = "sse" | "db";

const TERMINAL = ["completed", "cancelled", "error"];

/** SSE is the fast path; reconciliation also recovers missed terminal events. */
export function useTaskProgress<T>({ taskId, active, refresh, onProgress, onClip }: {
  taskId: string;
  active: boolean;
  refresh: () => Promise<unknown>;
  onProgress: (event: ProgressEvent, source: ProgressSource) => void;
  onClip: (clip: T) => void;
}) {
  const callbacks = useRef({ refresh, onProgress, onClip });
  callbacks.current = { refresh, onProgress, onClip };
  const [reconnecting, setReconnecting] = useState(false);
  // What the UI currently shows, so slower sources cannot move it backwards.
  const shown = useRef<{ progress: number; ts: number; sse: boolean }>({ progress: 0, ts: 0, sse: false });

  /**
   * Single entry point for progress. Live events win by timestamp; the database
   * snapshot (older, coarser) only applies when it is ahead of what is shown, or
   * when no live event has arrived yet.
   */
  const ingest = useCallback((event: ProgressEvent, source: ProgressSource) => {
    const terminal = TERMINAL.includes(event.status ?? "");
    const state = shown.current;
    // Only Redis-published events carry a timestamp. Database snapshots (polling,
    // or the one the stream sends on every reconnect) have none.
    if (source === "sse" && typeof event.ts === "number") {
      if (event.ts < state.ts) return;
      state.ts = event.ts;
      state.sse = true;
    } else if (!terminal && state.sse && !((event.progress ?? 0) > state.progress)) {
      return;
    }
    if (typeof event.progress === "number") state.progress = event.progress;
    callbacks.current.onProgress(event, source);
  }, []);

  useEffect(() => {
    shown.current = { progress: 0, ts: 0, sse: false };
    if (!active || !taskId) {
      setReconnecting(false);
      return;
    }
    let disposed = false;
    let refreshing = false;
    const reconcile = async () => {
      if (disposed || refreshing) return;
      refreshing = true;
      try { await callbacks.current.refresh(); }
      catch { /* Keep retrying; a transport failure is not a failed generation. */ }
      finally { refreshing = false; }
    };
    const source = new EventSource(`/api/tasks/${taskId}/progress`);
    source.onopen = () => setReconnecting(false);
    source.onerror = () => {
      setReconnecting(true);
      void reconcile();
      // Leave the source open so the browser reconnects with its retry policy.
    };
    const read = (event: Event) => {
      try {
        const data = JSON.parse((event as MessageEvent<string>).data);
        return data && typeof data === "object" ? data : null;
      } catch { void reconcile(); return null; }
    };
    const progress = (event: Event) => {
      const data = read(event);
      if (!data) return;
      ingest(data, "sse");
      if (TERMINAL.includes(data.status)) void reconcile();
    };
    source.addEventListener("status", progress);
    source.addEventListener("progress", progress);
    source.addEventListener("clip_ready", (event) => {
      const data = read(event);
      if (data?.clip) callbacks.current.onClip(data.clip as T);
    });
    source.addEventListener("close", () => {
      source.close();
      void reconcile();
    });
    const timer = window.setInterval(() => { void reconcile(); }, 5000);
    const onVisible = () => { if (document.visibilityState === "visible") void reconcile(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      disposed = true;
      source.close();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [taskId, active, ingest]);
  return { reconnecting, ingest };
}
