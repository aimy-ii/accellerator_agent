"""Промпты подбора команды.

Из ML-сервиса взято ценное из roles_generation и candidate_selector,
но подбор идёт НЕ через векторную базу, а через фильтры реального API
акселератора — поэтому LLM только (1) мапит роли на справочники платформы
и (2) ранжирует уже отфильтрованный пул.
"""
from __future__ import annotations

import json

MAP_ROLES_SYSTEM = """Ты сопоставляешь роли из ТЗ со справочниками платформы.

На вход: список ролей проекта + ПОЛНЫЕ справочники платформы (профессии и стеки)
с их id. На выход: для каждой роли — какие profession_ids и stack_ids ей соответствуют.

Жёсткие правила:
1. Используй ТОЛЬКО те id, что есть в переданных справочниках. Ничего не выдумывай.
2. Если для роли нет подходящей профессии в справочнике — верни для неё пустой
   profession_ids (её просто не будет на платформе, это нормально).
3. stack_ids подбирай по смыслу роли и технологиям из ТЗ. Бери релевантные, но не
   вали всё подряд: 3-6 штук достаточно.
4. count — сколько таких специалистов нужно проекту (по ТЗ; если не сказано — 1).

Верни строго JSON по схеме RoleQueries. Ничего вне JSON."""


RANK_SYSTEM = """Ты помогаешь заказчику выбрать специалистов под роль в проекте.

На вход: описание проекта, роль и список РЕАЛЬНЫХ кандидатов с платформы
(они уже прошли фильтр по профессии и стеку).

Задача: отобрать лучших под эту роль и объяснить выбор заказчику.

Правила:
1. Работай ТОЛЬКО с переданными кандидатами. Никого не выдумывай, id не сочиняй.
2. match_reason — 1 предложение, человеческим языком, БЕЗ жаргона. Заказчик не технарь.
   Пиши по факту профиля: опыт, подтверждённые технологии, проекты.
3. score — насколько кандидат закрывает роль (1-100). Не ставь всем одинаково.
4. Порядок в списке — от лучшего к худшему.
5. Если кандидаты слабо подходят — всё равно верни лучших из имеющихся,
   но честно снизь score и скажи в match_reason, чего не хватает.

Верни строго JSON по схеме RankedTeam. Ничего вне JSON."""


def map_roles_user_message(
    roles: list[str],
    professions: list[dict],
    stacks: list[dict],
    spec_text: str,
) -> str:
    """User-сообщение для маппинга ролей на справочники."""
    prof_list = [{"id": p["id"], "name": p.get("name", "")} for p in professions]
    stack_list = [{"id": s["id"], "name": s.get("name", "")} for s in stacks]

    return (
        f"Роли проекта: {', '.join(roles) if roles else '(не указаны — выведи из ТЗ)'}\n\n"
        f"ТЗ проекта (фрагмент):\n---\n{spec_text[:6000]}\n---\n\n"
        f"Справочник ПРОФЕССИЙ платформы:\n{json.dumps(prof_list, ensure_ascii=False)}\n\n"
        f"Справочник СТЕКОВ платформы:\n{json.dumps(stack_list, ensure_ascii=False)}\n\n"
        "Сопоставь каждую роль с профессиями и стеками платформы."
    )


def rank_user_message(
    role: str,
    spec_summary: str,
    candidates: list[dict],
    top_n: int,
) -> str:
    """User-сообщение для ранжирования кандидатов под роль."""
    brief = [
        {
            "id": c.get("id"),
            "name": " ".join(
                filter(None, [c.get("first_name"), c.get("last_name")])
            ).strip()
            or c.get("username")
            or f"Специалист #{c.get('id')}",
            "profession": (c.get("profession") or {}).get("name"),
            "stacks": [s.get("name") for s in (c.get("stacks") or [])],
            "hard_skills": (c.get("confirmed_hard_skills") or [])
            + (c.get("declared_hard_skills") or []),
            "about": (c.get("about") or "")[:400],
            "experience": c.get("experience") or c.get("work_experience"),
        }
        for c in candidates
    ]

    return (
        f"Роль: {role}\n\n"
        f"О проекте: {spec_summary}\n\n"
        f"Кандидаты с платформы:\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\n"
        f"Отбери до {top_n} лучших под эту роль."
    )
