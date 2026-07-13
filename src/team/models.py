"""Модели подбора команды.

Два правила, оба выстраданы:

1. СХЕМЫ ПЛОСКИЕ — без вложенных моделей.
   Pydantic для list[SomeModel] генерит JSON Schema с "$defs"/"$ref",
   а Anthropic через kodikrouter такое отклоняет:
       400 {'message': 'Провайдер отклонил запрос: Provider returned error'}
   Только str/int/bool и list[str]/list[int].

2. МОДЕЛЬ ОПЕРИРУЕТ НАЗВАНИЯМИ, А НЕ ID.
   Число «28» для модели — шум: его надо удержать из справочника и точно
   скопировать. На 57 позициях она сбивается и хватает не то (Дизайнер →
   Frontend). Названия — это смысл, с ним модель работает естественно.
   ID подставляет наш код по словарю: детерминированно, ошибиться нельзя.
   Заодно видно галлюцинации — выдуманное название в лог, выдуманный id
   был бы неотличим от опечатки.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RoleFilter(BaseModel):
    """Одна роль, переведённая в фильтр по справочникам платформы.

    Вызывается на КАЖДУЮ роль отдельно (роли идут параллельно).
    """

    # reasoning ПЕРВЫМ: модель заполняет поля по порядку, поэтому обязана
    # сначала сформулировать суть роли и только потом выбирать профессии.
    reasoning: str = Field(
        default="",
        description=(
            "Сначала объясни: чем занимается человек в этой роли, что делает руками. "
            "Затем — какие профессии из справочника эту работу выполняют и почему"
        ),
    )
    professions: list[str] = Field(
        default_factory=list,
        description=(
            "НАЗВАНИЯ профессий из справочника — ТОЧНО как они там написаны. "
            "Все, что закрывают роль. Ни одна не подходит — пустой список"
        ),
    )
    stacks: list[str] = Field(
        default_factory=list,
        description=(
            "НАЗВАНИЯ технологий из справочника — точно как там написаны. "
            "3-6 релевантных роли. Роль нетехническая — пустой список"
        ),
    )
    count: int = Field(
        default=1,
        description="Сколько таких специалистов нужно проекту. Не сказано — 1",
    )


class RankedTeam(BaseModel):
    """Отранжированные кандидаты под роль — тремя ПАРАЛЛЕЛЬНЫМИ списками.

    Плоская вместо list[RankedCandidate]: i-й элемент каждого списка относится
    к одному кандидату.

    Здесь id уместны: это идентификаторы из ТОЛЬКО ЧТО переданного короткого
    списка кандидатов (единицы штук), а не выбор из большого справочника.
    """

    intern_ids: list[int] = Field(
        default_factory=list,
        description="ID специалистов из переданного списка, от лучшего к худшему",
    )
    match_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Почему подходит — по предложению на каждого, в ТОМ ЖЕ порядке, "
            "что и intern_ids. Человеческим языком, без жаргона"
        ),
    )
    scores: list[int] = Field(
        default_factory=list,
        description="Оценка соответствия 1-100, в ТОМ ЖЕ порядке, что и intern_ids",
    )

    def pairs(self) -> list[tuple[int, str, int]]:
        """Сшивает параллельные списки обратно в кандидатов."""
        out: list[tuple[int, str, int]] = []
        for i, intern_id in enumerate(self.intern_ids):
            reason = self.match_reasons[i] if i < len(self.match_reasons) else ""
            score = self.scores[i] if i < len(self.scores) else 50
            out.append((intern_id, reason, score))
        return out