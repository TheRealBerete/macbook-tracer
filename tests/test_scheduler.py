"""Tests du cycle périodique (`bot/scheduler.py`)."""

from __future__ import annotations

import bot.scheduler as scheduler
from bot.alerts import Alert, AlertKind, AlertLevel
from bot.config import Settings


def test_run_cycle_writes_heartbeat(monkeypatch, tmp_path) -> None:
    hb = tmp_path / "last_run.txt"
    monkeypatch.setattr(scheduler, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(scheduler, "load_targets", lambda: [])
    monkeypatch.setattr(scheduler, "StateStore", lambda: object())
    monkeypatch.setattr(scheduler, "BestBuyClient", _FakeClient)
    monkeypatch.setattr(scheduler, "full_cycle", lambda *a, **k: [])

    scheduler.run_cycle(Settings(_env_file="none.env"))

    assert hb.exists()


def test_run_cycle_never_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler, "HEARTBEAT_PATH", tmp_path / "hb.txt")

    def boom() -> list:
        raise RuntimeError("catalog/query explosé")

    monkeypatch.setattr(scheduler, "load_targets", boom)

    # ne doit pas lever
    scheduler.run_cycle(Settings(_env_file="none.env"))


def test_run_cycle_sends_alerts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler, "HEARTBEAT_PATH", tmp_path / "hb.txt")
    monkeypatch.setattr(scheduler, "load_targets", lambda: [1, 2])
    monkeypatch.setattr(scheduler, "StateStore", lambda: object())
    monkeypatch.setattr(scheduler, "BestBuyClient", _FakeClient)

    alert = Alert(kind=AlertKind.PRICE_DROP, name="x", reason="", level=AlertLevel.RED)
    monkeypatch.setattr(scheduler, "full_cycle", lambda *a, **k: [alert])

    calls: list[int] = []
    monkeypatch.setattr(scheduler, "send_alerts", lambda alerts, settings: calls.append(len(alerts)) or 1)

    scheduler.run_cycle(Settings(_env_file="none.env"))
    assert calls == [1]


class _FakeClient:
    def __init__(self, *a, **k): ...
    def __enter__(self): return self
    def __exit__(self, *a): ...


def test_healthcheck_cli(monkeypatch, tmp_path) -> None:
    import bot.scheduler as sched
    from bot.__main__ import cmd_healthcheck

    hb = tmp_path / "last_run.txt"
    monkeypatch.setattr(sched, "HEARTBEAT_PATH", hb)

    assert cmd_healthcheck(180) == 1        # pas de fichier

    hb.write_text("x", encoding="utf-8")
    assert cmd_healthcheck(180) == 0        # frais

    import os
    old = tmp_path.stat().st_mtime - 4 * 3600
    os.utime(hb, (old, old))
    assert cmd_healthcheck(180) == 1        # trop vieux (4 h > 3 h)
