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
