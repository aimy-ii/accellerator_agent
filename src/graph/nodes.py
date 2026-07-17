"""Узлы графа — тонкая обёртка над сервисами, без бизнес-логики внутри.

Поток:
    guard → load_projects → select_project
      create: ask_questions ⇄ more_or_generate → create_project → generate_spec
      edit:   load_spec (скрытый контекст) → ask_questions ⇄ ... → refine_spec
    → confirm_spec
        ├─► attach_spec   (файл ТЗ → проект)   ┐ ПАРАЛЛЕЛЬНО
        └─► match_team    (подбор спецов)      ┘
    → present_team → presentation → finalize

Проект создаётся ДО генерации ТЗ — заказчик видит его в списке, пока ИИ пишет.
Каждый шаг публикует прогресс (emit) — фронт видит, что происходит.
Каждый ввод пользователя падает в messages — диалог листается как чат.
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
    KIND_CONFIRM_RENAME,
    KIND_CONFIRM_SPEC,
    KIND_EDIT_INTENT,
    KIND_MORE_OR_GENERATE,
    KIND_SELECT_PROJECT,
    KIND_TEAM_READY,
    Ask,
    Choice,
    ask,
    render_choices,
    resolve,
)
from src.graph.progress import emit, emit_token, progress_for
from src.graph.state import AgentState, Context
from src.presentation.service import build_presentation
from src.requirements_flow.service import next_questions
from src.team.service import collect_candidate_ids, match_team, merge_team
from src.techspec.card import make_card
from src.techspec.render import DOCX_MIME, markdown_to_docx
from src.techspec.service import (
    extract_roles_from_spec,
    generate_spec,
    refine_spec,
    spec_file_name,
)
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

    Источники токена по приоритету:
      1. config["configurable"]["langgraph_auth_user"]["user_token"] — прод: токен
         разложил хук `@auth.authenticate` (src/auth.py) из заголовка Authorization
         Bearer, который фронт шлёт один раз за запрос.
      2. context= / config["configurable"]["user_token"] — совместимость: нативный
         способ LangGraph 1.x (`graph.ainvoke(..., context={...})`) и то, как шлёт
         Studio (`runs.stream(config={"configurable": {...}})`) без нашего auth-хука.
      3. DEV_USER_TOKEN из .env — только для локальной отладки.
    """
    params: dict = dict(runtime.context or {})

    try:
        configurable = (get_config() or {}).get("configurable") or {}
    except Exception:  # noqa: BLE001
        configurable = {}

    # 1. Прод: токен из auth-контекста run'а — наивысший приоритет, перетирает
    # то, что могло прийти в context/configurable напрямую.
    auth_user = configurable.get("langgraph_auth_user")
    auth_token = auth_user.get("user_token") if auth_user is not None else None
    if auth_token:
        params["user_token"] = auth_token

    # 2. Совместимость (Studio / прямой вызов графа) — fallback, если auth-хук
    # токен не положил.
    if not params.get("user_token"):
        for key in ("user_token", "api_base_url"):
            if not params.get(key) and configurable.get(key):
                params[key] = configurable[key]
    elif not params.get("api_base_url") and configurable.get("api_base_url"):
        params["api_base_url"] = configurable["api_base_url"]

    # 3. Локалка.
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


