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
