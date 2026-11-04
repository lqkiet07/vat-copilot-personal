import pytest
from src.config import (
    DATA_FREEZE_DATE,
    today_iso,
    default_as_of_date,
    is_vector_enabled,
    is_strict_grounding,
)


def test_today_iso_format():
    assert len(today_iso()) == 10
    assert today_iso().count("-") == 2


def test_default_as_of_date_clamps_future(monkeypatch):
    monkeypatch.setattr("src.config.today_iso", lambda: "2026-11-03")
    assert default_as_of_date() == DATA_FREEZE_DATE
    assert default_as_of_date() == "2026-09-30"


def test_default_as_of_date_uses_today_past(monkeypatch):
    monkeypatch.setattr("src.config.today_iso", lambda: "2026-05-15")
    assert default_as_of_date() == "2026-05-15"


def test_default_as_of_date_uses_today_on_freeze(monkeypatch):
    monkeypatch.setattr("src.config.today_iso", lambda: "2026-09-30")
    assert default_as_of_date() == "2026-09-30"


@pytest.mark.parametrize("val,expected", [
    ("1", True),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("0", False),
    ("false", False),
    ("no", False),
    ("NO", False),
])
def test_is_vector_enabled_overrides(monkeypatch, val, expected):
    monkeypatch.setenv("VECTOR_ENABLED", val)
    assert is_vector_enabled() is expected


def test_is_vector_enabled_default(monkeypatch):
    monkeypatch.delenv("VECTOR_ENABLED", raising=False)
    assert is_vector_enabled() is True


@pytest.mark.parametrize("val,expected", [
    ("1", True),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("0", False),
    ("false", False),
    ("no", False),
    ("random", False),
])
def test_is_strict_grounding_overrides(monkeypatch, val, expected):
    monkeypatch.setenv("STRICT_GROUNDING", val)
    assert is_strict_grounding() is expected


def test_is_strict_grounding_default(monkeypatch):
    monkeypatch.delenv("STRICT_GROUNDING", raising=False)
    assert is_strict_grounding() is False
