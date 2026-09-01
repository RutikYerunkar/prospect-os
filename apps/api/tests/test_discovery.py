from groundwork.domain.discovery import (
    is_structural_aggregator,
    label_supported_by_sources,
    resolve_candidate_domain,
)


def test_structural_aggregator_domains_rejected() -> None:
    assert is_structural_aggregator("linkedin.com") is True
    assert is_structural_aggregator("www.linkedin.com") is True
    assert is_structural_aggregator("crunchbase.com") is True
    assert is_structural_aggregator("acme.com") is False


def test_resolve_candidate_domain_requires_served_set() -> None:
    served = frozenset({"acme.com"})
    assert resolve_candidate_domain("https://acme.com/about", served) == "acme.com"
    assert resolve_candidate_domain("https://not-served.com/about", served) is None


def test_resolve_candidate_domain_never_originates_from_model_text_alone() -> None:
    """Even a plausible-looking domain must be rejected if it wasn't in the
    served candidate set — the model asserting a domain is not enough."""
    served: frozenset[str] = frozenset()
    assert resolve_candidate_domain("https://acme.com/about", served) is None


def test_resolve_candidate_domain_rejects_aggregators_even_if_served() -> None:
    served = frozenset({"linkedin.com"})
    assert resolve_candidate_domain("https://linkedin.com/company/acme", served) is None


def test_resolve_candidate_domain_rejects_unsafe_url() -> None:
    served = frozenset({"acme.com"})
    assert resolve_candidate_domain("http://localhost/acme", served) is None
    assert resolve_candidate_domain(None, served) is None


def test_resolve_candidate_domain_normalizes_before_matching() -> None:
    served = frozenset({"acme.com"})
    assert resolve_candidate_domain("https://WWW.ACME.COM/about", served) == "acme.com"


def test_label_supported_requires_served_ref() -> None:
    served_refs = frozenset({"source-1"})
    assert label_supported_by_sources("Acme Corp", served_refs, "source-1") is True
    assert label_supported_by_sources("Acme Corp", served_refs, "source-2") is False
    assert label_supported_by_sources("Acme Corp", served_refs, None) is False


def test_label_supported_rejects_empty_label() -> None:
    served_refs = frozenset({"source-1"})
    assert label_supported_by_sources("   ", served_refs, "source-1") is False
