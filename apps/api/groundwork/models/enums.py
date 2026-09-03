from enum import StrEnum


class Mode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class EvidenceOrigin(StrEnum):
    DEMO_FIXTURE = "DEMO_FIXTURE"
    LIVE_FETCH = "LIVE_FETCH"
    LLM_INFERENCE = "LLM_INFERENCE"


class SignalType(StrEnum):
    FUNDING = "FUNDING"
    HIRING = "HIRING"
    TECH = "TECH"
    LEADERSHIP = "LEADERSHIP"
    PRODUCT = "PRODUCT"


class ContactVerification(StrEnum):
    VERIFIED = "VERIFIED"
    PERSONA_ONLY = "PERSONA_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


class ProspectStage(StrEnum):
    DISCOVERED = "DISCOVERED"
    RESEARCH = "RESEARCH"
    SIGNALS = "SIGNALS"
    ENRICH = "ENRICH"
    SCORE = "SCORE"
    CONTACT = "CONTACT"
    PERSONALIZE = "PERSONALIZE"
    REVIEW = "REVIEW"
    DONE = "DONE"


class ProspectStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class ReviewVerdict(StrEnum):
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAIL = "FAIL"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    INTERRUPTED = "INTERRUPTED"


class StepStatus(StrEnum):
    OK = "OK"
    RETRY = "RETRY"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    SKIPPED = "SKIPPED"


class CheckSeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class SourceStatus(StrEnum):
    """One retrieval occurrence's outcome (H1 Phase 9) — distinct from
    `Evidence`/provider-call status; this describes whether *this specific
    occurrence* yielded usable text."""

    OK = "ok"
    FAILED = "failed"
    PARTIAL = "partial"


class ExclusionEvaluation(StrEnum):
    """Tri-state exclusion-policy evaluation (H1 Phase 7) — distinct from
    the boolean `ICPScore.disqualified`, which only means EXCLUDED. A
    company whose industry was never grounded is neither excluded nor
    clearly not-excluded: policy simply couldn't be evaluated, and that
    must never silently pass."""

    EXCLUDED = "EXCLUDED"
    NOT_EXCLUDED = "NOT_EXCLUDED"
    UNKNOWN = "UNKNOWN"


class DimensionSupport(StrEnum):
    """Tri-state per-dimension scoring support (H1 Phase 7), replacing the
    old boolean `unsupported` flag. UNSUPPORTED still contributes 0 and
    counts in the confidence denominator (a claim was checked for and not
    found); UNKNOWN also contributes 0 but is *excluded* from the
    denominator entirely (the fact was never independently established, so
    it should neither help nor hurt confidence)."""

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


# =====================================================================
# v2 — Contact Enrichment & Governed Outbound Action
# (docs/V2_IMPLEMENTATION_PLAN.md, frozen Rev 4, Part 3 / Part 4)
# =====================================================================


class Channel(StrEnum):
    """Outreach channel. Mirrors `OutreachDraft.channel`'s existing string
    convention (`"email"`) — this enum gives v2 code (content hashing,
    action policy) a closed, typed vocabulary without changing the v1
    column's string values."""

    EMAIL = "email"
    LINKEDIN = "linkedin"


class EmailDiscoveryState(StrEnum):
    """§3.1 — one of the five independent contact axes. Never collapsed
    with `EmailVerificationState` or `ContactVerification` (the
    person-identity axis, v1, unchanged)."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"  # enrichment disabled, or no named person to look up
    NOT_FOUND = "NOT_FOUND"  # a provider call SUCCEEDED and returned no address
    FOUND = "FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"  # no successful observation has ever been obtained (§3.6)


class EmailVerificationState(StrEnum):
    """§3.1. `VERIFIED` is the ONLY sendable state — no override anywhere
    in v2 (D7)."""

    UNVERIFIED = "UNVERIFIED"  # no signal; also the fail-closed default for unmapped statuses
    UNVERIFIABLE = "UNVERIFIABLE"
    RISKY = "RISKY"  # catch-all domain / low provider confidence
    VERIFIED = "VERIFIED"  # the ONLY sendable state
    INVALID = "INVALID"


class LinkedInResolutionState(StrEnum):
    """§3.1."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    NOT_FOUND = "NOT_FOUND"
    RESOLVED = "RESOLVED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class LinkedInIdentityState(StrEnum):
    """§3.1 / §3.7 Step 4. Only `STRONG_MATCH` is actionable."""

    UNKNOWN = "UNKNOWN"
    MISMATCH = "MISMATCH"
    WEAK_MATCH = "WEAK_MATCH"
    STRONG_MATCH = "STRONG_MATCH"  # only STRONG is actionable


