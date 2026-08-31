from groundwork.domain.dedupe import dedupe_key, find_duplicate, normalize_domain, normalize_name


def test_normalize_domain_strips_scheme_www_and_path() -> None:
    assert normalize_domain("https://www.Acme.com/") == "acme.com"
    assert normalize_domain("http://Acme.com/pricing") == "acme.com"
    assert normalize_domain("acme.com") == "acme.com"


def test_normalize_name_strips_legal_suffix_and_case() -> None:
    assert normalize_name("Northwind Labs Inc.") == "northwind labs"
    assert normalize_name("Northwind Labs") == "northwind labs"
    assert normalize_name("Acme, LLC") == "acme"


def test_dedupe_key_prefers_domain_over_name() -> None:
    key_a = dedupe_key("https://www.northwindlabs.com/", "Northwind Labs")
    key_b = dedupe_key("northwindlabs.com", "Northwind Labs Inc.")
    assert key_a == key_b


def test_dedupe_key_falls_back_to_name_without_domain() -> None:
    key = dedupe_key("", "Acme Corp.")
    assert key == "name:acme"


def test_find_duplicate_returns_earlier_prospect_id() -> None:
    seen = {"domain:acme.com": "prospect-1"}
    assert find_duplicate("domain:acme.com", seen) == "prospect-1"
    assert find_duplicate("domain:other.com", seen) is None
