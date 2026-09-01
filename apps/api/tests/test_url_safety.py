from groundwork.domain.url_safety import canonicalize_url, is_safe_source_url


def test_rejects_non_http_schemes() -> None:
    assert is_safe_source_url("ftp://example.com/file") is False
    assert is_safe_source_url("javascript:alert(1)") is False
    assert is_safe_source_url("mailto:a@example.com") is False
    assert is_safe_source_url("file:///etc/passwd") is False


def test_accepts_http_and_https() -> None:
    assert is_safe_source_url("http://example.com/page") is True
    assert is_safe_source_url("https://example.com/page") is True


def test_rejects_malformed_url() -> None:
    assert is_safe_source_url("") is False
    assert is_safe_source_url("not a url") is False
    assert is_safe_source_url(None) is False


def test_rejects_missing_host() -> None:
    assert is_safe_source_url("https:///path-only") is False
    assert is_safe_source_url("https://") is False


def test_rejects_credentialed_url() -> None:
    assert is_safe_source_url("https://user:pass@example.com/") is False
    assert is_safe_source_url("https://user@example.com/") is False


def test_rejects_localhost_and_local_suffixes() -> None:
    assert is_safe_source_url("https://localhost/page") is False
    assert is_safe_source_url("https://myservice.local/page") is False
    assert is_safe_source_url("https://internal-tool.internal/page") is False
    assert is_safe_source_url("https://box.localhost/page") is False


def test_rejects_ip_literal_hosts() -> None:
    assert is_safe_source_url("http://93.184.216.34/page") is False  # public IPv4
    assert is_safe_source_url("http://127.0.0.1/page") is False  # loopback
    assert is_safe_source_url("http://10.0.0.5/page") is False  # private
    assert is_safe_source_url("http://169.254.1.1/page") is False  # link-local
    assert is_safe_source_url("http://[::1]/page") is False  # IPv6 loopback
    assert is_safe_source_url("http://[2001:db8::1]/page") is False  # IPv6 documentation range


def test_rejects_overlength_url() -> None:
    long_path = "a" * 3000
    assert is_safe_source_url(f"https://example.com/{long_path}") is False


def test_canonicalize_lowercases_scheme_and_host() -> None:
    assert canonicalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_canonicalize_removes_default_port() -> None:
    assert canonicalize_url("https://example.com:443/page") == "https://example.com/page"
    assert canonicalize_url("http://example.com:80/page") == "http://example.com/page"


def test_canonicalize_keeps_non_default_port() -> None:
    assert canonicalize_url("https://example.com:8443/page") == "https://example.com:8443/page"


def test_canonicalize_removes_fragment() -> None:
    assert canonicalize_url("https://example.com/page#section") == "https://example.com/page"


def test_canonicalize_strips_tracking_params() -> None:
    result = canonicalize_url(
        "https://example.com/page?utm_source=x&utm_campaign=y&gclid=abc&fbclid=def&ref=z&keep=1"
    )
    assert result == "https://example.com/page?keep=1"


def test_canonicalize_sorts_remaining_query_params() -> None:
    assert canonicalize_url("https://example.com/page?b=2&a=1") == canonicalize_url(
        "https://example.com/page?a=1&b=2"
    )
    assert canonicalize_url("https://example.com/page?b=2&a=1") == "https://example.com/page?a=1&b=2"


def test_canonicalize_normalizes_trailing_slash() -> None:
    assert canonicalize_url("https://example.com/page/") == "https://example.com/page"
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_canonicalize_returns_none_for_unsafe_url() -> None:
    assert canonicalize_url("ftp://example.com") is None
    assert canonicalize_url("http://localhost/page") is None
