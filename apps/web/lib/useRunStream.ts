"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { ApiError, NetworkError, eventStreamUrl, getRun, listRunProspects } from "@/lib/api";
import type { ProspectSummary, RunEvent, RunResponse, RunStatus } from "@/lib/types";

const TERMINAL_STATUSES = new Set<RunStatus>(["COMPLETED", "PARTIAL", "INTERRUPTED"]);

const KNOWN_EVENT_TYPES = [
  "run.started",
  "run.completed",
  "run.failed",
  "prospect.discovered",
  "prospect.stage_changed",
  "prospect.scored",
  "prospect.reviewed",
  "prospect.completed",
  "step.started",
  "step.completed",
  "step.retrying",
] as const;

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed" | "error" | "degraded";

// Checkpoint I1 Phase 9: bounded reconnect attempts — past this many
// consecutive failures the stream gives up and surfaces a "degraded" state
// (the UI falls back to periodic REST polling, see app/runs/[id]/page.tsx)
// rather than retrying forever against a genuinely down API.
const MAX_RECONNECT_ATTEMPTS = 8;

export interface RetryInfo {
  step: string;
  attempt: number;
  errorType: string;
}

interface StreamState {
  run: RunResponse | null;
  prospects: ProspectSummary[] | null;
  events: RunEvent[];
  retrying: Record<string, RetryInfo>;
  connection: ConnectionState;
  loadError: string | null;
  // Checkpoint I1 Phase 9: lets the page render "start the API" instead of
  // "run not found" — a reachability failure and a real 404 need different
  // copy and a different retry affordance.
  loadErrorUnreachable: boolean;
}

type Action =
  | { type: "hydrated"; run: RunResponse; prospects: ProspectSummary[] }
  | { type: "hydrate_failed"; message: string; unreachable: boolean }
  | { type: "connection"; state: ConnectionState }
  | { type: "event"; event: RunEvent }
  | { type: "run_refreshed"; run: RunResponse }
  | { type: "prospects_refreshed"; prospects: ProspectSummary[] };

const initialState: StreamState = {
  run: null,
  prospects: null,
  events: [],
  retrying: {},
  connection: "connecting",
  loadError: null,
  loadErrorUnreachable: false,
};

const MAX_EVENTS = 300;

function upsertDiscovered(
  prospects: ProspectSummary[],
  runId: string,
  prospectId: string,
  companyName: string,
): ProspectSummary[] {
  if (prospects.some((p) => p.id === prospectId)) return prospects;
  const placeholder: ProspectSummary = {
    id: prospectId,
    run_id: runId,
    company_name: companyName,
    company_domain: "",
    stage: "DISCOVERED",
    status: "RUNNING",
    top_signal: null,
    contact_verification: null,
    contact_name: null,
    icp_score: null,
    confidence: null,
    had_retry: false,
    approval_state: "PENDING",
    error: null,
  };
  return [...prospects, placeholder];
}

