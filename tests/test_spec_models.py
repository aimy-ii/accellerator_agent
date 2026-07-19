"""Схемы ТЗ принимают execution_days и корректно дефолтят его в None."""
from __future__ import annotations

from src.techspec.models import RefinedSpec, TechSpec


def test_techspec_execution_days_optional_default_none():
    spec = TechSpec(
        title="X",
        summary="Y",
        tech_spec_text="# Техническое задание\n\n## X",
    )
    assert spec.execution_days is None


def test_techspec_accepts_execution_days_int():
    spec = TechSpec(
        title="X",
        summary="Y",
        tech_spec_text="# Техническое задание\n\n## X",
        execution_days=45,
    )
    assert spec.execution_days == 45


def test_refinedspec_execution_days_optional_default_none():
    refined = RefinedSpec(tech_spec_text="# Техническое задание\n\n## X")
    assert refined.execution_days is None


def test_refinedspec_accepts_execution_days_int():
    refined = RefinedSpec(
        tech_spec_text="# Техническое задание\n\n## X",
        execution_days=60,
    )
    assert refined.execution_days == 60
