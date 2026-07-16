"""Граф ИИ-ассистента заказчика.

    __start__
       │
    guard ──(чат уже отработал)──────────────────────────────► __end__
       │
    load_projects → select_project ⇄ (выбор не разобран)
       ├─ create ─► ask_questions ⇄ more_or_generate ─┬─► create_project (секунда) ─┐
       │                                              └─► generate_spec  (минута)  ─┤
       └─ edit  ──► load_spec ─► ask_questions ⇄ more_or_generate ──► refine_spec ──┤
                                                                                         │
                              ┌──────────────────────────────────────────────────────────┘
                              ▼
                       confirm_spec ──(нужны правки)──► refine_spec
                              │ (подтверждено)
                              ├──────────► attach_spec   (файл ТЗ → проект)  ┐
                              └──────────► match_team    (подбор спецов)     ┘ ПАРАЛЛЕЛЬНО
                                                  │
                                          present_team ──(подобрать ещё)──► match_team
                                                  │ (достаточно)
                                          save_candidates (POST в проект)
                                                  ▼
                                          presentation → finalize → __end__

Ключевое:
  * проект создаётся ОДНОВРЕМЕННО с написанием ТЗ — он появляется в списке
    заказчика через секунду, а не через минуту;
  * оценка «хватает ли данных» приходит вместе с вопросами (один вызов LLM
    вместо двух) — more_or_generate работает мгновенно, без похода к модели;
  * после подтверждения ТЗ файл крепится к проекту И идёт подбор команды —
    одновременно, а не по очереди (fan-out списком узлов из роутера);
  * у каждого условного перехода ЯВНАЯ карта назначений — иначе Studio не
    нарисует рёбра, а роутер может уехать в несуществующий узел.
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import StateGraph

from src.graph.nodes import (
    ask_questions_node,
    attach_spec_node,
    confirm_spec_node,
    create_project_node,
    finalize_node,
    generate_spec_node,
    guard_node,
    load_projects_node,
    load_spec_node,
    match_team_node,
    more_or_generate_node,
    present_team_node,
    presentation_node,
    refine_spec_node,
    save_candidates_node,
    select_project_node,
)
from src.graph.state import AgentState, Context


def _after_guard(state: AgentState) -> Literal["load_projects", "__end__"]:
    """Чат уже отработал — новых запусков не делаем."""
    return "__end__" if state.get("finished") else "load_projects"


def _after_load_projects(state: AgentState) -> Literal["select_project", "finalize"]:
    """Не смогли получить проекты — в финал с ошибкой."""
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
    """Старое ТЗ поднято в скрытый контекст — спрашиваем про изменения."""
    return "finalize" if state.get("error") else "ask_questions"


def _to_spec(state: AgentState) -> list[str]:
    """Куда идти, когда требования собраны.

    create: проект создаётся И ТЗ пишется ОДНОВРЕМЕННО — заказчик видит проект
    в списке через секунду, а не ждёт минуту, пока ИИ допишет ТЗ.
    edit: проект уже есть, только правим ТЗ.
    """
    if state.get("mode") == "create":
        return ["create_project", "generate_spec"]
    return ["refine_spec"]


def _after_questions(
    state: AgentState,
) -> list[Literal["more_or_generate", "create_project", "generate_spec", "refine_spec", "finalize"]]:
    """Собрали достаточно — заводим проект и пишем ТЗ (параллельно)."""
    if state.get("error"):
        return ["finalize"]
    if state.get("ready_to_generate"):
        return _to_spec(state)
    return ["more_or_generate"]


def _after_more_or_generate(
    state: AgentState,
) -> list[Literal["ask_questions", "create_project", "generate_spec", "refine_spec", "finalize"]]:
    """Заказчик выбрал: писать ТЗ или задать ещё вопросы."""
    if state.get("error"):
        return ["finalize"]
    if state.get("ready_to_generate"):
        return _to_spec(state)
    return ["ask_questions"]


def _after_create_project(state: AgentState) -> Literal["confirm_spec", "finalize"]:
    """Проект создан. Ждём вторую ветку (ТЗ) — сходимся на confirm_spec."""
    return "finalize" if state.get("error") else "confirm_spec"


def _after_spec(state: AgentState) -> Literal["confirm_spec", "finalize"]:
    """ТЗ готово — показываем заказчику."""
    return "finalize" if state.get("error") else "confirm_spec"


def _after_confirm(
    state: AgentState,
) -> list[Literal["attach_spec", "match_team", "refine_spec", "finalize"]]:
    """Подтверждено → крепим ТЗ и подбираем команду ОДНОВРЕМЕННО.

    Возврат списка = fan-out: LangGraph запускает узлы параллельно в одном
    супершаге и сходится на present_team, когда оба закончат.
    """
    if state.get("error"):
        return ["finalize"]
    if not state.get("spec_confirmed"):
        return ["refine_spec"]
    return ["attach_spec", "match_team"]


def _after_attach(state: AgentState) -> Literal["present_team", "finalize"]:
    """Файл ТЗ прикреплён — ждём вторую ветку (подбор) на present_team."""
    return "finalize" if state.get("error") else "present_team"


def _after_match(state: AgentState) -> Literal["present_team", "finalize"]:
    """Команда подобрана — показываем заказчику."""
    return "finalize" if state.get("error") else "present_team"


def _after_present_team(
    state: AgentState,
) -> Literal["match_team", "save_candidates", "finalize"]:
    """«Подобрать ещё» → назад в match_team. «Достаточно» → записать подборку в БД."""
    if state.get("error"):
        return "finalize"
    return "match_team" if state.get("wants_more_candidates") else "save_candidates"


def _after_save_candidates(state: AgentState) -> Literal["presentation", "finalize"]:
    """Подборка записана — дальше презентация (или сразу финал, если она off)."""
    return "finalize" if state.get("error") else "presentation"


builder = (
    StateGraph(AgentState, context_schema=Context)
    .add_node("guard", guard_node)
    .add_node("load_projects", load_projects_node)
    .add_node("select_project", select_project_node)
    .add_node("load_spec", load_spec_node)
    .add_node("ask_questions", ask_questions_node)
    .add_node("more_or_generate", more_or_generate_node)
    .add_node("create_project", create_project_node)
    .add_node("generate_spec", generate_spec_node)
    .add_node("refine_spec", refine_spec_node)
    .add_node("confirm_spec", confirm_spec_node)
    .add_node("attach_spec", attach_spec_node)
    .add_node("match_team", match_team_node)
    .add_node("present_team", present_team_node)
    .add_node("save_candidates", save_candidates_node)
    .add_node("presentation", presentation_node)
    .add_node("finalize", finalize_node)
    # ─── рёбра: у каждого условного перехода явная карта назначений ─────────
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
    .add_conditional_edges("load_spec", _after_load_spec, ["ask_questions", "finalize"])
    # fan-out: проект создаётся ПАРАЛЛЕЛЬНО с написанием ТЗ
    .add_conditional_edges(
        "ask_questions",
        _after_questions,
        ["more_or_generate", "create_project", "generate_spec", "refine_spec", "finalize"],
    )
    .add_conditional_edges(
        "more_or_generate",
        _after_more_or_generate,
        ["ask_questions", "create_project", "generate_spec", "refine_spec", "finalize"],
    )
    .add_conditional_edges(
        "create_project", _after_create_project, ["confirm_spec", "finalize"]
    )
    .add_conditional_edges("generate_spec", _after_spec, ["confirm_spec", "finalize"])
    .add_conditional_edges("refine_spec", _after_spec, ["confirm_spec", "finalize"])
    # fan-out: attach_spec и match_team идут ПАРАЛЛЕЛЬНО
    .add_conditional_edges(
        "confirm_spec",
        _after_confirm,
        ["attach_spec", "match_team", "refine_spec", "finalize"],
    )
    .add_conditional_edges("attach_spec", _after_attach, ["present_team", "finalize"])
    .add_conditional_edges("match_team", _after_match, ["present_team", "finalize"])
    .add_conditional_edges(
        "present_team",
        _after_present_team,
        ["match_team", "save_candidates", "finalize"],
    )
    .add_conditional_edges(
        "save_candidates", _after_save_candidates, ["presentation", "finalize"]
    )
    .add_edge("presentation", "finalize")
    .add_edge("finalize", "__end__")
)

graph = builder.compile(name="Accelerator Customer Agent")