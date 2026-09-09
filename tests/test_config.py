"""Tests du chargement de config (`bot/config.py`)."""

from __future__ import annotations

import json

import pytest

from bot.config import Settings, Target, load_targets


def test_settings_defaults_without_env(monkeypatch, tmp_path) -> None:
    # isole des vraies variables d'env / du vrai .env
    for var in ("TAX_RATE", "INTERVAL_MINUTES", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=tmp_path / "none.env")

    assert settings.interval_minutes == 60
    assert settings.tax_rate == 0.13
    assert settings.telegram_ready is False


def test_settings_reads_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("INTERVAL_MINUTES", "15")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    settings = Settings(_env_file=tmp_path / "none.env")

    assert settings.interval_minutes == 15
    assert settings.telegram_ready is True


def test_target_uses_default_discount_when_absent() -> None:
    settings = Settings(_env_file="none.env")
    target = Target(name="x", web_code="123456", alert_price=2000)
    assert target.effective_min_discount_pct(settings) == settings.default_min_discount_pct

    target2 = Target(name="y", web_code="123457", alert_price=2000, min_discount_pct=30)
    assert target2.effective_min_discount_pct(settings) == 30


def test_target_rejects_unknown_key() -> None:
    with pytest.raises(Exception):
        Target(name="x", web_code="123456", alert_price=2000, alrt_price=1999)  # typo


def test_load_targets_detects_duplicates(tmp_path) -> None:
    path = tmp_path / "targets.json"
    path.write_text(
        json.dumps(
            [
                {"name": "a", "web_code": "111111", "alert_price": 2000},
                {"name": "b", "web_code": "111111", "alert_price": 1800},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="double"):
        load_targets(path)


def test_load_targets_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_targets(tmp_path / "absent.json")