def _turn(question: str, reply: Reply, choices: list[Choice] | None = None) -> list:
    """Пара реплик для истории чата: вопрос агента + ответ пользователя.

    Всё, что пользователь выбрал или ввёл, попадает в messages — чтобы
    диалог можно было пролистать как обычный чат.
    Нажал кнопку → в чате видно название кнопки, а не сырой id.
    """
    from langchain_core.messages import HumanMessage

    said = reply.text.strip()
    if not said and reply.id is not None and choices:
        said = next(
            (c.title for c in choices if str(c.id) == str(reply.id)),
            str(reply.id),
        )
    return [AIMessage(content=question), HumanMessage(content=said or "(без ответа)")]


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
        emit("load_projects", "Смотрю ваши проекты…", "start")
        api = _api(runtime)
        projects = await api.get_my_projects_flat()
        emit("load_projects", f"Нашёл проектов: {len(projects)}")
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
    turn = _turn(message, reply, choices)

    if choice == "new":
        emit("select_project", "Создаём новый проект")
        return {"mode": "create", "messages": turn}

    if isinstance(choice, int):
        title = next(c.title for c in choices if c.id == choice)
        emit("select_project", f"Работаем с проектом «{title}»")
        return {"mode": "edit", "target_project_id": choice, "messages": turn}

    # Не разобрали — переспрашиваем (ребро ведёт обратно в этот же узел).
    return {
        "messages": turn
        + [AIMessage(content="Не понял выбор. Назовите проект из списка или скажите «новый».")]
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
        emit("load_spec", "Поднимаю техническое задание проекта…", "start")
        api = _api(runtime)
        project = await api.get_project(int(project_id))
        if not project:
            return {"error": f"Проект #{project_id} не найден среди ваших"}

        spec_file = pick_spec_file(project.get("files") or [])
        if not spec_file:
            log.info("У проекта #%s нет файла ТЗ — работаем от описания", project_id)
            emit("load_spec", "Техническое задание поднято", "done")
            return {
                "existing_spec_text": (project.get("description") or "").strip(),
                "spec_title": project.get("title"),
                "spec_summary": (project.get("description") or "").strip(),
                "edit_project": project,
            }

        data = await api.download_file(spec_file["file_url"])
        text = await extract_text(data, spec_file.get("file_name", ""))
        log.info(
            "ТЗ проекта #%s поднято в контекст: %d символов из %s",
            project_id,
            len(text),
            spec_file.get("file_name"),
        )
        emit("load_spec", "Техническое задание поднято", "done")
        return {
            "existing_spec_text": text,
            "existing_spec_file": spec_file,
            "spec_title": project.get("title"),
            "spec_summary": (project.get("description") or "").strip(),
            "edit_project": project,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось прочитать ТЗ проекта: {exc}"}


# ─── 2.5. краткая сводка проекта + развилка «что делаем» ────────────────────

def _roles_from_project(project: dict) -> list[str]:
    """Роли из состава специалистов проекта (если он задан в БД)."""
    roles: list[str] = []
    for rs in (project or {}).get("required_specialists") or []:
        name = rs.get("profession_name")
        if name and name not in roles:
            roles.append(name)
    return roles


def build_edit_summary(
    project: dict,
    invitations: list[dict] | None,
    *,
    char_limit: int = 300,
) -> str:
    """Краткая детерминированная сводка проекта для доработки.

    invitations=None → приглашения не удалось получить, счётчики по ним не
    показываем (не путаем «0» с «неизвестно»).
    """
    title = project.get("title") or f"Проект #{project.get('id')}"
    has_spec = bool(pick_spec_file(project.get("files") or []))
    responses = int(project.get("responses_count") or 0)
    required = int(project.get("specialists_count") or 0)

    lines = [f"Проект «{title}»:"]
    lines.append(
        "— техническое задание: "
        + ("файл прикреплён" if has_spec else "файла нет, есть только описание")
    )
    if required:
        lines.append(f"— требуется по проекту: {required} чел.")
    lines.append(f"— откликов от специалистов: {responses}")

    if invitations is not None:
        potential = sum(
            1 for i in invitations if str(i.get("status")).lower() == "potential"
        )
        accepted = sum(
            1 for i in invitations if str(i.get("status")).lower() == "accepted"
        )
        lines.append(f"— претендентов (наброски подборки): {potential}")
        lines.append(f"— в команде (приняли приглашение): {accepted}")

    desc = (project.get("description") or "").strip()
    if desc:
        if len(desc) > char_limit:
            desc = desc[:char_limit].rstrip() + "…"
        lines.append(f"— кратко: {desc}")

    return "\n".join(lines)


async def edit_menu_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Показывает краткую сводку проекта и спрашивает, что с ним делать.

    Развилка: только правка ТЗ / только подбор команды / и то, и другое.
    Для маршрута «только команда» здесь же готовим роли — их берём из состава
    специалистов проекта, а если он пуст (проект заведён ассистентом) — извлекаем
    из текста ТЗ.
    """
    if _skip_on_error(state) or state.get("edit_intent"):
        return {}

    project = state.get("edit_project") or {}

    # Сводка — детерминированная; один запрос за приглашениями (может не быть прав).
    api = _api(runtime)
    invitations: list[dict] | None
    try:
        invitations = await api.list_project_invitations(int(project.get("id")))
    except Exception as exc:  # noqa: BLE001
        log.info("Не удалось получить приглашения проекта для сводки: %s", exc)
        invitations = None

    summary = build_edit_summary(project, invitations)

    choices = [
        Choice(id="spec", title="Доработать техническое задание"),
        Choice(id="team", title="Подобрать команду"),
        Choice(id="both", title="И то, и другое"),
    ]
    message = (
        f"{summary}\n\n"
        "Что делаем с проектом?\n"
        f"{render_choices(choices)}"
    )

    reply = ask(Ask(kind=KIND_EDIT_INTENT, message=message, choices=choices, data={"summary": summary}))
    choice = await resolve(reply, choices)
    turn = _turn(message, reply, choices)

    if choice not in ("spec", "team", "both"):
        # Не разобрали — переспрашиваем (ребро ведёт обратно в этот же узел).
        return {
            "messages": turn
            + [AIMessage(content="Уточните: доработать ТЗ, подобрать команду или и то, и другое?")]
        }

    emit("edit_menu", {"spec": "Дорабатываем ТЗ", "team": "Подбираем команду", "both": "ТЗ и команда"}[choice])

    result: dict = {"edit_intent": choice, "messages": turn}

    if choice == "team":
        # ТЗ не правим — роли нужны сразу, готовим их здесь.
        roles = _roles_from_project(project)
        if not roles:
            try:
                roles = await extract_roles_from_spec(state.get("existing_spec_text") or "")
            except LLMOverloadedError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": f"Не удалось определить состав команды по ТЗ: {exc}"}
        result["roles_needed"] = roles
        # match_team берёт ТЗ как контекст — на этом маршруте это существующее ТЗ.
        result["spec_text"] = state.get("existing_spec_text") or ""

    return result


# ─── 3. сбор требований ─────────────────────────────────────────────────────

async def ask_questions_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Задаёт вопросы одной репликой и ждёт ответ свободным текстом.

    Никаких choices — значит фронт покажет одно поле ввода.
    Вопросы приходят ВНУТРИ message: модель формулирует готовую реплику.
    """
    if _skip_on_error(state):
        return {}

    is_first_round = not state.get("question_rounds")
    thinking_text = (
        "Читаю вашу идею, формулирую первые вопросы…"
        if is_first_round
        else "Читаю ваш ответ, оцениваю, чего ещё не хватает…"
    )

    try:
        emit("ask_questions", thinking_text, "start")
        batch = await next_questions(
            _dialog(state),
            spec_context=state.get("existing_spec_text"),
            on_delta=emit_token,
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось сформулировать вопросы: {exc}"}

    # Оценка пришла тем же вызовом — отдельный поход к модели не нужен.
    assessment = {
        "coverage": batch.coverage,
        "missing_topics": batch.missing_topics,
        "can_generate": batch.can_generate,
    }

    if not batch.has_questions or not batch.message.strip():
        return {**assessment, "ready_to_generate": True}

    emit("ask_questions", "Вопросы готовы", "done")
    reply = ask(Ask(kind=KIND_ASK_QUESTIONS, message=batch.message))

    return {
        **assessment,
        "messages": _turn(batch.message, reply),
        "question_rounds": int(state.get("question_rounds") or 0) + 1,
    }


async def more_or_generate_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Спрашивает: ещё вопросы или уже писать ТЗ.

    LLM здесь НЕ вызывается — оценка достаточности пришла вместе с вопросами
    (ask_questions). Узел только показывает паузу: это мгновенно.
    """
    if _skip_on_error(state) or state.get("ready_to_generate"):
        return {}

    coverage = state.get("coverage") or "workable"

    # Всё раскрыто — спрашивать нечего, сразу к ТЗ.
    if coverage == "rich":
        return {"ready_to_generate": True}

    # ВСЕГДА даём выбор. Решает заказчик, а не наш if: даже если данных мало,
    # он вправе сказать «пиши как есть».
    choices = [
        Choice(id="generate", title="Составьте ТЗ сами"),
        Choice(id="more", title="Задайте ещё вопросы"),
    ]
    if coverage == "thin":
        message = (
            "Идею понял, но деталей пока немного — ТЗ придётся во многом "
            "додумывать за вас (всё придуманное я честно помечу, поправите). "
            "Могу задать ещё пару вопросов — тогда точнее. Или собрать ТЗ "
            "сейчас из того, что есть. Как удобнее?"
        )
    else:
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
            data={"missing_topics": state.get("missing_topics") or []},
        )
    )
    choice = await resolve(reply, choices)

    # Не разобрали ответ — не мучаем вопросами, пишем ТЗ (дефолт «не утомлять»).
    return {
        "ready_to_generate": choice != "more",
        "messages": _turn(message, reply, choices),
    }


# ─── 4. создание проекта (ДО генерации ТЗ) ─────────────────────────────────

async def create_project_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Заводит проект в БД СРАЗУ после сбора требований — до написания ТЗ.

    Заказчик видит проект в списке, пока ИИ ещё пишет техническое задание.
    Файл ТЗ прикрепится позже, отдельным узлом (параллельно с подбором команды).

    В режиме edit проект уже есть — узел ничего не делает.
    """
    if _skip_on_error(state) or state.get("target_project_id"):
        return {}

    try:
        emit("create_project", "Оформляю карточку проекта…", "start")
        card = await make_card(_dialog(state))

        api = _api(runtime)
        created = await api.create_project(
            {
                "title": card.title[:255],
                "description": card.description,
                "required_specialists": [],
                "files": [],
            }
        )
        project_id = int(created["id"])
        emit("create_project", f"Проект «{card.title}» создан")

        return {
            "target_project_id": project_id,
            "spec_title": card.title,
            "spec_summary": card.description,
            "messages": [
                AIMessage(
                    content=f"Создал проект «{card.title}». Теперь пишу техническое задание."
                )
            ],
        }
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось создать проект: {exc}"}


# ─── 5. генерация / правка ТЗ ───────────────────────────────────────────────

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
        emit("generate_spec", "Пишу техническое задание…", "start")
        spec = await generate_spec(
            _dialog(state),
            creative=creative,
            missing_topics=state.get("missing_topics") or [],
            on_delta=emit_token,
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось составить ТЗ: {exc}"}

    emit("generate_spec", "Техническое задание готово", "done")
    return {
        # title/summary приходят из create_project (идёт параллельно) — не перетираем
        "spec_text": spec.tech_spec_text,
        "spec_assumptions": spec.assumptions,
        "roles_needed": spec.roles_needed,
        "messages": [AIMessage(content="Техническое задание готово.")],
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
        emit("refine_spec", "Вношу правки в техническое задание…", "start")
        refined = await refine_spec(
            current,
            _dialog(state),
            current_title=state.get("spec_title") or "",
            on_delta=emit_token,
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось обновить ТЗ: {exc}"}

    # Новое название предлагаем только если оно реально другое (не пустое и не то же).
    current_title = (state.get("spec_title") or "").strip()
    proposed = (refined.proposed_title or "").strip()
    proposed_title = proposed if proposed and proposed != current_title else None

    emit("refine_spec", "Правки внесены", "done")
    return {
        "spec_text": refined.tech_spec_text,
        "spec_changes": refined.change_summary,
        "roles_needed": refined.roles_needed or state.get("roles_needed") or [],
        "roles_changed": bool(refined.roles_changed),
        "proposed_title": proposed_title,
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
    turn = _turn(message, reply, choices)

    if choice == "ok":
        return {
            "spec_confirmed": True,
            "messages": turn + [AIMessage(content="Принято. Сохраняю ТЗ и подбираю команду.")],
        }

    # Кнопка «Нужны правки» ИЛИ свободный текст с замечаниями — круг правки.
    return {
        "spec_confirmed": False,
        "existing_spec_text": state.get("spec_text"),
        "spec_changes": [],
        "messages": turn,
    }


# ─── 5.5. переименование проекта (если суть сменилась) ──────────────────────

async def confirm_rename_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Спрашивает про переименование проекта, если правки сменили его суть.

    Срабатывает ТОЛЬКО когда refine предложил новое название (proposed_title). В
    остальных случаях (создание проекта, правки без смены сути) — проходной узел
    без паузы. Заказчик просил: спросить, но переименовать самим при согласии.
    """
    if _skip_on_error(state):
        return {}

    proposed = (state.get("proposed_title") or "").strip()
    current = (state.get("spec_title") or "").strip()
    if not proposed or proposed == current:
        return {}  # переименовывать нечего — идём дальше без вопроса

    message = (
        f"Из-за правок суть проекта изменилась — название «{current}» больше не "
        f"подходит. Переименовать проект в «{proposed}»?"
    )
    choices = [
        Choice(id="yes", title=f"Да, переименовать в «{proposed}»"),
        Choice(id="no", title="Нет, оставить прежнее"),
    ]

    reply = ask(Ask(kind=KIND_CONFIRM_RENAME, message=message, choices=choices))
    choice = await resolve(reply, choices)
    turn = _turn(message, reply, choices)

    if choice == "yes":
        emit("confirm_rename", f"Переименовываю проект в «{proposed}»")
        return {"rename_confirmed": True, "spec_title": proposed, "messages": turn}

    # «Нет» или непонятный ответ — не рискуем переименованием, оставляем как было.
    return {"rename_confirmed": False, "messages": turn}


# ─── 6. прикрепление файла ТЗ (параллельно с подбором) ──────────────────────

async def attach_spec_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Прикрепляет файл ТЗ к проекту. Идёт ПАРАЛЛЕЛЬНО с подбором команды.

    Проект уже создан (create_project), поэтому здесь только:
      upload файла → PATCH проекта с files[] → заодно освежаем title/описание.
    """
    if _skip_on_error(state):
        return {}

    project_id = state.get("target_project_id")
    if not project_id:
        return {"error": "Проект не создан — некуда прикреплять ТЗ"}

    api = _api(runtime)
    title = state.get("spec_title") or "Проект"
    spec_text = state.get("spec_text") or ""

    try:
        emit("attach_spec", "Сохраняю ТЗ в проект…", "start")

        if state.get("mode") == "edit":
            patch = {"description": state.get("spec_summary") or ""}
            # Заказчик согласился переименовать — обновляем и название проекта.
            if state.get("rename_confirmed") and (state.get("spec_title") or "").strip():
                patch["title"] = state.get("spec_title")
            await api.update_project(int(project_id), patch)

        version = 2 if state.get("mode") == "edit" else 1
        file_name = spec_file_name(title, version=version)

        # Бэкенд принимает docx, а не markdown — конвертируем (pandoc).
        content = await markdown_to_docx(spec_text)

        updated = await api.attach_file_to_project(
            int(project_id),
            file_name,
            content,
            mime_type=DOCX_MIME,
        )
        file_url = next(
            (
                f["file_url"]
                for f in (updated or {}).get("files", [])
                if f.get("file_name") == file_name
            ),
            None,
        )
        emit("attach_spec", f"ТЗ прикреплено: {file_name}")

        return {
            "spec_file_url": file_url,
            "messages": [
                AIMessage(content=f"Техническое задание сохранено в проекте «{title}».")
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось сохранить ТЗ в проект: {exc}"}


# ─── 6. подбор команды (в state, БЕЗ записи в БД) ───────────────────────────

async def match_team_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Подбирает специалистов под роли из ТЗ. Результат — в state."""
    if _skip_on_error(state):
        return {}

    try:
        api = _api(runtime)
        # На маршруте «только команда» refine не запускался — берём существующее ТЗ.
        spec_text = state.get("spec_text") or state.get("existing_spec_text") or ""
        spec_summary = state.get("spec_summary") or ""
        team = await match_team(
            state.get("roles_needed") or [],
            spec_text,
            spec_summary,
            api,
            exclude_ids=state.get("candidate_ids") or [],
            progress=progress_for("match_team"),
        )
    except LLMOverloadedError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось подобрать команду: {exc}"}

    merged = merge_team(list(state.get("team") or []), team)
    emit("match_team", "Команда подобрана", "done")
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
        if names:
            lines.append(f"— {block['role']}: {names}")
        else:
            # Причина у каждой роли своя: профессии нет на платформе / есть,
            # но никто не подошёл / фильтр не собрался.
            note = block.get("note") or "подходящих не нашлось"
            lines.append(f"— {block['role']}: {note.lower()}")
    lines.append(
        "\nМогу подобрать ещё кандидатов на эти же роли — тех, кто уже в списке, "
        "повторять не буду."
    )

    choices = [
        Choice(id="done", title="Достаточно"),
        Choice(id="more", title="Подобрать ещё"),
    ]

    message = "\n".join(lines)
    reply = ask(
        Ask(
            kind=KIND_TEAM_READY,
            message=message,
            choices=choices,
            data={"team": team},
        )
    )
    choice = await resolve(reply, choices)
    turn = _turn(message, reply, choices)

    if choice == "more":
        return {"wants_more_candidates": True, "messages": turn}
    return {"wants_more_candidates": False, "messages": turn}


# ─── 6.5. запись подборки в проект ──────────────────────────────────────────

def _required_specialists_from_team(team: list[dict]) -> list[dict]:
    """Собирает состав специalистов проекта из подобранных ролей.

    Роль → основная профессия (первый profession_id) и нужное количество. Если
    одна профессия встречается в нескольких ролях — количества суммируем. Роли
    без сопоставленной профессии пропускаем.

    Формат под ProjectUpdate.required_specialists: [{profession_id, count}].
    """
    by_prof: dict[int, int] = {}
    for block in team or []:
        pids = block.get("profession_ids") or []
        if not pids:
            continue
        pid = int(pids[0])
        by_prof[pid] = by_prof.get(pid, 0) + int(block.get("count") or 1)
    return [{"profession_id": pid, "count": cnt} for pid, cnt in by_prof.items()]


async def save_candidates_node(state: AgentState, runtime: Runtime[Context]) -> dict:
    """Пишет в проект состав ролей и подобранных претендентов.

    Идёт после подтверждения подборки («достаточно»). В БД проекта попадает:
      1) состав специалистов (required_specialists) — из подобранных ролей, чтобы
         у проекта заполнились «Требуемые специалисты» и счётчик specialists_count;
      2) сами претенденты (статус POTENTIAL) — POST /candidates.

    Роли пишем ПЕРВЫМИ (как при обычном создании: сначала роли проекта, потом
    подбор под них). До этого узла команда жила только в стейте графа.
    """
    if _skip_on_error(state):
        return {}

    project_id = state.get("target_project_id")
    if not project_id:
        return {}

    api = _api(runtime)
    team = state.get("team") or []

    # 1) состав специалистов проекта — из подобранных ролей.
    required = _required_specialists_from_team(team)
    if required:
        try:
            await api.update_project(
                int(project_id), {"required_specialists": required}
            )
            log.info("Состав ролей записан в проект #%s: %s", project_id, required)
        except Exception as exc:  # noqa: BLE001
            # Роли — не критично для показа подборки; прогон не рушим.
            log.error(
                "Не удалось записать состав ролей в проект #%s: %s", project_id, exc
            )

    # 2) претенденты (POTENTIAL).
    intern_ids = collect_candidate_ids(team)
    if not intern_ids:
        # Никого не подобрали — роли (если были) записали, идём дальше.
        return {}

    try:
        emit("save_candidates", "Сохраняю подобранную команду в проект…", "start")
        result = await api.add_candidates(int(project_id), intern_ids)

        created = result.get("created", [])
        skipped = result.get("skipped", [])
        log.info(
            "Подборка записана в проект #%s: создано=%d, пропущено=%d",
            project_id,
            len(created),
            len(skipped),
        )
        if skipped:
            log.info("Пропущены при записи: %s", skipped)

        emit("save_candidates", "Команда сохранена в проект", "done")
        return {
            "messages": [
                AIMessage(
                    content=f"Сохранил подобранную команду в проект — {len(created)} чел."
                )
            ]
        }
    except Exception as exc:  # noqa: BLE001
        # Подборка уже показана заказчику; не рушим весь прогон из-за записи.
        log.error("Не удалось записать подборку в проект #%s: %s", project_id, exc)
        return {
            "messages": [
                AIMessage(
                    content="Подборку показал, но сохранить в проект не удалось — "
                    "попробуйте позже."
                )
            ]
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
        emit("presentation", "Собираю презентацию проекта…", "start")
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

    emit("presentation", "Презентация готова", "done")
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