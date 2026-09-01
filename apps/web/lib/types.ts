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
  mode?: Mode;
  target_count?: number;
  use_live_objective_parser?: boolean;
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
  parse_source: "llm" | "deterministic";
  runs: RunSummary[];
}

// --- runs ---

export interface RunCreateRequest {
  mode?: Mode;
  seed?: number;
}

export interface ProviderProfile {
  mode: Mode;
  llm_provider: string;
  model: string;
  reasoning_effort: string | null;
  prompt_versions: Record<string, string>;
  search_provider: string;
  synthetic_search: boolean;
  evidence_origin: string;
  // H2: present only on new Live runs, absent on Demo and on historical
  // Checkpoint G rows (`LIVE LLM · FIXTURE SEARCH`) — never assume present.
  query_plan_version?: string;
  search_hard_bounds?: Record<string, number>;
  search_usage_capable?: boolean;
  search_pricing_configured?: boolean;
  llm_max_output_tokens: number | null;
  llm_max_transport_retries: number | null;
  llm_max_schema_retries: number | null;
  live_max_prospects_per_run: number | null;
  soft_budget_usd: number | null;
  soft_budget_enforceable: boolean;
  pricing_configured: boolean;
  deterministic: boolean;
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
  // Empty object `{}` until the run's mode is known to populate it fully —
  // never assume every field is present.
  provider_profile: Partial<ProviderProfile>;
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

// --- prospect aggregate (GET /api/prospects/{id}) ---

export type EvidenceOrigin = "DEMO_FIXTURE" | "LIVE_FETCH" | "LLM_INFERENCE";

export interface EvidenceItem {
  id: string;
  source_url: string | null;
  source_ref: string | null;
  source_provider: string;
  title: string;
  claim: string;
  snippet: string;
  signal_type: SignalType | null;
  retrieved_at: string | null;
  confidence: number;
  origin: EvidenceOrigin;
}

export type SignalType = "FUNDING" | "HIRING" | "TECH" | "LEADERSHIP" | "PRODUCT";

export interface SignalItem {
  id: string;
  type: SignalType;
  summary: string;
  occurred_at: string | null;
  confidence: number;
  evidence_ids: string[];
}

export interface DimensionScore {
  name: string;
  raw: number;
  weight: number;
  contribution: number;
  evidence_ids: string[];
  unsupported: boolean;
}

export interface ScoreModifier {
  name: string;
  reason: string;
  detail: string;
}

export interface ProspectScore {
  overall: number;
  dimensions: DimensionScore[];
  modifiers: ScoreModifier[];
  disqualified: boolean;
  explanation: string;
  confidence: number;
  rubric_version: string;
  computed_at: string;
}

export interface ProspectContact {
  full_name: string | null;
  title: string | null;
  persona: string | null;
  linkedin_url: string | null;
  email: string | null;
  verification: ContactVerification;
  evidence_ids: string[];
}

export interface ClaimMapEntry {
  sentence: string;
  evidence_ids: string[];
}

export interface OutreachDraft {
  id: string;
  channel: string;
  step_index: number;
  subject: string | null;
  body: string;
  claim_map: ClaimMapEntry[];
  version: number;
  status: string;
}

export type ReviewSeverity = "hard" | "soft";

export interface ReviewCheck {
  id: string;
  passed: boolean;
  severity: ReviewSeverity;
  detail: string;
  evidence_refs: string[];
}

export type ReviewVerdict = "PASS" | "NEEDS_REVIEW" | "FAIL";

export interface ReviewResult {
  verdict: ReviewVerdict;
  checks: ReviewCheck[];
  reasons: string[];
  reviewed_at: string;
}

export interface AgentTaskTrace {
  id: string;
  step_name: string;
  attempt: number;
  status: string;
  started_at: string;
  duration_ms: number | null;
  model: string | null;
  provider: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  error_type: string | null;
  error_message: string | null;
  evidence_count: number | null;
}

export type ApprovalState = "PENDING" | "APPROVED" | "REJECTED";

export interface ApprovalInfo {
  state: ApprovalState;
  actor: string | null;
  reason: string | null;
  decided_at: string | null;
}

export interface ProspectCompany {
  id?: string;
  canonical_domain?: string;
  display_name?: string;
  industry?: string;
  size_band?: string;
  employee_count?: number;
  hq_country?: string;
  description?: string;
  [key: string]: unknown;
}

export interface ProspectAggregate {
  id: string;
  run_id: string;
  company: ProspectCompany;
  dedupe_key: string;
  duplicate_of: string | null;
  stage: ProspectStage;
  status: ProspectStatus;
  error: string | null;
  evidence: EvidenceItem[];
  signals: SignalItem[];
  score: ProspectScore | null;
  contact: ProspectContact | null;
  drafts: OutreachDraft[];
  review: ReviewResult | null;
  trace: AgentTaskTrace[];
  approval: ApprovalInfo;
}

// --- evaluation (GET /api/runs/{id}/evaluation) ---

export interface VolumeMetrics {
  discovered: number;
  duplicated: number;
  researched: number;
  qualified: number;
  needs_review: number;
  rejected: number;
  failed: number;
  by_status: Record<string, number>;
}

export interface QualityMetrics {
  evidence_coverage: number | null;
  grounded_claim_rate: number | null;
  dimension_support_rate: number | null;
  unsupported_claim_count: number;
  contact_verification_breakdown: Record<string, number>;
  mean_icp_score: number | null;
  mean_confidence: number | null;
  provenance_mix: Record<string, number>;
}

export interface ReliabilityMetrics {
  step_status_counts: Record<string, number>;
  total_retries: number;
  p50_step_duration_ms: number | null;
  p95_step_duration_ms: number | null;
  run_wall_clock_ms: number | null;
  provider_error_counts: Record<string, number>;
  per_step_success_rate: Record<string, number>;
}

export interface GuardrailMetric {
  id: string;
  passed: number;
  total: number;
  pass_rate: number;
  failed_prospect_ids: string[];
}

export interface LLMUsageByOperation {
  attempts: number;
  tokens_in: number;
  tokens_out: number;
}

export interface LLMUsage {
  logical_calls: number;
  provider_attempts: number;
  tokens_in: number;
  tokens_out: number;
  tokens_total: number;
  reasoning_tokens: number | null;
  estimated_cost_usd: number | null;
  by_operation: Record<string, LLMUsageByOperation>;
  by_status: Record<string, number>;
  transport_retries: number;
  schema_repairs: number;
  budget_tripped: boolean;
}

export interface SearchQualityMetrics {
  result_occurrences: number;
  sources_retrieved_unique: number;
  sources_used_as_evidence: number;
  source_utilization_rate: number | null;
  duplicate_retrieval_rate: number | null;
  industry_grounded_coverage: number | null;
  employee_count_grounded_coverage: number | null;
  unevaluable_exclusion_count: number;
  search_calls: number;
  search_retries: number;
  search_error_counts: Record<string, number>;
  p50_search_latency_ms: number | null;
  p95_search_latency_ms: number | null;
  search_cost_usd: number | null;
  search_credits_used: number | null;
  extraction_calls: number;
  partial_extractions: number;
  failed_or_partial_sources: number;
  discovery_rejection_reasons: Record<string, number>;
  domain_resolution_method_counts: Record<string, number>;
}

export interface RunEvaluation {
  run_id: string;
  volume: VolumeMetrics;
  quality: QualityMetrics;
  reliability: ReliabilityMetrics;
  guardrails: GuardrailMetric[];
  llm_usage: LLMUsage;
  search_quality: SearchQualityMetrics;
}

// --- settings ---

export interface ProviderInfo {
  name: string;
  configured: boolean;
}

export interface LiveAvailability {
  available: boolean;
  llm_available: boolean;
  search_available: boolean;
  model: string;
  reasoning_effort: string | null;
  prompt_versions: Record<string, string>;
  search_provider: string;
  synthetic_search: boolean;
  query_plan_version: string;
  live_max_prospects_per_run: number;
  llm_max_output_tokens: number;
  llm_max_transport_retries: number;
  llm_max_schema_retries: number;
  llm_call_deadline_s: number;
  live_step_timeout_s: number;
  search_hard_bounds: Record<string, number>;
  search_usage_capable: boolean;
  search_pricing_configured: boolean;
  pricing_configured: boolean;
  soft_budget_usd: number | null;
  soft_budget_enforceable: boolean;
}

export interface ProviderSettingsResponse {
  mode: Mode;
  llm: ProviderInfo;
  search: ProviderInfo;
  live: LiveAvailability;
}
