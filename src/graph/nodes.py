"""Узлы графа — тонкая обёртка над сервисами, без бизнес-логики внутри.

Поток:
    guard → load_projects → select_project → [create | edit]
      create: ask_questions ⇄ more_or_generate → generate_spec (creative|strict)
      edit:   load_spec (скрытый контекст) → ask_questions ⇄ ... → refine_spec
    → confirm_spec → persist_project (create: POST / edit: PATCH + файл ТЗ)
    → match_team → present_team → presentation → finalize
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from src.api.client import AcceleratorAPI
from src.api.documents import extract_text, pick_spec_file
from src.core.config import settings
from src.graph.interrupts import (
    KIND_ASK_QUESTIONS,
    KIND_CONFIRM_SPEC,
    KIND_MORE_OR_GENERATE,
    KIND_SELECT_PROJECT,
    KIND_TEAM_READY,
    Ask,
    Choice,
    ask,
    render_choices,
    resolve,
)
from src.graph.state import AgentState, Context
from src.presentation.service import build_presentation
from src.requirements_flow.service import assess_info, next_questions
from src.team.service import collect_candidate_ids, match_team, merge_team
from src.techspec.service import generate_spec, refine_spec, spec_file_name
from src.utils.llm_gen import LLMOverloadedError

log = logging.getLogger(__name__)

FINISHED_MESSAGE = (
    "По этому проекту я всё отработал: ТЗ готово и команда подобрана. "
    "Чтобы продолжить — начните новый чат: там я снова покажу ваши проекты "
    "и мы либо доработаем этот, либо создадим новый."
)


# ─── вспомогательное ────────────────────────────────────────────────────────

def _runtime_params(runtime: Runtime[Context]) -> dict:
    """Достаёт параметры run'а. Токен НИКОГДА не берётся из state (он в чекпоинте).

    Ищем в двух местах, потому что передать его можно по-разному:
      1. context=  — нативный способ LangGraph 1.x (`graph.ainvoke(..., context={...})`);
      2. config["configurable"] — так шлёт SDK/Studio (`runs.stream(config={"configurable": {...}})`).
    Fallback — DEV_USER_TOKEN из .env, только для локальной отладки.
    """
    params: dict = dict(runtime.context or {})

    if not params.get("user_token"):
        try:
            configurable = (get_config() or {}).get("configurable") or {}
        except Exception:  # noqa: BLE001
            configurable = {}
        for key in ("user_token", "api_base_url"):
            if not params.get(key) and configurable.get(key):
                params[key] = configurable[key]

    if not params.get("user_token") and settings.dev_user_token:
        params["user_token"] = settings.dev_user_token

    return params


def _api(runtime: Runtime[Context]) -> AcceleratorAPI:
    """Собирает API-клиент. Токен — из context/configurable, НЕ из state."""
    params = _runtime_params(runtime)
    token = params.get("user_token")
    if not token:
        raise RuntimeError(
            "Нет токена заказчика. Передайте user_token — либо в context "
            '(graph.ainvoke(..., context={"user_token": "<JWT>"})), либо в '
            'config["configurable"]["user_token"] (так шлёт SDK/Studio). '
            "Для локальной отладки можно задать DEV_USER_TOKEN в .env."
        )
    return AcceleratorAPI(token, base_url=params.get("api_base_url"))


def _skip_on_error(state: AgentState) -> bool:
    """Проверяет, что предыдущий узел уже зафиксировал ошибку."""
    return bool(state.get("error"))


def _dialog(state: AgentState) -> list[dict]:
    """История диалога в простом виде для промптов."""
    out: list[dict] = []
    for m in state.get("messages") or []:
        role = getattr(m, "type", None)
        content = getattr(m, "content", "")
        if not content:
            continue
        out.append(
            {
                "role": "user" if role == "human" else "assistant",
                "content": content if isinstance(content, str) else str(content),
            }
        )
    return out


def _say(text: str) -> dict:
    """Реплика ассистента в диалог."""
    return {"messages": [AIMessage(content=text)]}


# ─── 0. guard: чат уже отработал ────────────────────────────────────────────

async def guard_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Если по проекту всё пройдено — детерминированно просим завести новый чат."""
    if state.get("finished"):
        return _say(FINISHED_MESSAGE)
    return {}


