"""Клиент API акселератора.

Работает СТРОГО с теми ручками, что есть в бэкенде сейчас (develop).
Все запросы от лица заказчика — Bearer-токен передаётся в конструктор.

Реализованные ручки:
    GET   /api/customer/projects/v2      — проекты заказчика (конверт с пагинацией)
    GET   /api/customer/projects         — проекты заказчика (плоский список)
    POST  /api/customer/create-project   — создать проект
    PATCH /api/customer/{project_id}     — обновить проект (в т.ч. files[])
    POST  /api/customer/upload-project-file — загрузить файл → file_url
    GET   /api/public/interns/v2         — специалисты (фильтры + пагинация)
    GET   /api/public/interns/{id}       — профиль специалиста
    GET   /api/public/professions        — справочник профессий
    GET   /api/public/stacks             — справочник стеков

Чего в API ещё НЕТ (осознанно не эмулируем ручками, закрываем на клиенте):
    - exclude_for_project_id у /interns/v2 → исключение делаем фильтром на клиенте
    - /specialists/facets                  → используем /professions + /stacks
    - project_invitations                  → команда живёт в state графа
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from src.api.errors import (
    AcceleratorAPIError,
    AcceleratorAuthError,
    AcceleratorNotFoundError,
    DocumentDownloadError,
    DocumentTooLargeError,
)
from src.core.config import settings

log = logging.getLogger(__name__)


class AcceleratorAPI:
    """HTTP-клиент акселератора. Один экземпляр — один пользовательский токен."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._token = token
        self._base = (base_url or settings.api_base_url).rstrip("/")
        self._timeout = timeout or settings.api_timeout

    # ── низкий уровень ──────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self._base}/api{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        files: Any = None,
        data: Any = None,
    ) -> Any:
        """Единая точка HTTP-вызова с разбором ошибок."""
        url = self._url(path)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                verify=settings.api_verify_ssl,
                trust_env=False,  # прокси LLM не должен влиять на внутренний API
                follow_redirects=True,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    files=files,
                    data=data,
                    headers=self._headers,
                )
        except httpx.RequestError as exc:
            raise AcceleratorAPIError(f"Сеть недоступна ({url}): {exc}") from exc

        if response.status_code in (401, 403):
            raise AcceleratorAuthError(
                f"Нет доступа ({response.status_code}) к {path}: токен невалиден или протух"
            )
        if response.status_code == 404:
            raise AcceleratorNotFoundError(f"Не найдено: {path}")
        if response.status_code >= 400:
            raise AcceleratorAPIError(
                f"HTTP {response.status_code} на {path}: {response.text[:500]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # ── проекты заказчика ───────────────────────────────────────────────────

    async def list_my_projects(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """GET /api/customer/projects/v2 — проекты ТЕКУЩЕГО заказчика.

        Бэкенд сам фильтрует по владельцу из JWT — чужие проекты недоступны.

        Returns:
            Конверт: {"meta": {...}, "links": {...}, "items": [ProjectRead, ...]}
        """
        return await self._request(
            "GET",
            "/customer/projects/v2",
            params={"page": page, "per_page": per_page},
        )

    async def get_my_projects_flat(self) -> list[dict]:
        """GET /api/customer/projects — плоский список проектов заказчика."""
        data = await self._request("GET", "/customer/projects")
        return data or []

    async def get_project(self, project_id: int) -> dict | None:
        """Возвращает проект заказчика по id (из его же списка).

        Отдельной ручки «проект по id для заказчика» в API нет,
        поэтому берём из списка своих проектов — заодно проверяем владение.
        """
        for project in await self.get_my_projects_flat():
            if int(project.get("id", 0)) == int(project_id):
                return project
        return None

    async def create_project(self, payload: dict) -> dict:
        """POST /api/customer/create-project — создать проект.

        Args:
            payload: ProjectCreate — title, description, budget, execution_days,
                required_specialists[{profession_id, count}], files[{...}].

        Returns:
            ProjectRead созданного проекта (в т.ч. id).
        """
        return await self._request("POST", "/customer/create-project", json=payload)

    async def update_project(self, project_id: int, payload: dict) -> dict:
        """PATCH /api/customer/{project_id} — обновить проект.

        Принимает ProjectUpdate; в т.ч. files[] — так к проекту крепится
        сгенерированный файл ТЗ (после upload_project_file).
        """
        return await self._request("PATCH", f"/customer/{project_id}", json=payload)

    async def upload_project_file(
        self,
        file_name: str,
        content: bytes,
        mime_type: str = "text/markdown",
    ) -> dict:
        """POST /api/customer/upload-project-file — залить файл, получить file_url.

        Ручка НЕ привязывает файл к проекту (в ней нет project_id) — она только
        сохраняет и возвращает метаданные. Привязка — вторым шагом через
        update_project(files=[...]).
        """
        files = {"file": (file_name, content, mime_type)}
        return await self._request("POST", "/customer/upload-project-file", files=files)

    async def attach_file_to_project(
        self,
        project_id: int,
        file_name: str,
        content: bytes,
        mime_type: str = "text/markdown",
        *,
        keep_existing: bool = True,
    ) -> dict:
        """Полный цикл привязки файла: upload → PATCH files[].

        Args:
            keep_existing: дописать к текущим файлам проекта, а не затереть их.
        """
        uploaded = await self.upload_project_file(file_name, content, mime_type)
        file_url = uploaded.get("file_url") or uploaded.get("url")
        if not file_url:
            raise AcceleratorAPIError(f"Ответ upload-project-file без file_url: {uploaded}")

        new_file = {
            "file_name": file_name,
            "file_url": file_url,
            "mime_type": mime_type,
        }

        files_payload = [new_file]
        if keep_existing:
            project = await self.get_project(project_id)
            existing = [
                {
                    "file_name": f["file_name"],
                    "file_url": f["file_url"],
                    "mime_type": f.get("mime_type"),
                }
                for f in (project or {}).get("files", [])
                if f.get("file_url") != file_url
            ]
            files_payload = existing + [new_file]

        return await self.update_project(project_id, {"files": files_payload})

    # ── специалисты и справочники ───────────────────────────────────────────

    async def search_interns(
        self,
        *,
        profession_ids: list[int] | None = None,
        stack_ids: list[int] | None = None,
        exclude_ids: list[int] | None = None,
        page: int = 1,
        per_page: int = 30,
        sort: str = "created_at_desc",
        is_active: bool = True,
    ) -> list[dict]:
        """GET /api/public/interns/v2 — специалисты по фильтрам.

        `exclude_ids` в API пока НЕТ — исключаем на клиенте (для «подобрать ещё»).
        Матч по стекам — ANY (как в бэке): достаточно одного совпадения.

        Returns:
            Список InternProfileRead (уже без исключённых).
        """
        params: list[tuple[str, Any]] = [
            ("page", page),
            ("per_page", per_page),
            ("sort", sort),
            ("is_active", str(is_active).lower()),
        ]
        for pid in profession_ids or []:
            params.append(("profession_ids", pid))
        for sid in stack_ids or []:
            params.append(("stack_ids", sid))

        envelope = await self._request("GET", "/public/interns/v2", params=params)
        items = (envelope or {}).get("items", [])

        excluded = set(exclude_ids or [])
        if not excluded:
            return items
        return [i for i in items if int(i.get("id", 0)) not in excluded]

    async def get_intern(self, intern_id: int) -> dict:
        """GET /api/public/interns/{intern_id} — полный профиль специалиста."""
        return await self._request("GET", f"/public/interns/{intern_id}")

    async def add_candidates(self, project_id: int, intern_ids: list[int]) -> dict:
        """POST /api/projects/{id}/candidates — записать подборку пачкой (POTENTIAL).

        Специалисты попадают в проект как «наброски» (им пока НЕ видны), заказчик
        потом приглашает их отдельно. Ручка идемпотентна и не падает из-за одного
        плохого id — создаёт всех, кого может.

        Args:
            intern_ids: id специалистов, 1..50. Дубли бэкенд убирает сам.

        Returns:
            {"created": [id, ...], "skipped": [{"intern_id", "reason", "message"}, ...]}
            reason: not_found | already_responded | already_invited.
        """
        return await self._request(
            "POST",
            f"/projects/{project_id}/candidates",
            json={"intern_ids": intern_ids},
        )

    async def list_project_invitations(self, project_id: int) -> list[dict]:
        """GET /api/projects/{id}/invitations — приглашения проекта (для заказчика).

        Возвращает ВСЕ статусы, включая наброски:
        `potential` (претенденты) | `invited` | `accepted` | `declined`.
        Нужно для краткой сводки проекта при доработке.

        Returns:
            Список ProjectInvitationRead (может быть пустым).
        """
        data = await self._request("GET", f"/projects/{project_id}/invitations")
        return data or []

    async def list_professions(self) -> list[dict]:
        """GET /api/public/professions — справочник профессий."""
        return await self._request("GET", "/public/professions") or []

    async def list_stacks(self) -> list[dict]:
        """GET /api/public/stacks — справочник стеков."""
        return await self._request("GET", "/public/stacks") or []

    # ── файлы проекта ───────────────────────────────────────────────────────

    async def download_file(self, file_url: str) -> bytes:
        """Скачивает файл проекта по file_url (с файловым кешем).

        Нужно, чтобы поднять текст существующего ТЗ в контекст диалога
        при доработке проекта.
        """
        url = file_url if file_url.startswith("http") else f"{self._base}{file_url}"

        cache_dir = settings.doc_cache_dir_path
        cached = cache_dir / hashlib.sha256(url.encode()).hexdigest()

        # ВСЯ файловая возня (mkdir + exists + read) синхронная и блокирует event
        # loop — LangGraph такое отлавливает и роняет узел. Уводим в один поток.
        hit = await asyncio.to_thread(_read_cache, cache_dir, cached)
        if hit is not None:
            return hit

        try:
            async with httpx.AsyncClient(
                timeout=settings.doc_download_timeout,
                verify=settings.api_verify_ssl,
                trust_env=False,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url, headers=self._headers) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > settings.doc_download_max_bytes:
                            limit_mb = settings.doc_download_max_bytes // (1024 * 1024)
                            raise DocumentTooLargeError(
                                f"Файл превысил лимит {limit_mb} МБ"
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
        except DocumentTooLargeError:
            raise
        except httpx.HTTPStatusError as exc:
            raise DocumentDownloadError(
                f"HTTP {exc.response.status_code} при скачивании {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise DocumentDownloadError(f"Сетевая ошибка при скачивании {url}: {exc}") from exc

        await asyncio.to_thread(cached.write_bytes, data)
        return data


def _read_cache(cache_dir: Path, cached: Path) -> bytes | None:
    """Синхронная работа с ФС в ОДНОМ месте: создать каталог кеша и прочитать
    файл, если он уже есть.

    Вынесено отдельной функцией, чтобы целиком уходить в asyncio.to_thread:
    mkdir/exists/read блокируют event loop, а в async-узле это падает.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    if cached.exists():
        return cached.read_bytes()
    return None


def cache_path_for(url: str) -> Path:
    """Путь в кеше для данного URL (для отладки)."""
    return settings.doc_cache_dir_path / hashlib.sha256(url.encode()).hexdigest()