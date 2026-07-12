"""Подбор команды под проект.

Поток (без векторной базы — всё через реальные ручки API):
    роли из ТЗ
      → LLM мапит роли на profession_ids / stack_ids платформы (справочники)
      → GET /public/interns/v2 по фильтрам (ANY по стекам)
      → LLM ранжирует полученный пул и объясняет выбор
      → результат кладётся в state графа (в БД пока НЕ пишем)

«Подобрать ещё» = повторный вызов с exclude_ids (id уже показанных).
"""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.api.client import AcceleratorAPI
from src.core.config import settings
from src.team.models import RankedTeam, RoleQueries
from src.team.prompts import (
    MAP_ROLES_SYSTEM,
    RANK_SYSTEM,
    map_roles_user_message,
    rank_user_message,
)
from src.utils.llm_gen import ainvoke_llm, get_llm

log = logging.getLogger(__name__)


async def map_roles_to_filters(
    roles: list[str],
    spec_text: str,
    api: AcceleratorAPI,
) -> RoleQueries:
    """Переводит роли из ТЗ в фильтры по справочникам платформы."""
    professions = await api.list_professions()
    stacks = await api.list_stacks()

    log.info(
        "Справочники платформы: профессий=%d, стеков=%d",
        len(professions),
        len(stacks),
    )

    async with get_llm(temperature=0.0) as llm:
        structured = llm.with_structured_output(RoleQueries)
        result: RoleQueries = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=MAP_ROLES_SYSTEM),
                HumanMessage(
                    content=map_roles_user_message(roles, professions, stacks, spec_text)
                ),
            ],
        )

    known_professions = {p["id"] for p in professions}
    known_stacks = {s["id"] for s in stacks}
    for role in result.roles:
        role.profession_ids = [i for i in role.profession_ids if i in known_professions]
        role.stack_ids = [i for i in role.stack_ids if i in known_stacks]

    log.info("Роли сопоставлены: %d", len(result.roles))
    return result


async def rank_candidates(
    role: str,
    spec_summary: str,
    candidates: list[dict],
    *,
    top_n: int,
) -> RankedTeam:
    """Ранжирует уже отфильтрованный пул кандидатов под роль."""
    if not candidates:
        return RankedTeam(candidates=[])

    async with get_llm(temperature=0.2) as llm:
        structured = llm.with_structured_output(RankedTeam)
        result: RankedTeam = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=RANK_SYSTEM),
                HumanMessage(
                    content=rank_user_message(role, spec_summary, candidates, top_n)
                ),
            ],
        )

    valid_ids = {int(c.get("id", 0)) for c in candidates}
    result.candidates = [c for c in result.candidates if c.intern_id in valid_ids][:top_n]
    return result


async def match_team(
    roles: list[str],
    spec_text: str,
    spec_summary: str,
    api: AcceleratorAPI,
    *,
    exclude_ids: list[int] | None = None,
) -> list[dict]:
    """Подбирает команду под проект.

    Args:
        exclude_ids: кого не показывать (уже в подборке) — для «подобрать ещё».

    Returns:
        Список блоков по ролям:
        [{"role": str, "profession_ids": [...], "candidates": [
            {"intern_id", "name", "profession", "match_reason", "score", "profile"}
        ]}]
    """
    exclude = list(exclude_ids or [])
    role_queries = await map_roles_to_filters(roles, spec_text, api)

    result: list[dict] = []
    for rq in role_queries.roles:
        pool = await api.search_interns(
            profession_ids=rq.profession_ids or None,
            stack_ids=rq.stack_ids or None,
            exclude_ids=exclude,
            per_page=settings.candidates_pool_size,
        )
        log.info("Роль '%s': пул кандидатов=%d", rq.role, len(pool))

        if not pool:
            result.append(
                {
                    "role": rq.role,
                    "profession_ids": rq.profession_ids,
                    "count": rq.count,
                    "candidates": [],
                    "note": "На платформе пока нет подходящих специалистов по этим фильтрам",
                }
            )
            continue

        top_n = max(settings.candidates_per_role, rq.count)
        ranked = await rank_candidates(rq.role, spec_summary, pool, top_n=top_n)

        by_id = {int(c["id"]): c for c in pool}
        candidates: list[dict] = []
        for rc in ranked.candidates:
            profile = by_id.get(rc.intern_id)
            if not profile:
                continue
            name = " ".join(
                filter(None, [profile.get("first_name"), profile.get("last_name")])
            ).strip() or f"Специалист #{rc.intern_id}"
            candidates.append(
                {
                    "intern_id": rc.intern_id,
                    "name": name,
                    "profession": (profile.get("profession") or {}).get("name"),
                    "match_reason": rc.match_reason,
                    "score": rc.score,
                    "profile": profile,
                }
            )
            exclude.append(rc.intern_id)

        result.append(
            {
                "role": rq.role,
                "profession_ids": rq.profession_ids,
                "count": rq.count,
                "candidates": candidates,
            }
        )

    return result


def collect_candidate_ids(team: list[dict]) -> list[int]:
    """Собирает id всех кандидатов подборки — для exclude при «подобрать ещё»."""
    ids: list[int] = []
    for block in team or []:
        for c in block.get("candidates", []):
            ids.append(int(c["intern_id"]))
    return ids


def merge_team(existing: list[dict], extra: list[dict]) -> list[dict]:
    """Дописывает новых кандидатов к существующей подборке (по ролям)."""
    by_role = {b["role"]: b for b in existing}
    for block in extra:
        role = block["role"]
        if role in by_role:
            by_role[role]["candidates"].extend(block.get("candidates", []))
        else:
            existing.append(block)
    return existing