# ─── 1. проекты заказчика ───────────────────────────────────────────────────

async def load_projects_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Тянет проекты ТЕКУЩЕГО заказчика (API фильтрует по владельцу из JWT)."""
    if state.get("projects"):
        return {}
    try:
        api = _api(runtime)
        projects = await api.get_my_projects_flat()
        log.info("Проектов у заказчика: %d", len(projects))
        return {"projects": projects}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось получить ваши проекты: {exc}"}


async def select_project_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Пауза: доработать существующий проект или создать новый."""
    if _skip_on_error(state) or state.get("mode"):
        return {}

    projects = state.get("projects") or []

    choices = [Choice(id="new", title="Создать новый проект")]
    choices += [
        Choice(
            id=int(p["id"]),
            title=p.get("title") or f"Проект #{p['id']}",
            meta={"status": p.get("status"), "has_spec": bool(p.get("files"))},
        )
        for p in projects
    ]

    if projects:
        message = (
            f"Вижу ваши проекты — их {len(projects)}:\n"
            f"{render_choices(choices[1:])}\n\n"
            "Доработаем один из них или создадим новый?"
        )
    else:
        message = (
            "Проектов у вас пока нет. Создадим первый — "
            "я помогу собрать техническое задание и подобрать команду."
        )

    reply = ask(Ask(kind=KIND_SELECT_PROJECT, message=message, choices=choices))
    choice = await resolve(reply, choices)

    if choice == "new":
        return {"mode": "create", "messages": [AIMessage(content="Создаём новый проект.")]}

    if isinstance(choice, int):
        title = next(c.title for c in choices if c.id == choice)
        return {
            "mode": "edit",
            "target_project_id": choice,
            "messages": [AIMessage(content=f"Работаем с проектом «{title}».")],
        }

    # Не разобрали — переспрашиваем (ребро ведёт обратно в этот же узел).
    return {
        "messages": [
            AIMessage(content="Не понял выбор. Назовите проект из списка или скажите «новый».")
        ]
    }


# ─── 2. режим edit: поднять старое ТЗ в скрытый контекст ────────────────────

