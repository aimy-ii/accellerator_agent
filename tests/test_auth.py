"""Тесты аутентификации агента (src/auth.py) + приоритет токена в nodes.py.

Introspection-ручку акселератора НЕ дёргаем реально — подменяем
httpx.AsyncClient на MockTransport (тот же приём, что в test_client.py).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from langgraph.runtime import Runtime
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException as StarletteHTTPException

import src.auth as auth_module
import src.graph.nodes as nodes_module
from src.core.config import settings
from src.graph.state import Context

JWT = "user.jwt.token"  # noqa: S105 — тестовый токен, не секрет
S2S_TOKEN = "s2s-secret-token"  # noqa: S105


def _patch_introspect(monkeypatch, handler):
    """Подменяет httpx.AsyncClient в src.auth так, чтобы запрос шёл в MockTransport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", factory)


def _ctx(identity: str, *, resource: str = "threads", action: str = "create") -> Auth.types.AuthContext:
    """Минимальный AuthContext для тестов хуков on.threads.*."""
    return Auth.types.AuthContext(
        permissions=[],
        user=SimpleNamespace(identity=identity),
        resource=resource,
        action=action,
    )


@pytest.fixture(autouse=True)
def _auth_settings(monkeypatch):
    """Общие настройки auth на все тесты: включён, известный s2s-токен."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "agent_introspect_token", S2S_TOKEN)
    monkeypatch.setattr(settings, "agent_introspect_url", "http://acc/api/internal/auth/introspect")
    monkeypatch.setattr(settings, "agent_name", "customer_agent")


# ─── authenticate: happy path ───────────────────────────────────────────────

async def test_authenticate_200_returns_identity_and_user_token(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"user_id": 42})

    _patch_introspect(monkeypatch, handler)

    result = await auth_module.authenticate(authorization=f"Bearer {JWT}")

    assert result == {"identity": "42", "user_token": JWT}
    assert captured["headers"]["x-service-token"] == S2S_TOKEN
    assert JWT in captured["body"]  # ушёл в теле запроса к ручке


# ─── authenticate: коды ошибок ручки ────────────────────────────────────────

async def test_authenticate_401_from_introspect_raises_401(monkeypatch):
    _patch_introspect(monkeypatch, lambda request: httpx.Response(401, json={"detail": "Invalid user token"}))

    with pytest.raises(StarletteHTTPException) as exc_info:
        await auth_module.authenticate(authorization=f"Bearer {JWT}")
    assert exc_info.value.status_code == 401


async def test_authenticate_403_from_introspect_raises_403(monkeypatch):
    _patch_introspect(monkeypatch, lambda request: httpx.Response(403, json={"detail": "Access denied"}))

    with pytest.raises(StarletteHTTPException) as exc_info:
        await auth_module.authenticate(authorization=f"Bearer {JWT}")
    assert exc_info.value.status_code == 403


async def test_authenticate_400_unknown_agent_raises_403_outward(monkeypatch):
    """400 (agent_name не в AGENT_ROLES) — наружу закрыто как 403, не 400."""
    _patch_introspect(monkeypatch, lambda request: httpx.Response(400, json={"detail": "Unknown agent"}))

    with pytest.raises(StarletteHTTPException) as exc_info:
        await auth_module.authenticate(authorization=f"Bearer {JWT}")
    assert exc_info.value.status_code == 403


async def test_authenticate_network_error_raises_503(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_introspect(monkeypatch, handler)

    with pytest.raises(StarletteHTTPException) as exc_info:
        await auth_module.authenticate(authorization=f"Bearer {JWT}")
    assert exc_info.value.status_code == 503


# ─── authenticate: заголовок Authorization ─────────────────────────────────

@pytest.mark.parametrize("authorization", [None, "", "Bearer", "Bearer   ", "Basic abc123", "abc123"])
async def test_authenticate_missing_or_malformed_header_raises_401_no_network_call(monkeypatch, authorization):
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"user_id": 1})

    _patch_introspect(monkeypatch, handler)

    with pytest.raises(StarletteHTTPException) as exc_info:
        await auth_module.authenticate(authorization=authorization)

    assert exc_info.value.status_code == 401
    assert called is False  # ручка НЕ вызывалась


# ─── authenticate: auth_enabled=false ──────────────────────────────────────

async def test_authenticate_disabled_skips_introspect_call(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"user_id": 1})

    _patch_introspect(monkeypatch, handler)

    result = await auth_module.authenticate(authorization=None)

    assert called is False
    assert result["identity"] == auth_module._DEV_IDENTITY


# ─── _runtime_params: приоритет источников токена ──────────────────────────

def test_runtime_params_prefers_langgraph_auth_user_token(monkeypatch):
    monkeypatch.setattr(
        nodes_module,
        "get_config",
        lambda: {
            "configurable": {
                "langgraph_auth_user": {"identity": "42", "user_token": "prod-token"},
                "user_token": "studio-token",
            }
        },
    )
    runtime: Runtime[Context] = Runtime(context={"user_token": "context-token"})

    params = nodes_module._runtime_params(runtime)

    assert params["user_token"] == "prod-token"


def test_runtime_params_falls_back_to_configurable_without_auth_user(monkeypatch):
    monkeypatch.setattr(
        nodes_module,
        "get_config",
        lambda: {"configurable": {"user_token": "studio-token", "api_base_url": "http://acc"}},
    )
    runtime: Runtime[Context] = Runtime(context={})

    params = nodes_module._runtime_params(runtime)

    assert params["user_token"] == "studio-token"
    assert params["api_base_url"] == "http://acc"


def test_runtime_params_falls_back_to_dev_user_token(monkeypatch):
    monkeypatch.setattr(nodes_module, "get_config", lambda: {})
    monkeypatch.setattr(settings, "dev_user_token", "dev-token")
    runtime: Runtime[Context] = Runtime(context={})

    params = nodes_module._runtime_params(runtime)

    assert params["user_token"] == "dev-token"


def test_runtime_params_auth_user_without_user_token_falls_through(monkeypatch):
    """langgraph_auth_user есть (напр. dev-identity), но user_token в нём None."""
    monkeypatch.setattr(
        nodes_module,
        "get_config",
        lambda: {"configurable": {"langgraph_auth_user": {"identity": "dev", "user_token": None}}},
    )
    monkeypatch.setattr(settings, "dev_user_token", "dev-token")
    runtime: Runtime[Context] = Runtime(context={})

    params = nodes_module._runtime_params(runtime)

    assert params["user_token"] == "dev-token"


# ─── персональные треды: owner только из ctx.user.identity ─────────────────

async def test_thread_create_stamps_owner_from_identity_not_body():
    value = {"metadata": {}, "owner": "attacker-supplied-id"}

    await auth_module.on_thread_create(_ctx("42", action="create"), value)

    assert value["metadata"]["owner"] == "42"
    assert value["owner"] == "attacker-supplied-id"  # тело не читаем — не трогаем


async def test_thread_create_sets_metadata_when_absent():
    value: dict = {}

    await auth_module.on_thread_create(_ctx("7", action="create"), value)

    assert value["metadata"]["owner"] == "7"


@pytest.mark.parametrize("action", ["read", "search", "update", "delete"])
async def test_thread_scope_returns_owner_filter(action):
    result = await auth_module.on_thread_scope(_ctx("42", action=action), {})
    assert result == {"owner": "42"}


# ─── JWT / s2s не попадают в логи ───────────────────────────────────────────

async def test_secrets_not_logged_on_success(monkeypatch, caplog):
    _patch_introspect(monkeypatch, lambda request: httpx.Response(200, json={"user_id": 42}))

    with caplog.at_level(logging.DEBUG):
        await auth_module.authenticate(authorization=f"Bearer {JWT}")

    log_text = caplog.text
    assert JWT not in log_text
    assert S2S_TOKEN not in log_text


async def test_secrets_not_logged_on_failure(monkeypatch, caplog):
    _patch_introspect(monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad"}))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(StarletteHTTPException):
            await auth_module.authenticate(authorization=f"Bearer {JWT}")

    log_text = caplog.text
    assert JWT not in log_text
    assert S2S_TOKEN not in log_text