function reducer(state: StreamState, action: Action): StreamState {
  switch (action.type) {
    case "hydrated":
      return { ...state, run: action.run, prospects: action.prospects, loadError: null, loadErrorUnreachable: false };
    case "hydrate_failed":
      return { ...state, loadError: action.message, loadErrorUnreachable: action.unreachable };
    case "connection":
      return { ...state, connection: action.state };
    case "run_refreshed":
      return { ...state, run: action.run };
    case "prospects_refreshed":
      return { ...state, prospects: action.prospects };
    case "event": {
      const { event } = action;
      const events = [...state.events, event].slice(-MAX_EVENTS);
      let prospects = state.prospects ?? [];
      let retrying = state.retrying;

      switch (event.type) {
        case "prospect.discovered": {
          const company = String(event.payload.company ?? "unknown");
          prospects = upsertDiscovered(prospects, event.run_id, event.prospect_id ?? "", company);
          break;
        }
        case "prospect.stage_changed": {
          const stage = String(event.payload.stage ?? "");
          prospects = prospects.map((p) =>
            p.id === event.prospect_id ? { ...p, stage: stage as ProspectSummary["stage"] } : p,
          );
          break;
        }
        case "step.retrying": {
          if (event.prospect_id) {
            retrying = {
              ...retrying,
              [event.prospect_id]: {
                step: String(event.payload.step ?? ""),
                attempt: Number(event.payload.attempt ?? 0),
                errorType: String(event.payload.error_type ?? ""),
              },
            };
          }
          prospects = prospects.map((p) => (p.id === event.prospect_id ? { ...p, had_retry: true } : p));
          break;
        }
        case "step.completed": {
          if (event.prospect_id && retrying[event.prospect_id]) {
            const next = { ...retrying };
            delete next[event.prospect_id];
            retrying = next;
          }
          break;
        }
        case "prospect.completed": {
          const status = String(event.payload.status ?? "");
          const error = event.payload.error != null ? String(event.payload.error) : null;
          prospects = prospects.map((p) =>
            p.id === event.prospect_id
              ? { ...p, status: status as ProspectSummary["status"], stage: "DONE", error }
              : p,
          );
          if (event.prospect_id && retrying[event.prospect_id]) {
            const next = { ...retrying };
            delete next[event.prospect_id];
            retrying = next;
          }
          break;
        }
        default:
          break;
      }

      return { ...state, events, prospects, retrying };
    }
    default:
      return state;
  }
}

/**
 * Consumes GET /api/runs/{id}/events (§19), applying lightweight reducer
 * updates for immediate feedback and falling back to authoritative REST
 * reads (`GET /runs/{id}` + `GET /runs/{id}/prospects`) on hydrate, on every
 * reconnect, and whenever an event signals a field the SSE payload doesn't
 * fully carry (score/contact/review only land via the aggregate reads).
 * `lastSeq` is tracked in a ref and never rewound — a manual reconnect loop
 * (not the browser's built-in EventSource retry, which would replay from
 * the original `after_seq`) is what makes resuming from the true cursor
 * possible after a drop.
 */