async def load_spec_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Скачивает файл ТЗ проекта и кладёт его текст в скрытый контекст.

    Заказчик этого в чате НЕ видит — ИИ просто ведёт себя так, будто уже
    знаком с проектом.
    """
    if _skip_on_error(state) or state.get("existing_spec_text") is not None:
        return {}

    project_id = state.get("target_project_id")
    try:
        api = _api(runtime)
        project = await api.get_project(int(project_id))
        if not project:
            return {"error": f"Проект #{project_id} не найден среди ваших"}

        spec_file = pick_spec_file(project.get("files") or [])
        if not spec_file:
            log.info("У проекта #%s нет файла ТЗ — работаем от описания", project_id)
            return {
                "existing_spec_text": (project.get("description") or "").strip(),
                "spec_title": project.get("title"),
            }

        data = await api.download_file(spec_file["file_url"])
        text = extract_text(data, spec_file.get("file_name", ""))
        log.info(
            "ТЗ проекта #%s поднято в контекст: %d символов из %s",
            project_id,
            len(text),
            spec_file.get("file_name"),
        )
        return {
            "existing_spec_text": text,
            "existing_spec_file": spec_file,
            "spec_title": project.get("title"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось прочитать ТЗ проекта: {exc}"}


# ─── 3. сбор требований ─────────────────────────────────────────────────────

async def ask_questions_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Задаёт вопросы одной репликой и ждёт ответ свободным текстом.

    Никаких choices — значит фронт покажет одно поле ввода.
    Вопросы приходят ВНУТРИ message: модель формулирует готовую реплику.
    """
    if _skip_on_error(state):
        return {}

    try:
        batch = await next_questions(
            _dialog(state),
            spec_context=state.get("existing_spec_text"),
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось сформулировать вопросы: {exc}"}

    if not batch.has_questions or not batch.message.strip():
        return {"ready_to_generate": True}

    reply = ask(Ask(kind=KIND_ASK_QUESTIONS, message=batch.message))

    from langchain_core.messages import HumanMessage

    return {
        "messages": [
            AIMessage(content=batch.message),
            HumanMessage(content=reply.text or "(без ответа)"),
        ],
        "question_rounds": int(state.get("question_rounds") or 0) + 1,
    }


async def more_or_generate_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Оценивает собранное и спрашивает: ещё вопросы или уже писать ТЗ?

    Ключевое требование продукта: не утомлять. Как только минимума хватает —
    сразу предлагаем «давайте я сам напишу».
    """
    if _skip_on_error(state) or state.get("ready_to_generate"):
        return {}

    try:
        assessment = await assess_info(
            _dialog(state),
            spec_context=state.get("existing_spec_text"),
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось оценить собранную информацию: {exc}"}

    updates: dict[str, Any] = {
        "coverage": assessment.coverage,
        "missing_topics": assessment.missing_topics,
    }

    rounds = int(state.get("question_rounds") or 0)

    # Минимума ещё нет и вопросов задали мало — молча идём спрашивать дальше.
    if not assessment.can_generate and rounds < settings.min_question_rounds + 1:
        return updates

    if assessment.coverage == "rich" or not assessment.missing_topics:
        updates["ready_to_generate"] = True
        return updates

    choices = [
        Choice(id="generate", title="Составьте ТЗ сами"),
        Choice(id="more", title="Задайте ещё вопросы"),
    ]
    message = (
        "Основное я понял. Могу задать ещё пару уточняющих вопросов — "
        "тогда ТЗ будет точнее. Или соберу техническое задание сам из того, "
        "что есть, а вы потом поправите. Как удобнее?"
    )

    reply = ask(
        Ask(
            kind=KIND_MORE_OR_GENERATE,
            message=message,
            choices=choices,
            data={"missing_topics": assessment.missing_topics},
        )
    )
    choice = await resolve(reply, choices)

    # Не разобрали ответ — не мучаем вопросами, пишем ТЗ (дефолт «не утомлять»).
    updates["ready_to_generate"] = choice != "more"
    return updates


# ─── 4. генерация / правка ТЗ ───────────────────────────────────────────────

async def generate_spec_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Пишет ТЗ с нуля.

    Режим выбирается по объёму собранного:
      thin/workable → creative: ИИ достраивает продукт сам (всё придуманное — в допущения);
      rich          → strict:   ИИ оформляет сказанное, ничего не придумывая.
    """
    if _skip_on_error(state):
        return {}

    coverage = state.get("coverage") or "workable"
    creative = coverage != "rich"

    try:
        spec = await generate_spec(
            _dialog(state),
            creative=creative,
            missing_topics=state.get("missing_topics") or [],
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось составить ТЗ: {exc}"}

    return {
        "spec_title": spec.title,
        "spec_summary": spec.summary,
        "spec_text": spec.tech_spec_text,
        "spec_assumptions": spec.assumptions,
        "roles_needed": spec.roles_needed,
    }


async def refine_spec_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Правит существующее ТЗ по замечаниям — новая версия документа."""
    if _skip_on_error(state):
        return {}

    current = state.get("existing_spec_text") or ""
    if not current.strip():
        # ТЗ у проекта не было — пишем с нуля.
        return await generate_spec_node(state, runtime)

    try:
        refined = await refine_spec(current, _dialog(state))
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось обновить ТЗ: {exc}"}

    return {
        "spec_text": refined.tech_spec_text,
        "spec_changes": refined.change_summary,
        "roles_needed": refined.roles_needed or state.get("roles_needed") or [],
        "spec_summary": state.get("spec_summary")
        or "Обновлённое техническое задание проекта",
    }


async def confirm_spec_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Показывает ТЗ и ждёт подтверждения (или замечаний).

    data.tech_spec_text — тот же документ, что упомянут в message: фронт может
    отрисовать его красиво, но диалог осмыслен и без этого.
    """
    if _skip_on_error(state):
        return {}

    changes = state.get("spec_changes") or []
    assumptions = state.get("spec_assumptions") or []
    title = state.get("spec_title") or "проект"

    if changes:
        message = f"Обновил техническое задание по проекту «{title}». Что изменилось:\n"
        message += "\n".join(f"— {c}" for c in changes)
    else:
        message = f"Готово, техническое задание по проекту «{title}» составлено."
        if assumptions:
            message += (
                "\n\nЧасть деталей вы не уточняли — я взял разумные варианты, "
                "проверьте их:\n" + "\n".join(f"— {a}" for a in assumptions)
            )
    message += "\n\nВсё верно — подбираю команду. Или напишите, что поправить."

    choices = [
        Choice(id="ok", title="Всё верно, подбирайте команду"),
        Choice(id="edit", title="Нужны правки"),
    ]

    reply = ask(
        Ask(
            kind=KIND_CONFIRM_SPEC,
            message=message,
            choices=choices,
            data={
                "title": state.get("spec_title"),
                "summary": state.get("spec_summary"),
                "tech_spec_text": state.get("spec_text"),
                "assumptions": assumptions,
                "changes": changes,
                "roles_needed": state.get("roles_needed") or [],
            },
        )
    )
    choice = await resolve(reply, choices)

    if choice == "ok":
        return {
            "spec_confirmed": True,
            "messages": [AIMessage(content="Принято. Подбираю команду.")],
        }

    from langchain_core.messages import HumanMessage

    # Кнопка «Нужны правки» ИЛИ свободный текст с замечаниями — круг правки.
    feedback = reply.text or "Нужны правки"
    return {
        "spec_confirmed": False,
        "existing_spec_text": state.get("spec_text"),
        "spec_changes": [],
        "messages": [HumanMessage(content=feedback)],
    }


# ─── 5. сохранение проекта + файл ТЗ ────────────────────────────────────────

async def persist_project_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Создаёт проект (create) или обновляет (edit) и крепит файл ТЗ.

    В режиме create проект появляется в БД ТОЛЬКО здесь — когда собраны все
    поля и ТЗ подтверждено. Никаких пустышек-черновиков.
    """
    if _skip_on_error(state):
        return {}

    api = _api(runtime)
    title = state.get("spec_title") or "Проект без названия"
    description = state.get("spec_summary") or ""
    spec_text = state.get("spec_text") or ""

    try:
        project_id = state.get("target_project_id")

        if state.get("mode") == "create":
            created = await api.create_project(
                {
                    "title": title[:255],
                    "description": description,
                    "required_specialists": [],
                    "files": [],
                }
            )
            project_id = int(created["id"])
            log.info("Проект создан: #%s «%s»", project_id, title)
        else:
            await api.update_project(
                int(project_id),
                {"title": title[:255], "description": description},
            )
            log.info("Проект обновлён: #%s", project_id)

        version = 2 if state.get("mode") == "edit" else 1
        file_name = spec_file_name(title, version=version)
        updated = await api.attach_file_to_project(
            int(project_id),
            file_name,
            spec_text.encode("utf-8"),
            mime_type="text/markdown",
        )
        file_url = next(
            (
                f["file_url"]
                for f in (updated or {}).get("files", [])
                if f.get("file_name") == file_name
            ),
            None,
        )
        log.info("ТЗ прикреплено к проекту #%s: %s", project_id, file_name)

        return {
            "target_project_id": project_id,
            "spec_file_url": file_url,
            "messages": [
                AIMessage(
                    content=f"Техническое задание сохранено в проекте «{title}»."
                )
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось сохранить проект: {exc}"}


# ─── 6. подбор команды (в state, БЕЗ записи в БД) ───────────────────────────

async def match_team_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Подбирает специалистов под роли из ТЗ. Результат — в state."""
    if _skip_on_error(state):
        return {}

    try:
        api = _api(runtime)
        team = await match_team(
            state.get("roles_needed") or [],
            state.get("spec_text") or "",
            state.get("spec_summary") or "",
            api,
            exclude_ids=state.get("candidate_ids") or [],
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось подобрать команду: {exc}"}

    merged = merge_team(list(state.get("team") or []), team)
    return {
        "team": merged,
        "candidate_ids": collect_candidate_ids(merged),
    }


async def present_team_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Показывает подборку и предлагает добрать ещё."""
    if _skip_on_error(state):
        return {}

    team = state.get("team") or []
    total = sum(len(b.get("candidates", [])) for b in team)

    lines = [f"Подобрал специалистов под проект — всего {total}."]
    for block in team:
        names = ", ".join(c["name"] for c in block.get("candidates", []))
        lines.append(
            f"— {block['role']}: {names}"
            if names
            else f"— {block['role']}: подходящих на платформе пока нет"
        )
    lines.append(
        "\nМогу подобрать ещё кандидатов на эти же роли — тех, кто уже в списке, "
        "повторять не буду."
    )

    choices = [
        Choice(id="done", title="Достаточно"),
        Choice(id="more", title="Подобрать ещё"),
    ]

    reply = ask(
        Ask(
            kind=KIND_TEAM_READY,
            message="\n".join(lines),
            choices=choices,
            data={"team": team},
        )
    )
    choice = await resolve(reply, choices)

    if choice == "more":
        return {
            "wants_more_candidates": True,
            "messages": [AIMessage(content="Ищу ещё кандидатов.")],
        }
    return {
        "wants_more_candidates": False,
        "messages": [AIMessage(content="Хорошо, подборку зафиксировал.")],
    }


# ─── 7. презентация (бизнес-логика; включается флагом) ──────────────────────

async def presentation_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Собирает презентацию проекта и крепит её к проекту.

    Провайдер — PRESENTATION_PROVIDER (off | local | gamma).
    При off узел ничего не делает.
    """
    if _skip_on_error(state):
        return {}
    if (settings.presentation_provider or "off").lower() == "off":
        return {}

    try:
        api = _api(runtime)
        result = await build_presentation(
            state.get("spec_text") or "",
            state.get("roles_needed") or [],
            int(state["target_project_id"]),
            api,
            file_stem=f"presentation_{state.get('target_project_id')}",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Презентация не собралась: %s", exc)
        return {}

    if result.error:
        return {"presentation": result.model_dump()}

    return {
        "presentation": result.model_dump(),
        "messages": [AIMessage(content="Презентация проекта готова и приложена.")],
    }


# ─── 8. финал ───────────────────────────────────────────────────────────────

async def finalize_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Закрывает чат: дальше — только новый."""
    if _skip_on_error(state):
        return {
            "finished": True,
            "messages": [
                AIMessage(
                    content=f"Не смог довести до конца: {state.get('error')}"
                )
            ],
        }

    team = state.get("team") or []
    total = sum(len(b.get("candidates", [])) for b in team)
    title = state.get("spec_title") or "проект"

    message = (
        f"Готово по проекту «{title}»:\n"
        f"— техническое задание составлено и приложено к проекту;\n"
        f"— подобрано специалистов: {total}.\n\n"
        "Этот чат своё дело сделал. Чтобы доработать проект или начать новый — "
        "откройте новый чат."
    )
    return {"finished": True, "messages": [AIMessage(content=message)]}
