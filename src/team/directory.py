"""Справочники платформы: сопоставление названий с id.

Модель оперирует НАЗВАНИЯМИ («UI/UX Design»), а не числами — числа для неё шум,
на большом справочнике она в них путается. Превращать названия в id — работа
кода, а не модели: детерминированная и не ошибающаяся.

Сопоставление точное, с нормализацией (регистр, пробелы, дефисы, ё/e), потому
что модель может слегка исказить написание. Что не сматчилось — в лог: это
галлюцинация, и её надо видеть, а не глотать молча.
"""
from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """Приводит название к сравнимому виду.

    Регистр, ё→е, лишние пробелы, дефисы/слэши/точки — всё убирается.
    'UI/UX Design' и 'ui ux дизайн' сравниваются как есть, без искажений смысла.
    """
    text = unicodedata.normalize("NFKC", name).strip().lower().replace("ё", "е")
    text = re.sub(r"[\s\-_/\\.,()]+", "", text)
    return text


class Directory:
    """Справочник платформы: название → id."""

    def __init__(self, items: list[dict], kind: str) -> None:
        self._kind = kind
        self._by_norm: dict[str, int] = {}
        self._names: dict[int, str] = {}

        for item in items:
            item_id = item.get("id")
            name = item.get("name") or ""
            if item_id is None or not name:
                continue
            self._by_norm[_normalize(name)] = int(item_id)
            self._names[int(item_id)] = name

    def resolve(self, names: list[str]) -> tuple[list[int], list[str]]:
        """Превращает названия в id.

        Returns:
            (найденные id, названия, которых в справочнике нет).
        """
        found: list[int] = []
        unknown: list[str] = []

        for name in names:
            item_id = self._by_norm.get(_normalize(name))
            if item_id is None:
                unknown.append(name)
            elif item_id not in found:
                found.append(item_id)

        if unknown:
            log.warning(
                "%s: модель назвала то, чего нет в справочнике: %s",
                self._kind,
                unknown,
            )
        return found, unknown

    def names_of(self, ids: list[int]) -> list[str]:
        """Обратно: id → названия (для логов и показа заказчику)."""
        return [self._names[i] for i in ids if i in self._names]

    def __len__(self) -> int:
        return len(self._names)