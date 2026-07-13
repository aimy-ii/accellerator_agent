"""Модели презентации проекта."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Deck(BaseModel):
    """Структура презентации — ПЛОСКАЯ схема.

    Вложенный list[Slide] дал бы "$defs"/"$ref" в JSON Schema, а провайдер
    такое отклоняет с 400 (см. src/team/models.py). Слайды идут двумя
    параллельными списками: i-й заголовок ↔ i-й набор тезисов.
    """

    title: str = Field(description="Название презентации")
    subtitle: str = Field(default="", description="Подзаголовок")
    slide_titles: list[str] = Field(
        default_factory=list, description="Заголовки слайдов по порядку"
    )
    slide_bullets: list[str] = Field(
        default_factory=list,
        description=(
            "Тезисы слайдов. Для каждого слайда — ОДНА строка, "
            "тезисы внутри разделены символом |. Порядок тот же, что у slide_titles. "
            "Пример: 'Проблема долгая|Решение быстрое|Итог экономия'"
        ),
    )

    def slides(self) -> list[tuple[str, list[str]]]:
        """Сшивает параллельные списки в слайды."""
        out: list[tuple[str, list[str]]] = []
        for i, title in enumerate(self.slide_titles):
            raw = self.slide_bullets[i] if i < len(self.slide_bullets) else ""
            bullets = [b.strip() for b in raw.split("|") if b.strip()]
            out.append((title, bullets))
        return out


class PresentationResult(BaseModel):
    """Результат генерации презентации."""

    provider: str = Field(description="gamma | local | off")
    deck: Deck | None = Field(default=None, description="Структура слайдов")
    file_url: str | None = Field(
        default=None, description="Ссылка на наш файл .pptx (постоянная)"
    )
    preview_url: str | None = Field(
        default=None, description="Ссылка на просмотр в Gamma (если provider=gamma)"
    )
    error: str | None = Field(default=None)