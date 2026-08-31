import type {
  PlayCreateRequest,
  PlayResponse,
  ProspectAggregate,
  ProspectSummary,
  RunCreateRequest,
  RunCreateResponse,
  RunResponse,
} from "@/lib/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
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

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new ApiError(response.status, `GET ${path} failed: ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new ApiError(response.status, `POST ${path} failed: ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

export function eventStreamUrl(runId: string, afterSeq: number): string {
  return `${API_BASE_URL}/api/runs/${runId}/events?after_seq=${afterSeq}`;
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

export function startRun(playId: string, body: RunCreateRequest = {}): Promise<RunCreateResponse> {
  return apiPost<RunCreateResponse>(`/api/plays/${playId}/runs`, body);
}

export function getRun(runId: string): Promise<RunResponse> {
  return apiGet<RunResponse>(`/api/runs/${runId}`);
}

export function listRunProspects(runId: string): Promise<ProspectSummary[]> {
  return apiGet<ProspectSummary[]>(`/api/runs/${runId}/prospects`);
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
