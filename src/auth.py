"""Аутентификация внешнего агента через introspection-ручку акселератора.

Фронт шлёт пользовательский JWT ОДИН РАЗ за запрос — в заголовке
`Authorization: Bearer <JWT>`. Агент сам:
    1. валидирует токен, сходив в introspection-ручку акселератора со своим
       s2s-токеном (`@auth.authenticate`);
    2. раскладывает JWT в auth-контекст run'а (`langgraph_auth_user`), чтобы
       граф ходил в API акселератора от лица заказчика (см. `_runtime_params`
       в `src/graph/nodes.py`);
    3. персонализирует треды по владельцу — заказчик видит и резюмит только
       свои (`@auth.on.threads`).

Агент НЕ проверяет подпись JWT сам и НЕ хранит секрет issuer'а — только ходит
в ручку `settings.agent_introspect_url`. JWT и s2s-токен НЕ логируются никогда.
"""
from __future__ import annotations

import logging

import httpx
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.config import settings

log = logging.getLogger(__name__)

auth = Auth()

# Локальная отладка при auth_enabled=false — фиксированный identity, без
# похода в ручку. НЕ использовать в проде.
_DEV_IDENTITY = "dev-local-user"


@auth.authenticate
async def authenticate(authorization: str | None) -> dict:
    """Валидирует пользовательский JWT через introspection-ручку акселератора.

    Возвращает dict, который LangGraph кладёт в
    `config["configurable"]["langgraph_auth_user"]` — auth-контекст run'а.
    `identity` становится владельцем тредов; `user_token` — сырой JWT, которым
    граф ходит в API акселератора от лица заказчика (см. `_runtime_params`).

    Токен НИКОГДА не попадает в state графа — только в auth-контекст, в
    чекпоинт не пишется.
    """
    if not settings.auth_enabled:
        log.warning("AUTH_ENABLED=false — auth пропущен, dev-identity (только локальная отладка)")
        return {"identity": _DEV_IDENTITY, "user_token": None}

    token = _extract_bearer_token(authorization)
    if not token:
        log.info("Запрос без валидного Authorization: Bearer <JWT>")
        raise StarletteHTTPException(status_code=401, detail="Unauthorized")

    try:
        async with httpx.AsyncClient(
            timeout=settings.api_timeout,
            verify=settings.api_verify_ssl,
        ) as client:
            response = await client.post(
                settings.agent_introspect_url,
                headers={"X-Service-Token": settings.agent_introspect_token or ""},
                json={"token": token, "agent_name": settings.agent_name},
            )
    except httpx.RequestError as exc:
        log.error("Introspection-ручка недоступна: %s", type(exc).__name__)
        raise StarletteHTTPException(status_code=503, detail="Auth service unavailable") from exc

    if response.status_code == 200:
        try:
            user_id = response.json().get("user_id")
        except ValueError as exc:
            log.error("Introspection вернул невалидный JSON при http=200")
            raise StarletteHTTPException(status_code=503, detail="Auth service unavailable") from exc
        if user_id is None:
            log.error("Introspection вернул http=200 без user_id")
            raise StarletteHTTPException(status_code=503, detail="Auth service unavailable")
        log.info("Introspection OK, http=200")
        return {"identity": str(user_id), "user_token": token}

    if response.status_code == 401:
        log.info("Introspection отказал, http=401")
        raise StarletteHTTPException(status_code=401, detail="Unauthorized")

    if response.status_code == 403:
        log.info("Introspection отказал, http=403")
        raise StarletteHTTPException(status_code=403, detail="Forbidden")

    if response.status_code == 400:
        # Неизвестный agent_name — деталь конфигурации агента, наружу не раскрываем.
        log.error("Introspection: неизвестный agent_name, http=400")
        raise StarletteHTTPException(status_code=403, detail="Forbidden")

    log.error("Introspection вернул неожиданный код: %s", response.status_code)
    raise StarletteHTTPException(status_code=503, detail="Auth service unavailable")


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Достаёт JWT из заголовка `Authorization: Bearer <JWT>`.

    Нет заголовка / неверная схема / пустой токен → None.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


# ─── персональные треды: владелец — только из ctx.user.identity ────────────

@auth.on.threads.create
async def on_thread_create(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Проставляет владельца треда при создании. Владелец — ТОЛЬКО из ctx.user.

    Никогда не берём owner из тела запроса — заказчик не может создать тред
    от имени другого пользователя.
    """
    metadata = value.setdefault("metadata", {})
    metadata["owner"] = ctx.user.identity


@auth.on.threads.read
@auth.on.threads.update
@auth.on.threads.delete
@auth.on.threads.search
async def on_thread_scope(ctx: Auth.types.AuthContext, value: dict) -> dict:
    """Фильтрует чтение/обновление/удаление/поиск тредов по владельцу.

    Заказчик видит и резюмит только свои треды.
    """
    return {"owner": ctx.user.identity}
