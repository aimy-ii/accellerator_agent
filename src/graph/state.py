"""State графа и Context.

ВАЖНО про токен: пользовательский JWT живёт в Context (configurable), а НЕ в State.
State уходит в чекпоинт БД — держать там токен нельзя. Context передаётся на каждый
run заново, в чекпоинт не пишется.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def first_error(current: Optional[str], incoming: Optional[str]) -> Optional[str]:
    """Редьюсер для error: параллельные узлы могут упасть ОДНОВРЕМЕННО.

    Без редьюсера LangGraph бросает InvalidUpdateError («один ключ — одно
    значение за супершаг») и убивает весь граф. Держим первую ошибку:
    она обычно и есть корневая.
    """
    return current or incoming


def merge_flag(current: bool, incoming: bool) -> bool:
    """Редьюсер для флагов, в которые могут писать параллельные ветки."""
    return bool(current or incoming)


class Context(TypedDict, total=False):
    """Параметры run'а. Передаются при каждом вызове графа.

    user_token: JWT заказчика. Инструменты ходят в API акселератора от его лица.
        Обновляется на КАЖДЫЙ запрос — так он не протухает внутри диалога.
    api_base_url: адрес акселератора (по умолчанию из .env).
    """

    user_token: str
    api_base_url: str


class AgentState(TypedDict, total=False):
    """Общая память графа."""

    # ── диалог ──────────────────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── проекты заказчика (шаг 1) ───────────────────────────────────────────
    projects: list[dict]            # список проектов заказчика (из API)
    mode: Optional[Literal["create", "edit"]]
    target_project_id: Optional[int]  # при create появляется только после POST

    # ── скрытый контекст существующего ТЗ (режим edit) ──────────────────────
    # Заказчик этого в чате НЕ видит — ИИ просто «уже знаком» с проектом.
    existing_spec_text: Optional[str]
    existing_spec_file: Optional[dict]
    edit_project: Optional[dict]    # проект целиком (для сводки + ролей из БД)

    # что именно делаем с проектом при доработке:
    #   spec — только правим ТЗ; team — только подбираем команду; both — и то, и то.
    edit_intent: Optional[Literal["spec", "team", "both"]]

    # ── сбор требований ─────────────────────────────────────────────────────
    question_rounds: int            # сколько ходов вопросов уже задали
    coverage: Optional[str]         # thin | workable | rich
    can_generate: bool              # хватает ли данных на ТЗ (оценка от LLM)
    missing_topics: list[str]
    ready_to_generate: bool         # заказчик сказал «хватит, пиши ТЗ»

    # ── ТЗ ──────────────────────────────────────────────────────────────────
    spec_title: Optional[str]
    spec_summary: Optional[str]
    spec_text: Optional[str]        # markdown ТЗ (итог)
    spec_assumptions: list[str]
    spec_changes: list[str]         # что изменилось (режим edit)
    roles_needed: list[str]
    roles_changed: bool             # правки ТЗ изменили состав ролей (из refine)
    execution_days: Optional[int]   # ориентировочный срок в календарных днях (из ТЗ)
    spec_file_url: Optional[str]    # ТЗ, прикреплённое к проекту
    spec_confirmed: bool            # заказчик подтвердил ТЗ (роутер читает это, не текст)

    # ── переименование проекта при доработке ТЗ ─────────────────────────────
    proposed_title: Optional[str]   # новое название, если суть сменилась (из refine)
    rename_confirmed: bool          # заказчик согласился переименовать проект

    # ── команда (пока ТОЛЬКО в state, в БД не пишем) ────────────────────────
    team: list[dict]                # [{role, candidates: [...]}, ...]
    candidate_ids: list[int]        # id всех показанных — exclude для «подобрать ещё»
    wants_more_candidates: bool     # заказчик нажал «подобрать ещё»

    # ── презентация ─────────────────────────────────────────────────────────
    presentation: Optional[dict]

    # ── финал ───────────────────────────────────────────────────────────────
    finished: bool                  # все узлы пройдены — просим завести новый чат

    # error пишут ПАРАЛЛЕЛЬНЫЕ узлы (attach_spec ‖ match_team,
    # create_project ‖ generate_spec) — без редьюсера граф падает.
    error: Annotated[Optional[str], first_error]


def new_state_defaults() -> dict[str, Any]:
    """Дефолты для полей-списков, чтобы узлы не падали на None."""
    return {
        "projects": [],
        "missing_topics": [],
        "can_generate": False,
        "spec_assumptions": [],
        "spec_changes": [],
        "roles_needed": [],
        "roles_changed": False,
        "team": [],
        "candidate_ids": [],
        "question_rounds": 0,
        "ready_to_generate": False,
        "spec_confirmed": False,
        "rename_confirmed": False,
        "wants_more_candidates": False,
        "finished": False,
    }