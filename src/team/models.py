"""Модели подбора команды."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RoleQuery(BaseModel):
    """Роль из ТЗ, переведённая в фильтр по справочникам платформы."""

    role: str = Field(description="Название роли, как в ТЗ (например 'Backend-разработчик')")
    profession_ids: list[int] = Field(
        default_factory=list,
        description="ID профессий платформы, подходящих под роль (только из справочника)",
    )
    stack_ids: list[int] = Field(
        default_factory=list,
        description="ID стеков платформы, релевантных роли (только из справочника)",
    )
    count: int = Field(default=1, ge=1, description="Сколько таких специалистов нужно")


class RoleQueries(BaseModel):
    """Все роли проекта, переведённые в фильтры."""

    roles: list[RoleQuery] = Field(default_factory=list)


class RankedCandidate(BaseModel):
    """Кандидат, отобранный ИИ под конкретную роль."""

    intern_id: int = Field(description="ID специалиста из выдачи API")
    match_reason: str = Field(
        description="Почему подходит — коротко, человеческим языком, для заказчика"
    )
    score: int = Field(ge=1, le=100, description="Насколько хорошо подходит, 1-100")


class RankedTeam(BaseModel):
    """Отранжированные кандидаты по одной роли."""

    candidates: list[RankedCandidate] = Field(default_factory=list)
