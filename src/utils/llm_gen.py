"""ChatOpenAI-клиент с прокси через httpx, семафором и ретраями.

Перенесён из metro_smk_doc_validator без изменений логики: показал стабильность.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator

import httpx
from langchain_openai import ChatOpenAI

from src.core.config import settings

HTTP_CONNECT_TIMEOUT = 30.0
HTTP_READ_TIMEOUT = 300.0
HTTP_WRITE_TIMEOUT = 300.0
HTTP_POOL = None
HTTP_VERIFY = False
HTTP_MAX_KEEPALIVE_CONNECTIONS = 5
HTTP_MAX_CONNECTIONS = 10
HTTP_KEEPALIVE_EXPIRY = 30.0
LLM_RETRY_ATTEMPTS = 5
LLM_RETRY_BUDGET_SECONDS = 60.0
LLM_OVERLOADED_MESSAGE = (
    "Сервис ИИ временно перегружен. Повторите запрос чуть позже."
)

log = logging.getLogger(__name__)
_llm_semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrency))


class LLMOverloadedError(RuntimeError):
    """Понятная ошибка для пользователя, когда LLM стабильно отвечает 429."""


def _status_code(exc: BaseException) -> int | None:
    """Извлекает HTTP-статус из исключений OpenAI/LangChain/httpx."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Возвращает задержку из Retry-After, если провайдер её передал."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    if not retry_after:
        return None
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _raw_error_payload(exc: BaseException) -> str:
    """Готовит сырой ответ провайдера для логов."""
    response = getattr(exc, "response", None)
    if response is None:
        return repr(exc)
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        data = getattr(response, "text", repr(exc))
    return str(data)


# Провайдер может сорвать structured output посреди стрима — это лечится повтором.
_PROVIDER_GLITCHES = (
    "json error",
    "sse stream",
    "upstream",
    "invalid json",
    "unexpected end",
    "incomplete",
)


def _is_retryable(exc: BaseException) -> bool:
    """Проверяет, стоит ли повторить LLM-вызов."""
    status = _status_code(exc)
    if status == 429 or (status is not None and 500 <= status < 600):
        return True

    text = f"{exc} {_raw_error_payload(exc)}".lower()
    if any(glitch in text for glitch in _PROVIDER_GLITCHES):
        return True

    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.NetworkError,
            httpx.PoolTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
            httpx.WriteError,
            httpx.WriteTimeout,
        ),
    )


def _backoff_seconds(attempt: int, exc: BaseException) -> float:
    """Считает экспоненциальный backoff с jitter и учётом Retry-After."""
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after
    base = min(2 ** (attempt - 1), 16)
    return base + random.uniform(0.0, 0.5)


async def ainvoke_llm(runnable, messages):
    """Вызывает LLM с процессным семафором и ретраями на 429/сеть/5xx."""
    started = time.monotonic()
    async with _llm_semaphore:
        for attempt in range(1, LLM_RETRY_ATTEMPTS + 1):
            try:
                return await runnable.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001
                status = _status_code(exc)
                retryable = _is_retryable(exc)
                payload = _raw_error_payload(exc)
                if not retryable or attempt == LLM_RETRY_ATTEMPTS:
                    if status == 429:
                        log.error("LLM 429 после ретраев: %s", payload)
                        raise LLMOverloadedError(LLM_OVERLOADED_MESSAGE) from exc
                    # Показываем ТЕЛО ответа провайдера — иначе 400 не отладить.
                    log.error(
                        "LLM ошибка status=%s: %s",
                        status,
                        payload[:2000],
                    )
                    raise

                elapsed = time.monotonic() - started
                delay = _backoff_seconds(attempt, exc)
                remaining = LLM_RETRY_BUDGET_SECONDS - elapsed
                if remaining <= 0:
                    if status == 429:
                        log.error("LLM 429 после исчерпания бюджета: %s", payload)
                        raise LLMOverloadedError(LLM_OVERLOADED_MESSAGE) from exc
                    raise
                delay = min(delay, remaining)
                log.warning(
                    "Повтор LLM-вызова status=%s attempt=%s/%s delay=%.1f raw=%s",
                    status,
                    attempt,
                    LLM_RETRY_ATTEMPTS,
                    delay,
                    payload,
                )
                await asyncio.sleep(delay)


@asynccontextmanager
async def get_llm(
    temperature: float | None = None,
    *,
    fast: bool = False,
    max_tokens: int = 8000,
) -> AsyncIterator[ChatOpenAI]:
    """Async-клиент OpenAI-совместимой LLM.

    Args:
        fast: True — быстрая модель (LLM_MODEL_FAST): вопросы, карточка проекта,
            распознавание ответа, подбор. False — тяжёлая (LLM_MODEL): только ТЗ.

    Стриминг выключен: structured output нужен целиком, поток токенов только
    добавляет задержку.
    """
    temp = temperature if temperature is not None else settings.llm_temperature
    model = settings.fast_model if fast else settings.llm_model
    client_kwargs: dict = {
        "verify": HTTP_VERIFY,
        "timeout": httpx.Timeout(
            connect=HTTP_CONNECT_TIMEOUT,
            read=HTTP_READ_TIMEOUT,
            write=HTTP_WRITE_TIMEOUT,
            pool=HTTP_POOL,
        ),
        "limits": httpx.Limits(
            max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
            max_connections=HTTP_MAX_CONNECTIONS,
            keepalive_expiry=HTTP_KEEPALIVE_EXPIRY,
        ),
        "trust_env": False,
    }
    if settings.proxy_url:
        client_kwargs["proxy"] = settings.proxy_url

    async with httpx.AsyncClient(**client_kwargs) as http_client:
        yield ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "not-needed",
            model=model,
            temperature=temp,
            max_tokens=max_tokens,
            streaming=False,
            disable_streaming=True,
            http_async_client=http_client,
            # LangChain ломает транспорт httpx при SOCKS без этого:
            # https://github.com/langchain-ai/langchain/issues/11334
            http_socket_options=(),
        )