class EnrichmentOrigin(StrEnum):
    """§3.7 Step 0 / Part 4 — selects which identifier grammar applies to an
    observation. A structural fact about which provider produced the row,
    never an inference and never an LLM judgement."""

    DEMO_FIXTURE = "DEMO_FIXTURE"
    LIVE_PROVIDER = "LIVE_PROVIDER"


class EnrichmentOperation(StrEnum):
    """Part 4 — `enrichment_calls.operation`."""

    PERSON_ENRICHMENT = "person_enrichment"
    EMAIL_VERIFICATION = "email_verification"  # slot for a dedicated verifier; unused in v2


class EnrichmentAttemptStatus(StrEnum):
    """Part 4 — mirrors `SearchAttemptTelemetry`'s status vocabulary."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    NOT_ATTEMPTED_BUDGET = "NOT_ATTEMPTED_BUDGET"


class ActionType(StrEnum):
    """Part 4 (D6) — there is deliberately no `LINKEDIN_SEND` member.
    Nothing can invoke what does not exist."""

    EMAIL_SEND = "EMAIL_SEND"
    LINKEDIN_COPY_AND_OPEN = "LINKEDIN_COPY_AND_OPEN"


class ActionExecutionOrigin(StrEnum):
    """Part 4 (D14) — decides which safety rules bind. `LIVE_EXTERNAL` means
    execution on the live external-action path, capable of a real external
    side effect — it is NOT itself proof that a message left the system or
    was delivered; that is represented separately by execution
    status/outcome (`ActionExecutionStatus` below)."""

    DEMO_SIMULATED = "DEMO_SIMULATED"
    LIVE_EXTERNAL = "LIVE_EXTERNAL"


class ActionExecutionStatus(StrEnum):
    """§3.2 — the execution state machine. Terminal: `SUCCEEDED`, `FAILED`,
    `ABANDONED`. A process dying between `CLAIMED` and a settled state is
    recovered by a stale-claim sweep to `UNCERTAIN` — never to `FAILED`,
    never re-dispatched."""

    CLAIMED = "CLAIMED"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    ABANDONED = "ABANDONED"


class SendOutcome(StrEnum):
    """Part 4 — classification of one send-provider attempt (§3.4).
    `ACCEPTANCE_UNKNOWN` is the DEFAULT for anything not positively
    classified."""

    ACCEPTED = "ACCEPTED"
    PROVEN_NOT_DISPATCHED = "PROVEN_NOT_DISPATCHED"
    DEFINITIVE_REJECTION = "DEFINITIVE_REJECTION"
    ACCEPTANCE_UNKNOWN = "ACCEPTANCE_UNKNOWN"


class ReconcileStatus(StrEnum):
    """Part 4 / §3.3 — `NOT_FOUND_WITHIN_BOUNDS` is deliberately not `None`:
    it is not evidence of non-delivery."""

    FOUND = "FOUND"
    NOT_FOUND_WITHIN_BOUNDS = "NOT_FOUND_WITHIN_BOUNDS"
    UNSUPPORTED = "UNSUPPORTED"  # provider cannot reconcile at all
    LOOKUP_FAILED = "LOOKUP_FAILED"  # the reconciliation call itself failed


class ApprovalScope(StrEnum):
    """Part 5 — `approvals.scope`. Every v1 row is `PROSPECT` (the server
    default), preserving every existing approval as a valid row with the
    three new v2 columns left NULL."""

    PROSPECT = "PROSPECT"
    ACTION = "ACTION"


class ActionPolicyVerdict(StrEnum):
    """§6.1 — the deterministic action-policy verdict. No clause has an
    override (D7): a BLOCKED verdict carries `blocked_reasons` and offers no
    button."""

    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