export function useRunStream(runId: string) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [retryNonce, setRetryNonce] = useState(0);
  const lastSeqRef = useRef(0);
  const runStatusRef = useRef<RunStatus | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const refetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;
    lastSeqRef.current = 0;
    runStatusRef.current = null;
    let reconnectAttempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    async function refreshRun() {
      try {
        const run = await getRun(runId);
        runStatusRef.current = run.status;
        if (!unmountedRef.current) dispatch({ type: "run_refreshed", run });
      } catch {
        // transient — the next successful poll/event will correct this
      }
    }

    async function refreshProspects() {
      try {
        const prospects = await listRunProspects(runId);
        if (!unmountedRef.current) dispatch({ type: "prospects_refreshed", prospects });
      } catch {
        // transient — the next successful poll/event will correct this
      }
    }

    function scheduleReconcile() {
      if (refetchTimerRef.current) clearTimeout(refetchTimerRef.current);
      refetchTimerRef.current = setTimeout(() => {
        void refreshProspects();
      }, 150);
    }

    function connect(isReconnect: boolean) {
      if (unmountedRef.current) return;
      dispatch({ type: "connection", state: isReconnect ? "reconnecting" : "connecting" });

      // `withCredentials` — the operator session cookie has to ride along
      // on a Live run's SSE connection the same way it does on every REST
      // call (Checkpoint I1 Phase 8/9); without it a cross-origin
      // EventSource never sends cookies at all.
      const es = new EventSource(eventStreamUrl(runId, lastSeqRef.current), { withCredentials: true });
      esRef.current = es;

      es.onopen = () => {
        reconnectAttempt = 0;
        dispatch({ type: "connection", state: "live" });
        if (isReconnect) {
          void refreshRun();
          void refreshProspects();
        }
      };

      for (const type of KNOWN_EVENT_TYPES) {
        es.addEventListener(type, (raw: MessageEvent) => {
          let event: RunEvent;
          try {
            event = JSON.parse(raw.data) as RunEvent;
          } catch {
            return;
          }
          if (event.seq <= lastSeqRef.current) return; // already applied
          lastSeqRef.current = event.seq;

          // Set synchronously from the event itself — the server closes the
          // stream right after this frame, and the async GET /runs/{id}
          // refetch below could still be in flight when onerror fires.
          // Without this, a clean end-of-stream close would be mistaken for
          // a drop and trigger a pointless reconnect.
          if (type === "run.completed") {
            runStatusRef.current = String(event.payload.status ?? "COMPLETED") as RunStatus;
          } else if (type === "run.failed") {
            runStatusRef.current = "PARTIAL";
          }

          dispatch({ type: "event", event });

          if (
            type === "prospect.scored" ||
            type === "prospect.reviewed" ||
            type === "prospect.completed"
          ) {
            scheduleReconcile();
          }
          if (type === "run.started" || type === "run.completed" || type === "run.failed") {
            void refreshRun();
            if (type === "run.completed" || type === "run.failed") scheduleReconcile();
          }
        });
      }

      es.onerror = () => {
        es.close();
        if (esRef.current === es) esRef.current = null;

        if (runStatusRef.current && TERMINAL_STATUSES.has(runStatusRef.current)) {
          dispatch({ type: "connection", state: "closed" });
          return;
        }

        reconnectAttempt += 1;
        if (reconnectAttempt > MAX_RECONNECT_ATTEMPTS) {
          // Give up retrying the stream itself — fall back to periodic
          // REST polling rather than hammering a genuinely unreachable API
          // forever.
          dispatch({ type: "connection", state: "degraded" });
          startDegradedPolling();
          return;
        }

        dispatch({ type: "connection", state: "reconnecting" });
        // Exponential backoff with jitter (±20%) — the jitter avoids every
        // open tab reconnecting in lockstep after a shared outage (a
        // thundering-herd reconnect wave, not a concern at this project's
        // scale but cheap to avoid regardless).
        const base = Math.min(1000 * 2 ** (reconnectAttempt - 1), 8000);
        const jitter = base * (0.8 + Math.random() * 0.4);
        reconnectTimer = setTimeout(() => connect(true), jitter);
      };
    }

    // Checkpoint I1 Phase 9 — degraded-state fallback: once the SSE stream
    // gives up reconnecting, the run isn't necessarily unreachable, just
    // the long-lived connection is (a proxy/LB dropping idle connections,
    // for instance) — poll the two REST reads on a fixed interval instead
    // of leaving the board frozen.
    let degradedPollTimer: ReturnType<typeof setInterval> | null = null;
    function startDegradedPolling() {
      if (degradedPollTimer) return;
      degradedPollTimer = setInterval(() => {
        if (runStatusRef.current && TERMINAL_STATUSES.has(runStatusRef.current)) {
          if (degradedPollTimer) clearInterval(degradedPollTimer);
          degradedPollTimer = null;
          return;
        }
        void refreshRun();
        void refreshProspects();
      }, 5000);
    }

    async function bootstrap() {
      try {
        const [run, prospects] = await Promise.all([getRun(runId), listRunProspects(runId)]);
        if (unmountedRef.current) return;
        runStatusRef.current = run.status;
        dispatch({ type: "hydrated", run, prospects });
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Could not load run";
        const unreachable = err instanceof NetworkError;
        if (!unmountedRef.current) dispatch({ type: "hydrate_failed", message, unreachable });
        return;
      }
      connect(false);
    }

    void bootstrap();

    return () => {
      unmountedRef.current = true;
      esRef.current?.close();
      esRef.current = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (refetchTimerRef.current) clearTimeout(refetchTimerRef.current);
      if (degradedPollTimer) clearInterval(degradedPollTimer);
    };
  }, [runId, retryNonce]);

  return { ...state, retry: () => setRetryNonce((n) => n + 1) };
}
