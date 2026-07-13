"""Подбор команды под проект.

Поток (без векторной базы — всё через реальные ручки API):
    роли из ТЗ (roles_needed)
      → на КАЖДУЮ роль ПАРАЛЛЕЛЬНО:
          LLM: роль → profession_ids / stack_ids платформы (плоская схема)
          GET /public/interns/v2 по фильтрам (тянем ровно count + 1)
          LLM: ранжировать пул, объяснить выбор
      → результат в state графа (в БД пока НЕ пишем)

«Подобрать ещё» = повторный вызов с exclude_ids (id уже показанных).

Схемы ответов ПЛОСКИЕ: вложенные модели дают "$defs"/"$ref" в JSON Schema,
а провайдер отклоняет такое с 400.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from src.api.client import AcceleratorAPI
from src.core.config import settings
from src.team.directory import Directory
from src.team.models import RankedTeam, RoleFilter
from src.team.prompts import (
    RANK_SYSTEM,
    ROLE_FILTER_SYSTEM,
    rank_user_message,
    role_filter_user_message,
)
from src.utils.llm_gen import ainvoke_llm, get_llm

log = logging.getLogger(__name__)


async def role_to_filter(
    role: str,
    summary: str,
    professions: list[dict],
    stacks: list[dict],
) -> tuple[list[int], list[int], int]:
    """Переводит ОДНУ роль в фильтр по справочникам платформы.

    Модель возвращает НАЗВАНИЯ профессий/технологий, а id подставляет код
    (см. directory.py). Числа на большом справочнике модель путает — названия нет.

    Returns:
        (profession_ids, stack_ids, count)
    """
    prof_dir = Directory(professions, "Профессии")
    stack_dir = Directory(stacks, "Технологии")

    async with get_llm(temperature=0.0, fast=True) as llm:
        structured = llm.with_structured_output(RoleFilter)
        result: RoleFilter = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=ROLE_FILTER_SYSTEM),
                HumanMessage(
                    content=role_filter_user_message(role, summary, professions, stacks)
                ),
            ],
        )

    profession_ids, _ = prof_dir.resolve(result.professions)
    stack_ids, _ = stack_dir.resolve(result.stacks)
    count = max(1, result.count)

    log.info(
        "Роль '%s' → %s | %s",
        role,
        prof_dir.names_of(profession_ids) or "профессий не найдено",
        (result.reasoning or "—")[:150],
    )
    return profession_ids, stack_ids, count


async def rank_candidates(
    role: str,
    summary: str,
    candidates: list[dict],
    *,
    top_n: int,
) -> list[tuple[int, str, int]]:
    """Ранжирует пул кандидатов под роль.

    Returns:
        [(intern_id, match_reason, score), ...] — от лучшего к худшему.
    """
    if not candidates:
        return []

    async with get_llm(temperature=0.2, fast=True) as llm:
        structured = llm.with_structured_output(RankedTeam)
        result: RankedTeam = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=RANK_SYSTEM),
                HumanMessage(
                    content=rank_user_message(role, summary, candidates, top_n)
                ),
            ],
        )

    valid = {int(c["id"]) for c in candidates}
    return [p for p in result.pairs() if p[0] in valid][:top_n]


async def match_team(
    roles: list[str],
    spec_text: str,
    spec_summary: str,
    api: AcceleratorAPI,
    *,
    exclude_ids: list[int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Подбирает команду под проект. Все роли — ПАРАЛЛЕЛЬНО.

    На роль отдаём count + 1 кандидата (count из ТЗ; нет — 1), чтобы у заказчика
    был выбор, но без лишнего перебора.

    Args:
        exclude_ids: кого не показывать (уже в подборке) — для «подобрать ещё».
        progress: колбэк прогресса (пишет в стрим для фронта).

    Returns:
        [{"role", "profession_ids", "count", "candidates": [...]}, ...]
    """
    def say(text: str) -> None:
        if progress:
            progress(text)

    if not roles:
        log.warning("Роли не переданы — подбирать не под кого")
        return []

    say("Смотрю, какие специалисты есть на платформе…")
    professions = await api.list_professions()
    stacks = await api.list_stacks()
    log.info("Справочники: профессий=%d, стеков=%d", len(professions), len(stacks))

    summary = spec_summary or spec_text[:1200]
    exclude = list(exclude_ids or [])

    async def _one_role(role: str) -> dict:
        """Полный цикл по одной роли: фильтр → выборка → ранжирование."""
        try:
            profession_ids, stack_ids, count = await role_to_filter(
                role, summary, professions, stacks
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Роль '%s': не удалось сопоставить со справочниками: %s", role, exc)
            return {
                "role": role,
                "profession_ids": [],
                "count": 1,
                "candidates": [],
                "note": "Не удалось подобрать фильтры под эту роль",
            }

        want = count + 1  # count из ТЗ + 1 запасной
        say(f"Ищу: {role}…")

        # Профессии под роль нет вообще — на платформе таких людей не бывает.
        # Так и говорим, не подсовывая случайных.
        if not profession_ids:
            log.info("Роль '%s': профессии нет в справочнике платформы", role)
            return {
                "role": role,
                "profession_ids": [],
                "count": count,
                "candidates": [],
                "note": "Такой профессии на платформе нет — специалиста нужно искать вне её",
            }

        pool = await api.search_interns(
            profession_ids=profession_ids,
            stack_ids=stack_ids or None,
            exclude_ids=exclude,
            per_page=want,
        )

        # Стеки — это «хотелось бы», профессия — «обязательно». Если связка
        # профессия+стек не дала никого, ищем по одной профессии: лучше показать
        # спеца без нужного стека, чем не показать никого.
        if not pool and stack_ids:
            log.info("Роль '%s': по технологиям пусто — повторяю только по профессии", role)
            say(f"{role}: расширяю поиск…")
            pool = await api.search_interns(
                profession_ids=profession_ids,
                exclude_ids=exclude,
                per_page=want,
            )

        log.info("Роль '%s': нужно %d, нашлось %d", role, want, len(pool))

        if not pool:
            return {
                "role": role,
                "profession_ids": profession_ids,
                "count": count,
                "candidates": [],
                "note": "Специалисты этой профессии на платформе есть, но сейчас свободных нет",
            }

        try:
            ranked = await rank_candidates(role, summary, pool, top_n=want)
        except Exception as exc:  # noqa: BLE001
            # Ранжирование упало — отдаём пул как есть, без объяснений.
            log.error("Роль '%s': ранжирование не удалось: %s", role, exc)
            ranked = [(int(c["id"]), "", 50) for c in pool[:want]]

        by_id = {int(c["id"]): c for c in pool}
        candidates: list[dict] = []
        for intern_id, reason, score in ranked:
            profile = by_id.get(intern_id)
            if not profile:
                continue
            name = " ".join(
                filter(None, [profile.get("first_name"), profile.get("last_name")])
            ).strip() or f"Специалист #{intern_id}"
            candidates.append(
                {
                    "intern_id": intern_id,
                    "name": name,
                    "profession": (profile.get("profession") or {}).get("name"),
                    "match_reason": reason,
                    "score": score,
                    "profile": profile,
                }
            )

        say(f"{role}: подобрано {len(candidates)}")
        return {
            "role": role,
            "profession_ids": profession_ids,
            "count": count,
            "candidates": candidates,
        }

    # Все роли разом — не в очереди.
    return list(await asyncio.gather(*[_one_role(r) for r in roles]))


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