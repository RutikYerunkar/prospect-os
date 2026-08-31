/**
 * Hand-mirrored wire types for `groundwork/api/schemas.py` (Checkpoint C).
 * No codegen — keep these in sync by hand if the API DTOs change.
 */

export type Mode = "demo" | "live";

export type RunStatus = "RUNNING" | "COMPLETED" | "PARTIAL" | "INTERRUPTED";

export type ProspectStage =
  | "DISCOVERED"
  | "RESEARCH"
  | "SIGNALS"
  | "ENRICH"
  | "SCORE"
  | "CONTACT"
  | "PERSONALIZE"
  | "REVIEW"
  | "DONE";

export type ProspectStatus =
  | "PENDING"
  | "RUNNING"
  | "PASS"
  | "NEEDS_REVIEW"
  | "REJECTED"
  | "DUPLICATE"
  | "FAILED"
  | "TIMED_OUT";

export type ContactVerification = "VERIFIED" | "PERSONA_ONLY" | "UNAVAILABLE";

export const PIPELINE_STAGES: ProspectStage[] = [
  "RESEARCH",
  "SIGNALS",
  "ENRICH",
  "SCORE",
  "CONTACT",
  "PERSONALIZE",
  "REVIEW",
];

// --- plays ---

export interface PlaySpec {
  objective_text: string;
  target_industries: string[];
  excluded_industries: string[];
  adjacent_industries: Record<string, string[]>;
  size_band_min: number;
  size_band_max: number;
  target_funding_stages: string[];
  target_technologies: string[];
  persona_titles: string[];
  min_score: number;
  min_confidence: number;
  target_count: number;
}

export interface PlayCreateRequest {
  objective: string;
  icp_overrides?: Record<string, unknown>;
  mode?: "demo";
  target_count?: number;
}

export interface RunSummary {
  id: string;
  status: RunStatus;
  mode: Mode;
  seed: number;
  started_at: string;
  finished_at: string | null;
  counters: Record<string, number>;
}

export interface PlayResponse {
  id: string;
  name: string;
  objective_text: string;
  icp_spec: PlaySpec;
  mode: Mode;
  created_at: string;
  runs: RunSummary[];
}

// --- runs ---

export interface RunCreateRequest {
  mode?: "demo";
  seed?: number;
}

export interface RunCreateResponse {
  run_id: string;
  status: string;
}

export interface RunResponse {
  id: string;
  play_id: string;
  status: RunStatus;
  mode: Mode;
  seed: number;
  plan: unknown[];
  counters: Record<string, number>;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
}

// --- prospects ---

export interface ProspectSummary {
  id: string;
  run_id: string;
  company_name: string;
  company_domain: string;
  stage: ProspectStage;
  status: ProspectStatus;
  top_signal: string | null;
  contact_verification: ContactVerification | null;
  contact_name: string | null;
  icp_score: number | null;
  confidence: number | null;
  had_retry: boolean;
  approval_state: string;
  error: string | null;
}

// --- run events (SSE) ---

export type RunEventType =
  | "run.started"
  | "run.completed"
  | "run.failed"
  | "prospect.discovered"
  | "prospect.stage_changed"
  | "prospect.scored"
  | "prospect.reviewed"
  | "prospect.completed"
  | "step.started"
  | "step.completed"
  | "step.retrying";

export interface RunEvent {
  seq: number;
  run_id: string;
  type: RunEventType | string;
  ts: string;
  prospect_id: string | null;
  payload: Record<string, unknown>;
}

// --- settings ---

export interface ProviderInfo {
  name: string;
  configured: boolean;
}

export interface ProviderSettingsResponse {
  mode: Mode;
  llm: ProviderInfo;
  search: ProviderInfo;
}
