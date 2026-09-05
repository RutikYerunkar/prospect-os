import type {
  GmailConnectionResponse,
  GmailConnectResponse,
  OperatorLoginRequest,
  PlayCreateRequest,
  PlayPreviewRequest,
  PlayPreviewResponse,
  PlayResponse,
  ProspectAggregate,
  ProspectSummary,
  ProviderSettingsResponse,
  RunCreateRequest,
  RunCreateResponse,
  RunEvaluation,
  RunResponse,
} from "@/lib/types";

// Checkpoint I2: same-origin only. The browser never talks to the API's own
// host directly — every request goes through this app's own
// `app/api/[...path]/route.ts` proxy at a relative `/api/...` path, which
// forwards it server-to-server to `GROUNDWORK_API_ORIGIN` (a server-only env
// var, never NEXT_PUBLIC_-prefixed). See that file for why: the frontend and
// API are separate *.onrender.com origins, and the operator session cookie
// is host-only — same-origin is what makes that cookie work as intended.
const API_BASE_URL = "";

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * The API responded, just not with success — as opposed to `NetworkError`
 * below (the API process wasn't reachable at all). Checkpoint I1 Phase 9:
 * callers need to tell these apart to show the right message ("start the
 * API" vs "that request failed"), not one generic "something went wrong."
 */
export class NetworkError extends Error {
  constructor(message = "Could not reach the API — is it running?") {
    super(message);
  }
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

async function parseErrorDetail(response: Response): Promise<string | undefined> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.title === "string") return body.title;
  } catch {
    // response body wasn't JSON — fall through to the generic message
  }
  return undefined;
}

async function doFetch(path: string, init: RequestInit): Promise<Response> {
  try {
    // credentials:"include" — the operator session cookie is what every
    // Live-gated read/write actually authenticates with (Checkpoint I1
    // Phase 8). The request itself is same-origin as of Checkpoint I2 (see
    // API_BASE_URL above), so the browser would send the cookie by default
    // regardless — "include" is kept because it's still correct and makes
    // no behavioral difference for a same-origin request.
    return await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: "include" });
  } catch (err) {
    if (isAbortError(err)) throw err; // a superseded request, not a failure
    throw new NetworkError();
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await doFetch(path, {});
  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new ApiError(response.status, `GET ${path} failed: ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown, options?: { signal?: AbortSignal }): Promise<T> {
  const response = await doFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options?.signal,
  });
  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new ApiError(response.status, `POST ${path} failed: ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const response = await doFetch(path, { method: "DELETE" });
  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new ApiError(response.status, `DELETE ${path} failed: ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

export function eventStreamUrl(runId: string, afterSeq: number): string {
  // Relative and same-origin (Checkpoint I2) — the proxy at
  // app/api/[...path]/route.ts streams this through to the API without
  // buffering, so resumable SSE behaves identically to a direct connection.
  return `/api/runs/${runId}/events?after_seq=${afterSeq}`;
}

export interface HealthResponse {
  status: string;
  mode: "demo" | "live";
  version: string;
}

// --- typed endpoint wrappers (§21 API Contract) ---

export function createPlay(body: PlayCreateRequest): Promise<PlayResponse> {
  return apiPost<PlayResponse>("/api/plays", body);
}

// Checkpoint I1 Phase 7 — non-persisting, deterministic, never an LLM call.
// `signal` lets a caller (the New Play form's debounce) cancel a
// superseded request via AbortController rather than let a slow, stale
// response overwrite a newer one.
export function previewPlay(body: PlayPreviewRequest, signal?: AbortSignal): Promise<PlayPreviewResponse> {
  return apiPost<PlayPreviewResponse>("/api/plays/preview", body, { signal });
}

export function startRun(playId: string, body: RunCreateRequest = {}): Promise<RunCreateResponse> {
  return apiPost<RunCreateResponse>(`/api/plays/${playId}/runs`, body);
}

export function getRun(runId: string): Promise<RunResponse> {
  return apiGet<RunResponse>(`/api/runs/${runId}`);
}

export function listRunProspects(runId: string): Promise<ProspectSummary[]> {
  return apiGet<ProspectSummary[]>(`/api/runs/${runId}/prospects`);
}

export function getRunEvaluation(runId: string): Promise<RunEvaluation> {
  return apiGet<RunEvaluation>(`/api/runs/${runId}/evaluation`);
}

export function getProspect(prospectId: string): Promise<ProspectAggregate> {
  return apiGet<ProspectAggregate>(`/api/prospects/${prospectId}`);
}

export function approveProspect(prospectId: string, actor = "demo_user"): Promise<ProspectAggregate> {
  return apiPost<ProspectAggregate>(`/api/prospects/${prospectId}/approve`, { actor });
}

export function rejectProspect(
  prospectId: string,
  reason: string,
  actor = "demo_user",
): Promise<ProspectAggregate> {
  return apiPost<ProspectAggregate>(`/api/prospects/${prospectId}/reject`, { reason, actor });
}

export function getProviderSettings(): Promise<ProviderSettingsResponse> {
  return apiGet<ProviderSettingsResponse>("/api/settings/providers");
}

// Checkpoint I1 Phase 8 — operator session (Live unlock).
export function loginOperator(body: OperatorLoginRequest): Promise<{ status: string }> {
  return apiPost<{ status: string }>("/api/operator/session", body);
}

export function logoutOperator(): Promise<{ status: string }> {
  return apiDelete<{ status: string }>("/api/operator/session");
}

// V2-G — Gmail OAuth (connection only, no sending). All three routes are
// operator-gated server-side; the frontend never decides that on its own.
export function getGmailConnection(): Promise<GmailConnectionResponse> {
  return apiGet<GmailConnectionResponse>("/api/gmail/connection");
}

export function connectGmail(): Promise<GmailConnectResponse> {
  return apiPost<GmailConnectResponse>("/api/gmail/connect", {});
}

export function disconnectGmail(): Promise<{ status: string; deleted: boolean }> {
  return apiDelete<{ status: string; deleted: boolean }>("/api/gmail/connection");
}
