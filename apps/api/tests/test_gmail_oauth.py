"""V2-G — Gmail OAuth (connection only, no sending).

No automated test in this file makes a real Google API call — every HTTP
exchange with "Google" is scripted via `ScriptedGoogleTransport`
(`tests/gmail_oauth_helpers.py`), mirroring every other `providers/live/*`
test file in this suite.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from itsdangerous import URLSafeTimedSerializer

from groundwork.api import gmail_state_binding, operator_auth
from groundwork.api.deps import get_google_oauth_runtime
from groundwork.config import settings
from groundwork.main import app
from groundwork.providers.live.google_oauth_runtime import (
    GMAIL_SCOPES,
    GoogleOAuthError,
    generate_pkce_pair,
    google_oauth_configured,
)
from groundwork.providers.send_base import DemoEmailSendProvider
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.token_crypto import TokenEncryptionError, decrypt_refresh_token, encrypt_refresh_token
from tests.api_helpers import login_as_operator
from tests.gmail_oauth_helpers import TEST_REDIRECT_URI, make_runtime, profile_response, token_response


def _configure_google_oauth(monkeypatch, **overrides) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", TEST_REDIRECT_URI)
    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    monkeypatch.setattr(settings, "gmail_callback_failure_rate_limit_attempts", 1000)
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)


def _override_runtime(runtime) -> None:
    app.dependency_overrides[get_google_oauth_runtime] = lambda: runtime


def _clear_runtime_override() -> None:
    app.dependency_overrides.pop(get_google_oauth_runtime, None)


async def _bare_client() -> httpx.AsyncClient:
    """A fresh, cookie-less client against the same app/session-factory
    override the `client` fixture already installed — for scenarios needing
    a SECOND, independently-cookied browser session."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"})


def _make_test_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("utf-8")


# --- scope / authorization URL -------------------------------------------


def test_gmail_scopes_are_exactly_send_and_metadata() -> None:
    assert set(GMAIL_SCOPES) == {
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.metadata",
    }
    forbidden = {"gmail.readonly", "openid", "email", "profile"}
    for scope in GMAIL_SCOPES:
        assert not any(scope.endswith(f"/{f}") for f in forbidden)


