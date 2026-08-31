"""Post-smoke-test hardening (Issue 3): the first real live smoke run's
`Sable Compute` prospect landed `REJECTED`/review `FAIL`. Investigation (see
`docs/PROGRESS.md`'s Checkpoint G section) concluded the most likely cause
is `claim_grounding`: `domain/review.py::_claim_grounding` re-verifies a
personalization sentence's token overlap against the ORIGINAL evidence
snippet, but the personalization prompt only ever showed the model an
already-paraphrased signal summary — never the original snippet — and gave
it no reason to echo close wording rather than write natural marketing
prose. A real GPT model asked to "write a short, personalized outreach
email" very plausibly favors the latter.

This is a genuine prompt/wiring gap, not a guardrail defect —
`domain/review.py`/`domain/grounding.py` are unchanged and untouched here.
The fix is `prompts/personalization.py` (bumped to `personalization-v2`)
now explicitly instructing the model to echo a cited signal's wording
closely. This test (1) guards that instruction against silent regression
and (2) demonstrates, with the actual Sable Compute fixture data and the
real, unmodified `token_overlap` function, why loose marketing prose
plausibly falls below the grounding threshold while close-echo phrasing
does not — the same mechanism, not a claim about what any specific live
model call will produce (which this suite cannot control or predict).
"""

from __future__ import annotations

from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, token_overlap
from groundwork.prompts import personalization


def test_personalization_prompt_instructs_close_wording_echo():
    system = personalization._SYSTEM
    assert "echo" in system.lower() or "closely" in system.lower()
    assert "original source text" in system.lower() or "wording" in system.lower()


def test_personalization_prompt_version_was_bumped_for_the_wiring_fix():
    assert personalization.PROMPT_VERSION == "personalization-v2"


# The actual Sable Compute fixture text (groundwork/fixtures/demo_pack.yaml)
# — unmodified, not invented for this test.
_FUNDING_SNIPPET = (
    "Sable Compute announced a $20M Series A round to expand its "
    "managed training cluster offering."
)
_HIRING_SNIPPET = (
    "Sable Compute has posted openings for a VP of Sales and an "
    "Enterprise Account Executive to grow its enterprise pipeline."
)


def test_loose_marketing_paraphrase_plausibly_fails_grounding():
    """Illustrative, not predictive: natural marketing prose that never
    reuses the source's own nouns/numbers is exactly the shape of sentence
    a real (unprompted-to-echo) LLM tends to write, and it measurably falls
    below the review gate's threshold against real fixture evidence."""
    loose = "Congrats on closing your Series A — sounds like an exciting phase of growth for the team."
    assert token_overlap(loose, _FUNDING_SNIPPET) < DEFAULT_OVERLAP_THRESHOLD


def test_close_echo_paraphrase_passes_grounding():
    """The same fact, phrased to echo the source's own key terms (what
    `personalization-v2`'s system prompt now explicitly asks for), clears
    the threshold comfortably."""
    close_echo = "Congrats on the $20M Series A round for Sable Compute."
    assert token_overlap(close_echo, _FUNDING_SNIPPET) >= DEFAULT_OVERLAP_THRESHOLD


def test_hiring_signal_same_pattern():
    loose = "I noticed you are actively building out your go-to-market org with new sales hires."
    close_echo = "Congrats on posting openings for a VP of Sales and an Enterprise Account Executive."
    assert token_overlap(loose, _HIRING_SNIPPET) < DEFAULT_OVERLAP_THRESHOLD
    assert token_overlap(close_echo, _HIRING_SNIPPET) >= DEFAULT_OVERLAP_THRESHOLD
