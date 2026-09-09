"""`domain/reconciliation_match.py` (V2-I-b correction, post-smoke) — pure
candidate-matching predicate for historyId-based Live `EMAIL_SEND`
reconciliation. Replaces the prior generated-Message-ID matching approach,
which the real V2-I-b smoke send disproved: Gmail returned the actually-
sent message with NEITHER `Message-ID` nor `X-Google-Original-Message-ID`
carrying the generated header."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from groundwork.domain.reconciliation_match import candidate_matches, canonicalize_subject

WINDOW_START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(minutes=5)
IN_WINDOW_DATE = "Thu, 1 Jan 2026 12:02:00 +0000"
OUT_OF_WINDOW_DATE = "Thu, 1 Jan 2026 13:00:00 +0000"


def _matching_kwargs(**overrides) -> dict:
    kwargs = dict(
        expected_subject="Hi there",
        expected_recipient_identifier="prospect@example.com",
        expected_sender_identifier="operator@example.com",
        candidate_subject="Hi there",
        candidate_to="Prospect <prospect@example.com>",
        candidate_from="Operator <operator@example.com>",
        candidate_date=IN_WINDOW_DATE,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    kwargs.update(overrides)
    return kwargs


class TestCanonicalizeSubject:
    def test_none_in_none_out(self):
        assert canonicalize_subject(None) is None

    def test_plain_ascii_unchanged_besides_whitespace_trim(self):
        assert canonicalize_subject("  Hi   there  ") == "Hi there"

    def test_rfc2047_encoded_word_decodes(self):
        # "Café update" UTF-8 base64-encoded, exactly the shape Gmail may
        # return even when the approved draft subject was plain text.
        encoded = "=?UTF-8?B?Q2Fmw6kgdXBkYXRl?="
        assert canonicalize_subject(encoded) == "Café update"

    def test_naive_raw_equality_would_wrongly_differ_but_canonicalized_matches(self):
        encoded = "=?UTF-8?B?SGkgdGhlcmU=?="  # "Hi there"
        assert encoded != "Hi there"  # the whole point: raw strings differ
        assert canonicalize_subject(encoded) == canonicalize_subject("Hi there")

    def test_collapses_internal_whitespace(self):
        assert canonicalize_subject("Hi\t\tthere\n") == "Hi there"


class TestCandidateMatches:
    def test_all_fields_matching_is_true(self):
        assert candidate_matches(**_matching_kwargs()) is True

    def test_subject_mismatch_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_subject="Something else")) is False

    def test_subject_rfc2047_encoded_still_matches(self):
        encoded = "=?UTF-8?B?SGkgdGhlcmU=?="  # "Hi there"
        assert candidate_matches(**_matching_kwargs(candidate_subject=encoded)) is True

    def test_subject_none_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_subject=None)) is False

    def test_expected_subject_none_is_false(self):
        assert candidate_matches(**_matching_kwargs(expected_subject=None, candidate_subject=None)) is False

    def test_to_mismatch_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_to="Someone Else <someone-else@example.com>")) is False

    def test_to_display_name_difference_does_not_affect_match(self):
        assert candidate_matches(**_matching_kwargs(candidate_to="A Totally Different Display Name <prospect@example.com>")) is True

    def test_to_case_folded_local_part_still_matches(self):
        assert candidate_matches(**_matching_kwargs(candidate_to="Prospect <Prospect@example.com>")) is True

    def test_to_missing_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_to=None)) is False

    def test_from_mismatch_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_from="Someone Else <someone-else@example.com>")) is False

    def test_from_missing_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_from=None)) is False

    def test_expected_recipient_malformed_is_false(self):
        assert candidate_matches(**_matching_kwargs(expected_recipient_identifier="not-an-email")) is False

    def test_expected_sender_malformed_is_false(self):
        assert candidate_matches(**_matching_kwargs(expected_sender_identifier="not-an-email")) is False

    def test_date_missing_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_date=None)) is False

    def test_date_unparseable_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_date="not a real date")) is False

    def test_date_timezone_naive_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_date="Thu, 1 Jan 2026 12:02:00")) is False

    def test_date_outside_window_is_false(self):
        assert candidate_matches(**_matching_kwargs(candidate_date=OUT_OF_WINDOW_DATE)) is False

    def test_date_at_window_boundaries_is_true(self):
        start_str = WINDOW_START.strftime("%a, %d %b %Y %H:%M:%S +0000")
        end_str = WINDOW_END.strftime("%a, %d %b %Y %H:%M:%S +0000")
        assert candidate_matches(**_matching_kwargs(candidate_date=start_str)) is True
        assert candidate_matches(**_matching_kwargs(candidate_date=end_str)) is True

    def test_never_reads_message_id_headers(self):
        """`candidate_matches` has no `message_id`/`message_id_header`
        parameter at all — the regression this whole module exists to
        prevent (a return to matching on a header Gmail may not preserve)
        would be a TypeError here, not a silent behavior change."""
        import inspect

        from groundwork.domain.reconciliation_match import candidate_matches as fn

        params = set(inspect.signature(fn).parameters)
        assert not any("message_id" in p.lower() for p in params)


def test_reconciliation_match_module_is_pure():
    """CLAUDE.md's standing invariant: `domain/` never imports from
    `providers/` or `repositories/`, no I/O."""
    import ast
    import inspect

    from groundwork.domain import reconciliation_match

    tree = ast.parse(inspect.getsource(reconciliation_match))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith("groundwork.providers"), name
        assert not name.startswith("groundwork.repositories"), name
        assert not name.startswith("sqlalchemy"), name
        assert "httpx" not in name
