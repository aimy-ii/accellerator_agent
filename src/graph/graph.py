"""Граф ИИ-ассистента заказчика.

    __start__
       │
    guard ──(чат уже отработал)──────────────────────────────────► __end__
       │
    load_projects → select_project ⇄ (выбор не разобран)
       ├─ create ─► ask_questions ⇄ more_or_generate ─► generate_spec ─┐
       └─ edit  ──► load_spec ─► ask_questions ⇄ more_or_generate ─► refine_spec ─┤
                                                                                  │
                              ┌───────────────────────────────────────────────────┘
                              ▼
                       confirm_spec ──(нужны правки)──► refine_spec
                              │ (подтверждено)
                       persist_project (create: POST / edit: PATCH + файл ТЗ)
                              ▼
                       match_team → present_team ──(подобрать ещё)──► match_team
                              │ (достаточно)
                       presentation (off|local|gamma)
                              ▼
                          finalize → __end__

Ошибка в любом узле (state["error"]) → сразу finalize.

ВАЖНО про рёбра: у каждого add_conditional_edges третьим аргументом идёт КАРТА
возможных назначений. Без неё LangGraph не знает статически, куда ведёт роутер,
и Studio рисует узлы несвязанными. Возвращаемые значения роутеров типизированы
через Literal — это и документация, и защита от опечатки в имени узла.
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import StateGraph

from src.graph.nodes import (
    ask_questions_node,
    confirm_spec_node,
    finalize_node,
    generate_spec_node,
    guard_node,
    load_projects_node,
    load_spec_node,
    match_team_node,
    more_or_generate_node,
    persist_project_node,
    present_team_node,
    presentation_node,
    refine_spec_node,
    select_project_node,
)
from src.graph.state import AgentState, Context


def _after_guard(state: AgentState) -> Literal["load_projects", "__end__"]:
    """Чат уже отработал — новых запусков не делаем."""
    return "__end__" if state.get("finished") else "load_projects"


def _after_load_projects(state: AgentState) -> Literal["select_project", "finalize"]:
    """Не смогли получить проекты — сразу в финал с ошибкой."""
    return "finalize" if state.get("error") else "select_project"


def _after_select(
    state: AgentState,
) -> Literal["load_spec", "ask_questions", "select_project", "finalize"]:
    """Развилка create / edit. Выбор не разобран — переспрашиваем."""
    if state.get("error"):
        return "finalize"
    mode = state.get("mode")
    if mode == "edit":
        return "load_spec"
    if mode == "create":
        return "ask_questions"
    return "select_project"


def _after_load_spec(state: AgentState) -> Literal["ask_questions", "finalize"]:
    """Старое ТЗ поднято в скрытый контекст — идём спрашивать про изменения."""
    return "finalize" if state.get("error") else "ask_questions"


def _after_questions(
    state: AgentState,
) -> Literal["more_or_generate", "generate_spec", "refine_spec", "finalize"]:
    """Спрашивать нечего — сразу к ТЗ, иначе оцениваем достаточность."""
    if state.get("error"):
        return "finalize"
    if state.get("ready_to_generate"):
        return "generate_spec" if state.get("mode") == "create" else "refine_spec"
    return "more_or_generate"


def _after_more_or_generate(
    state: AgentState,
) -> Literal["ask_questions", "generate_spec", "refine_spec", "finalize"]:
    """Заказчик выбрал: писать ТЗ или задать ещё вопросы."""
    if state.get("error"):
        return "finalize"
    if state.get("ready_to_generate"):
        return "generate_spec" if state.get("mode") == "create" else "refine_spec"
    return "ask_questions"


def _after_spec(state: AgentState) -> Literal["confirm_spec", "finalize"]:
    """ТЗ готово — показываем заказчику."""
    return "finalize" if state.get("error") else "confirm_spec"


def _after_confirm(
    state: AgentState,
) -> Literal["persist_project", "refine_spec", "finalize"]:
    """Подтверждено → сохраняем. Есть замечания → новый круг правки."""
    if state.get("error"):
        return "finalize"
    return "persist_project" if state.get("spec_confirmed") else "refine_spec"


def _after_persist(state: AgentState) -> Literal["match_team", "finalize"]:
    """Проект сохранён и ТЗ приложено — подбираем команду."""
    return "finalize" if state.get("error") else "match_team"


def _after_match(state: AgentState) -> Literal["present_team", "finalize"]:
    """Команда подобрана — показываем заказчику."""
    return "finalize" if state.get("error") else "present_team"


def _after_present_team(
    state: AgentState,
) -> Literal["match_team", "presentation", "finalize"]:
    """«Подобрать ещё» → назад в match_team (exclude отсечёт уже показанных)."""
    if state.get("error"):
        return "finalize"
    return "match_team" if state.get("wants_more_candidates") else "presentation"


builder = (
    StateGraph(AgentState, context_schema=Context)
    .add_node("guard", guard_node)
    .add_node("load_projects", load_projects_node)
    .add_node("select_project", select_project_node)
    .add_node("load_spec", load_spec_node)
    .add_node("ask_questions", ask_questions_node)
    .add_node("more_or_generate", more_or_generate_node)
    .add_node("generate_spec", generate_spec_node)
    .add_node("refine_spec", refine_spec_node)
    .add_node("confirm_spec", confirm_spec_node)
    .add_node("persist_project", persist_project_node)
    .add_node("match_team", match_team_node)
    .add_node("present_team", present_team_node)
    .add_node("presentation", presentation_node)
    .add_node("finalize", finalize_node)
    # ─── рёбра: у КАЖДОГО условного перехода явная карта назначений ─────────
    .add_edge("__start__", "guard")
    .add_conditional_edges("guard", _after_guard, ["load_projects", "__end__"])
    .add_conditional_edges(
        "load_projects", _after_load_projects, ["select_project", "finalize"]
    )
    .add_conditional_edges(
        "select_project",
        _after_select,
        ["load_spec", "ask_questions", "select_project", "finalize"],
    )
    .add_conditional_edges(
        "load_spec", _after_load_spec, ["ask_questions", "finalize"]
    )
    .add_conditional_edges(
        "ask_questions",
        _after_questions,
        ["more_or_generate", "generate_spec", "refine_spec", "finalize"],
    )
    .add_conditional_edges(
        "more_or_generate",
        _after_more_or_generate,
        ["ask_questions", "generate_spec", "refine_spec", "finalize"],
    )
    .add_conditional_edges("generate_spec", _after_spec, ["confirm_spec", "finalize"])
    .add_conditional_edges("refine_spec", _after_spec, ["confirm_spec", "finalize"])
    .add_conditional_edges(
        "confirm_spec",
        _after_confirm,
        ["persist_project", "refine_spec", "finalize"],
    )
    .add_conditional_edges("persist_project", _after_persist, ["match_team", "finalize"])
    .add_conditional_edges("match_team", _after_match, ["present_team", "finalize"])
    .add_conditional_edges(
        "present_team",
        _after_present_team,
        ["match_team", "presentation", "finalize"],
    )
    .add_edge("presentation", "finalize")
    .add_edge("finalize", "__end__")
)

graph = builder.compile(name="Accelerator Customer Agent")