def test_authorization_url_has_exact_required_parameters() -> None:
    runtime, _transport = make_runtime()
    url = runtime.authorization_url(state="abc.def", code_challenge="challenge-value")
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)

    assert params["redirect_uri"] == [TEST_REDIRECT_URI]
    assert params["code_challenge"] == ["challenge-value"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["state"] == ["abc.def"]
    assert params["response_type"] == ["code"]
    assert set(params["scope"][0].split(" ")) == set(GMAIL_SCOPES)


def test_generate_pkce_pair_is_rfc7636_s256() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    assert challenge == expected
    assert "=" not in challenge


def test_google_oauth_configured_requires_all_three(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", None)
    assert google_oauth_configured() is False

    monkeypatch.setattr(settings, "google_client_id", "id")
    assert google_oauth_configured() is False
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    assert google_oauth_configured() is False
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", TEST_REDIRECT_URI)
    assert google_oauth_configured() is True


# --- runtime: token exchange / profile / revoke ---------------------------


async def test_exchange_code_success_returns_token_response() -> None:
    runtime, transport = make_runtime(token_steps=[(200, token_response())])
    body = await runtime.exchange_code(code="code-1", code_verifier="verifier-1")
    assert body["access_token"] == "test-access-token"
    assert transport.token_calls == 1


async def test_exchange_code_never_retries_a_definitive_4xx() -> None:
    runtime, transport = make_runtime(token_steps=[(400, {"error": "invalid_grant"})])
    with pytest.raises(GoogleOAuthError):
        await runtime.exchange_code(code="bad-code", code_verifier="verifier-1")
    assert transport.token_calls == 1  # never retried


async def test_exchange_code_retries_once_on_transport_failure_then_succeeds() -> None:
    runtime, transport = make_runtime(token_steps=[httpx.ConnectTimeout("boom"), (200, token_response())])
    body = await runtime.exchange_code(code="code-1", code_verifier="verifier-1")
    assert body["access_token"] == "test-access-token"
    assert transport.token_calls == 2


async def test_exchange_code_exhausts_bounded_transport_retries() -> None:
    runtime, transport = make_runtime(token_steps=[httpx.ConnectTimeout("1"), httpx.ConnectTimeout("2")])
    with pytest.raises(GoogleOAuthError):
        await runtime.exchange_code(code="code-1", code_verifier="verifier-1")
    assert transport.token_calls == 2  # 1 initial + 1 retry, bounded


async def test_get_profile_returns_email_address_and_sends_bearer_header() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=profile_response(email="operator@example.com"), request=request)

    runtime, _transport = make_runtime(handler=handler)
    body = await runtime.get_profile(access_token="tok-123")
    assert body["emailAddress"] == "operator@example.com"
    assert seen["request"].headers["authorization"] == "Bearer tok-123"


async def test_get_profile_missing_email_address_is_visible_to_caller() -> None:
    runtime, _transport = make_runtime(profile_steps=[(200, profile_response(email=None))])
    body = await runtime.get_profile(access_token="tok-123")
    assert "emailAddress" not in body


async def test_get_profile_only_ever_calls_the_profile_endpoint_never_userinfo() -> None:
    runtime, transport = make_runtime(profile_steps=[(200, profile_response())])
    await runtime.get_profile(access_token="tok-123")
    for request in transport.requests:
        assert "userinfo" not in str(request.url)


async def test_revoke_returns_true_on_2xx() -> None:
    runtime, _transport = make_runtime(revoke_steps=[(200, {})])
    assert await runtime.revoke(token="refresh-or-access-token") is True


async def test_revoke_returns_false_on_failure_never_raises() -> None:
    runtime, _transport = make_runtime(revoke_steps=[(400, {"error": "invalid_token"})])
    assert await runtime.revoke(token="whatever") is False

    runtime2, _transport2 = make_runtime(revoke_steps=[httpx.ConnectTimeout("x"), httpx.ConnectTimeout("y")])
    assert await runtime2.revoke(token="whatever") is False


async def test_refresh_access_token_success() -> None:
    runtime, _transport = make_runtime(token_steps=[(200, {"access_token": "fresh-token", "expires_in": 3599})])
    token = await runtime.refresh_access_token(refresh_token="stored-refresh-token")
    assert token == "fresh-token"


async def test_refresh_access_token_missing_field_raises() -> None:
    runtime, _transport = make_runtime(token_steps=[(200, {"expires_in": 3599})])
    with pytest.raises(GoogleOAuthError):
        await runtime.refresh_access_token(refresh_token="stored-refresh-token")


# --- provider purity -------------------------------------------------------


async def test_google_oauth_runtime_imports_no_repository_or_sqlalchemy() -> None:
    import ast
    import inspect

    from groundwork.providers.live import google_oauth_runtime

    tree = ast.parse(inspect.getsource(google_oauth_runtime))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith("sqlalchemy"), name
        assert not name.startswith("groundwork.repositories"), name
        assert name != "groundwork.models.tables", name


# --- token encryption -------------------------------------------------------


def test_token_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    ciphertext, version = encrypt_refresh_token("a-real-refresh-token")
    assert ciphertext != "a-real-refresh-token"
    assert decrypt_refresh_token(ciphertext, version) == "a-real-refresh-token"


def test_token_ciphertext_is_randomized(monkeypatch) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    ciphertext_a, _ = encrypt_refresh_token("same-plaintext")
    ciphertext_b, _ = encrypt_refresh_token("same-plaintext")
    assert ciphertext_a != ciphertext_b  # Fernet embeds a fresh IV/timestamp per call


def test_old_key_can_still_decrypt_after_rotation(monkeypatch) -> None:
    old_key = _make_test_fernet_key()
    monkeypatch.setattr(settings, "token_encryption_key", old_key)
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    ciphertext, _ = encrypt_refresh_token("pre-rotation-token")

    new_key = _make_test_fernet_key()
    monkeypatch.setattr(settings, "token_encryption_key", new_key)
    monkeypatch.setattr(settings, "token_encryption_key_old", old_key)
    assert decrypt_refresh_token(ciphertext) == "pre-rotation-token"


def test_new_writes_always_use_the_current_key(monkeypatch) -> None:
    old_key = _make_test_fernet_key()
    new_key = _make_test_fernet_key()
    monkeypatch.setattr(settings, "token_encryption_key", new_key)
    monkeypatch.setattr(settings, "token_encryption_key_old", old_key)
    ciphertext, _ = encrypt_refresh_token("post-rotation-token")

    # Decryptable with ONLY the new key configured (never needed the old one).
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    assert decrypt_refresh_token(ciphertext) == "post-rotation-token"


def test_decrypt_fails_closed_with_wrong_or_missing_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    ciphertext, _ = encrypt_refresh_token("secret-token")

    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    with pytest.raises(TokenEncryptionError):
        decrypt_refresh_token(ciphertext)

    monkeypatch.setattr(settings, "token_encryption_key", None)
    with pytest.raises(TokenEncryptionError):
        decrypt_refresh_token(ciphertext)


def test_encrypt_fails_closed_without_a_configured_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", None)
    with pytest.raises(TokenEncryptionError):
        encrypt_refresh_token("anything")


# --- state/session binding (pure) ------------------------------------------


def test_mint_and_verify_binding_round_trips(monkeypatch) -> None:
    monkeypatch.setattr(settings, "session_signing_key", "key-a")
    monkeypatch.setattr(settings, "session_signing_key_old", None)
    state_id, state_param = gmail_state_binding.mint_state_param("cookie-value-1")
    parsed_id, tag = gmail_state_binding.parse_state_param(state_param)
    assert parsed_id == state_id
    assert gmail_state_binding.verify_binding(state_id=state_id, tag=tag, operator_cookie_value="cookie-value-1")


def test_verify_binding_fails_for_a_different_cookie(monkeypatch) -> None:
    monkeypatch.setattr(settings, "session_signing_key", "key-a")
    monkeypatch.setattr(settings, "session_signing_key_old", None)
    state_id, state_param = gmail_state_binding.mint_state_param("cookie-value-1")
    _parsed_id, tag = gmail_state_binding.parse_state_param(state_param)
    assert not gmail_state_binding.verify_binding(state_id=state_id, tag=tag, operator_cookie_value="cookie-value-2")


@pytest.mark.parametrize(
    "bad_state",
    [
        "no-separator-at-all",
        "too.many.dots.here",
        ".emptystateid",
        "emptytag.",
        "has spaces.deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "abc123." + "z" * 64,  # tag not hex
        "abc123." + "a" * 10,  # tag too short
    ],
)
def test_parse_state_param_rejects_malformed_input(bad_state: str) -> None:
    with pytest.raises(gmail_state_binding.MalformedStateParam):
        gmail_state_binding.parse_state_param(bad_state)


def test_old_session_signing_key_verifies_a_state_minted_before_rotation(monkeypatch) -> None:
    old_key = "old-signing-key"
    monkeypatch.setattr(settings, "session_signing_key", old_key)
    monkeypatch.setattr(settings, "session_signing_key_old", None)
    state_id, state_param = gmail_state_binding.mint_state_param("cookie-1")
    _sid, tag = gmail_state_binding.parse_state_param(state_param)

    new_key = "new-signing-key"
    monkeypatch.setattr(settings, "session_signing_key", new_key)
    monkeypatch.setattr(settings, "session_signing_key_old", old_key)
    assert gmail_state_binding.verify_binding(state_id=state_id, tag=tag, operator_cookie_value="cookie-1")


def test_new_state_is_always_minted_with_the_current_key(monkeypatch) -> None:
    old_key = "old-signing-key"
    new_key = "new-signing-key"
    monkeypatch.setattr(settings, "session_signing_key", new_key)
    monkeypatch.setattr(settings, "session_signing_key_old", old_key)
    state_id, state_param = gmail_state_binding.mint_state_param("cookie-1")
    _sid, tag = gmail_state_binding.parse_state_param(state_param)

    # Verifiable with ONLY the new (current) key configured.
    monkeypatch.setattr(settings, "session_signing_key_old", None)
    assert gmail_state_binding.verify_binding(state_id=state_id, tag=tag, operator_cookie_value="cookie-1")


# --- repository: consume_state concurrency ---------------------------------


async def test_consume_state_concurrent_double_consume_has_exactly_one_winner(session_factory) -> None:
    repo = GmailConnectionRepository(session_factory)
    await repo.create_state(state_id="race-state", pkce_verifier="verifier", ttl_s=600)

    results = await asyncio.gather(
        repo.consume_state("race-state"), repo.consume_state("race-state"), return_exceptions=True
    )
    winners = [r for r in results if r is not None and not isinstance(r, Exception)]
    assert len(winners) == 1


async def test_consume_state_replay_fails_at_the_guarded_update(session_factory) -> None:
    repo = GmailConnectionRepository(session_factory)
    await repo.create_state(state_id="replay-state", pkce_verifier="verifier", ttl_s=600)
    first = await repo.consume_state("replay-state")
    assert first is not None
    second = await repo.consume_state("replay-state")
    assert second is None


async def test_consume_state_rejects_expired_state(session_factory) -> None:
    repo = GmailConnectionRepository(session_factory)
    await repo.create_state(state_id="expired-state", pkce_verifier="verifier", ttl_s=-1)
    result = await repo.consume_state("expired-state")
    assert result is None


async def test_connected_account_identifier_none_before_and_after_disconnect(session_factory) -> None:
    from groundwork.timeutil import utcnow

    repo = GmailConnectionRepository(session_factory)
    assert await repo.connected_account_identifier() is None

    await repo.upsert_connection(
        google_account_email="Operator@Example.com",
        encrypted_refresh_token="ciphertext",
        key_version=1,
        scopes=list(GMAIL_SCOPES),
        connected_at=utcnow(),
        connected_by_actor="operator",
        last_refreshed_at=utcnow(),
    )
    identifier = await repo.connected_account_identifier()
    assert identifier == "operator@example.com"  # normalized, never the credential

    await repo.delete_connection()
    assert await repo.connected_account_identifier() is None


# --- Demo sending identity --------------------------------------------------


async def test_demo_email_send_provider_identity_is_the_exact_constant() -> None:
    provider = DemoEmailSendProvider()
    assert await provider.connected_account_identifier() == "demo-sender@groundwork.invalid"


def test_send_base_module_has_zero_network_imports() -> None:
    import ast
    import inspect

    from groundwork.providers import send_base

    tree = ast.parse(inspect.getsource(send_base))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith("httpx"), name
        assert not name.startswith("socket"), name


# --- redaction --------------------------------------------------------------


def test_redact_strips_google_client_secret(monkeypatch) -> None:
    from groundwork.observability.redact import redact

    monkeypatch.setattr(settings, "google_client_secret", "SUPER-SECRET-CLIENT-VALUE")
    out = redact("token exchange failed, echoed SUPER-SECRET-CLIENT-VALUE in body")
    assert "SUPER-SECRET-CLIENT-VALUE" not in out
    assert "[REDACTED]" in out


def test_redact_strips_token_encryption_keys(monkeypatch) -> None:
    from groundwork.observability.redact import redact

    monkeypatch.setattr(settings, "token_encryption_key", "CURRENT-FERNET-KEY-VALUE")
    monkeypatch.setattr(settings, "token_encryption_key_old", "OLD-FERNET-KEY-VALUE")
    out = redact("leaked CURRENT-FERNET-KEY-VALUE and OLD-FERNET-KEY-VALUE somehow")
    assert "CURRENT-FERNET-KEY-VALUE" not in out
    assert "OLD-FERNET-KEY-VALUE" not in out


# --- API: connect requires operator ----------------------------------------


async def test_connect_requires_operator_session(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    r = await client.post("/api/gmail/connect")
    assert r.status_code == 401


async def test_connect_422s_when_google_oauth_not_configured(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", None)
    _clear_runtime_override()
    r = await client.post("/api/gmail/connect")
    assert r.status_code == 422


async def test_connection_requires_operator_session(client, session_factory) -> None:
    r = await client.get("/api/gmail/connection")
    assert r.status_code == 401


async def test_disconnect_requires_operator_session(client, session_factory) -> None:
    r = await client.delete("/api/gmail/connection")
    assert r.status_code == 401


# --- API: full end-to-end flow ---------------------------------------------


def _extract_state(authorization_url: str) -> str:
    params = parse_qs(urlsplit(authorization_url).query)
    return params["state"][0]


async def test_full_connect_callback_round_trip(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)

    runtime, transport = make_runtime(
        token_steps=[(200, token_response())],
        profile_steps=[(200, profile_response(email="Priya.Natarajan@Example.com"))],
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        assert connect_resp.status_code == 200, connect_resp.text
        authorization_url = connect_resp.json()["authorization_url"]
        state = _extract_state(authorization_url)

        callback_resp = await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
        assert callback_resp.status_code in (303, 307)
        assert callback_resp.headers["location"] == "/settings?gmail=connected"
        assert transport.token_calls == 1
        assert transport.profile_calls == 1

        conn_resp = await client.get("/api/gmail/connection")
        assert conn_resp.status_code == 200
        body = conn_resp.json()
        assert body["connected"] is True
        assert body["google_account_email"] == "Priya.Natarajan@Example.com"
        assert set(body["scopes"]) == set(GMAIL_SCOPES)
        # Never the token/ciphertext/key/verifier, under any key name.
        blob = str(body).lower()
        for forbidden in ("token", "secret", "verifier", "ciphertext"):
            assert forbidden not in blob
    finally:
        _clear_runtime_override()


async def test_missing_email_address_fails_closed_and_persists_nothing(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)

    runtime, transport = make_runtime(
        token_steps=[(200, token_response())],
        profile_steps=[(200, profile_response(email=None))],
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        callback_resp = await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
        assert callback_resp.headers["location"] == "/settings?gmail=error&reason=unknown"

        conn_resp = await client.get("/api/gmail/connection")
        assert conn_resp.json()["connected"] is False
    finally:
        _clear_runtime_override()


async def test_malformed_state_is_400_before_any_db_or_cookie_check(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    # Deliberately NOT logged in as operator — malformed state must win first.
    r = await client.get("/api/gmail/callback?state=not-well-formed&code=x")
    assert r.status_code == 400


async def test_missing_operator_session_is_401_no_exchange(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, transport = make_runtime(token_steps=[(200, token_response())])
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        bare = await _bare_client()
        try:
            r = await bare.get(f"/api/gmail/callback?state={state}&code=x")
            assert r.status_code == 401
        finally:
            await bare.aclose()
        assert transport.token_calls == 0
    finally:
        _clear_runtime_override()


async def test_different_valid_operator_session_is_403_and_never_consumes(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    old_key = "another-valid-signing-key"
    monkeypatch.setattr(settings, "session_signing_key_old", old_key)
    await login_as_operator(client, monkeypatch)

    cookie_a = client.cookies.get(operator_auth.COOKIE_NAME)
    assert cookie_a is not None

    # A second, genuinely different, ALSO-valid operator session — signed
    # with the OLD key rather than by sleeping for a new itsdangerous
    # timestamp (verify_session_cookie() accepts either).
    cookie_b = URLSafeTimedSerializer(old_key, salt=operator_auth._SALT).dumps(operator_auth._PAYLOAD)
    assert cookie_a != cookie_b
    assert operator_auth.verify_session_cookie(cookie_b) is True

    runtime, transport = make_runtime(
        token_steps=[(200, token_response())], profile_steps=[(200, profile_response())]
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        other = await _bare_client()
        try:
            other.cookies.set(operator_auth.COOKIE_NAME, cookie_b)
            r = await other.get(f"/api/gmail/callback?state={state}&code=x")
            assert r.status_code == 403
        finally:
            await other.aclose()
        assert transport.token_calls == 0

        # The row must remain unconsumed — the ORIGINAL session can still use it.
        second_resp = await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
        assert second_resp.status_code in (303, 307)
        assert second_resp.headers["location"] == "/settings?gmail=connected"
    finally:
        _clear_runtime_override()


async def test_replay_with_correct_session_is_409(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, transport = make_runtime(
        token_steps=[(200, token_response())], profile_steps=[(200, profile_response())]
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        first = await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
        assert first.status_code in (303, 307)

        second = await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
        assert second.status_code == 409
        assert transport.token_calls == 1  # no second exchange
    finally:
        _clear_runtime_override()


async def test_access_denied_consumes_state_and_redirects_sanitized(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, transport = make_runtime()
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        r = await client.get(f"/api/gmail/callback?state={state}&error=access_denied")
        assert r.headers["location"] == "/settings?gmail=error&reason=access_denied"
        assert transport.token_calls == 0

        # Repeat callback with the same (now-consumed) state -> replay.
        r2 = await client.get(f"/api/gmail/callback?state={state}&error=access_denied")
        assert r2.status_code == 409
    finally:
        _clear_runtime_override()


async def test_unknown_google_error_code_is_sanitized(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, _transport = make_runtime()
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])
        r = await client.get(f"/api/gmail/callback?state={state}&error=some_totally_unrecognized_code")
        assert r.headers["location"] == "/settings?gmail=error&reason=unknown"
    finally:
        _clear_runtime_override()


async def test_revoke_failure_still_deletes_the_local_row(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, transport = make_runtime(
        token_steps=[(200, token_response())], profile_steps=[(200, profile_response())]
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])
        await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")

        conn = await client.get("/api/gmail/connection")
        assert conn.json()["connected"] is True
    finally:
        _clear_runtime_override()

    # Swap in a runtime whose revoke call always fails.
    failing_runtime, _t = make_runtime(revoke_steps=[(400, {"error": "invalid_token"})])
    _override_runtime(failing_runtime)
    try:
        disconnect_resp = await client.delete("/api/gmail/connection")
        assert disconnect_resp.status_code == 200
        assert disconnect_resp.json()["deleted"] is True

        conn_after = await client.get("/api/gmail/connection")
        assert conn_after.json()["connected"] is False
    finally:
        _clear_runtime_override()


async def test_callback_failure_rate_limiting(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    monkeypatch.setattr(settings, "gmail_callback_failure_rate_limit_attempts", 2)
    monkeypatch.setattr(settings, "gmail_callback_failure_rate_limit_window_s", 60.0)

    # Rebuild the module-level limiter with the patched (small) bound —
    # it was constructed at import time from the pre-test settings.
    import groundwork.api.routers.gmail as gmail_router_module
    from groundwork.api.rate_limit import SlidingWindowRateLimiter

    monkeypatch.setattr(
        gmail_router_module,
        "_callback_failure_limiter",
        SlidingWindowRateLimiter(max_attempts=2, window_s=60.0),
    )

    await login_as_operator(client, monkeypatch)
    old_key = "yet-another-old-key"
    monkeypatch.setattr(settings, "session_signing_key_old", old_key)
    cookie_b = URLSafeTimedSerializer(old_key, salt=operator_auth._SALT).dumps(operator_auth._PAYLOAD)

    runtime, _transport = make_runtime()
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])

        other = await _bare_client()
        try:
            other.cookies.set(operator_auth.COOKIE_NAME, cookie_b)
            r1 = await other.get(f"/api/gmail/callback?state={state}&code=x")
            assert r1.status_code == 403
            r2 = await other.get(f"/api/gmail/callback?state={state}&code=x")
            assert r2.status_code == 403
            r3 = await other.get(f"/api/gmail/callback?state={state}&code=x")
            assert r3.status_code == 429
        finally:
            await other.aclose()
    finally:
        _clear_runtime_override()


async def test_settings_providers_hides_gmail_identity_from_non_operator(client, session_factory, monkeypatch) -> None:
    _configure_google_oauth(monkeypatch)
    await login_as_operator(client, monkeypatch)
    runtime, _transport = make_runtime(
        token_steps=[(200, token_response())], profile_steps=[(200, profile_response(email="hidden@example.com"))]
    )
    _override_runtime(runtime)
    try:
        connect_resp = await client.post("/api/gmail/connect")
        state = _extract_state(connect_resp.json()["authorization_url"])
        await client.get(f"/api/gmail/callback?state={state}&code=test-auth-code")
    finally:
        _clear_runtime_override()

    operator_settings = await client.get("/api/settings/providers")
    assert operator_settings.json()["gmail"]["connected"] is True
    assert operator_settings.json()["gmail"]["google_account_email"] == "hidden@example.com"

    bare = await _bare_client()
    try:
        non_operator_settings = await bare.get("/api/settings/providers")
        gmail_block = non_operator_settings.json()["gmail"]
        assert gmail_block["connected"] is False
        assert gmail_block["google_account_email"] is None
    finally:
        await bare.aclose()
