"""Tests des commandes Telegram (`bot/commands.py`)."""

from __future__ import annotations

import threading

import pytest

from bot.commands import CommandBot
from bot.config import Settings
from bot.config import load_targets as real_load_targets
from bot.config import save_targets as real_save_targets
from bot.models import Product

KNOWN_PRODUCTS = {
    "18619896": {"name": "MacBook Pro 14 M4", "regular": 2249.99, "sale": 1899.99},
    "20009307": {"name": "(Boîte ouverte) MacBook Pro 14 M3", "regular": 2187, "sale": 2187},
}


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.updates: list[list[dict]] = []

    def send_message(self, text: str) -> None:
        self.sent.append(text)

    def set_my_commands(self, commands) -> None: ...

    def get_updates(self, offset=None, *, timeout=15):
        return self.updates.pop(0) if self.updates else []


class FakeClient:
    def __init__(self, *a, **k): ...
    def __enter__(self): return self
    def __exit__(self, *a): ...

    def get_prices(self, skus):
        out = []
        for sku in skus:
            data = KNOWN_PRODUCTS.get(sku)
            if data:
                out.append(
                    Product.model_validate(
                        {
                            "sku": sku,
                            "name": data["name"],
                            "regularPrice": data["regular"],
                            "salePrice": data["sale"],
                            "productUrl": f"/fr-CA/produit/x/{sku}",
                            "sellerId": "bbyca",
                        }
                    )
                )
        return out


@pytest.fixture
def bot(tmp_path, monkeypatch) -> CommandBot:
    targets_path = tmp_path / "targets.json"

    def fake_load(path=None):
        return real_load_targets(targets_path) if targets_path.exists() else []

    monkeypatch.setattr("bot.commands.load_targets", fake_load)
    monkeypatch.setattr(
        "bot.commands.save_targets",
        lambda targets, path=None: real_save_targets(targets, targets_path),
    )
    monkeypatch.setattr("bot.commands.BestBuyClient", FakeClient)

    settings = Settings(_env_file="none.env")
    monkeypatch.setattr(settings, "telegram_chat_id", "42")

    triggered: list[int] = []
    b = CommandBot(
        settings,
        FakeNotifier(),
        cycle_lock=threading.Lock(),
        trigger_cycle=lambda: triggered.append(1),
        heartbeat_path=tmp_path / "last_run.txt",
    )
    b.triggered = triggered  # type: ignore[attr-defined]
    b.targets_path = targets_path  # type: ignore[attr-defined]
    return b


# --- dispatch -----------------------------------------------------------------


def test_unknown_command(bot: CommandBot) -> None:
    assert "inconnue" in bot._dispatch("/pouet").lower()


def test_help_lists_commands(bot: CommandBot) -> None:
    out = bot._dispatch("/help")
    assert "/add" in out and "/list" in out


def test_list_empty_then_populated(bot: CommandBot) -> None:
    assert "vide" in bot._dispatch("/list").lower()
    bot._dispatch("/add 18619896 1999")
    out = bot._dispatch("/list")
    assert "18619896" in out and "MacBook Pro 14 M4" in out


def test_add_writes_file_and_uses_bestbuy_name(bot: CommandBot) -> None:
    out = bot._dispatch("/add 18619896 1999")
    assert "✅" in out
    targets = real_load_targets(bot.targets_path)  # type: ignore[attr-defined]
    assert targets[0].web_code == "18619896"
    assert targets[0].alert_price == 1999
    assert targets[0].name == "MacBook Pro 14 M4"


def test_add_custom_name(bot: CommandBot) -> None:
    bot._dispatch("/add 18619896 1999 Mon petit portable")
    targets = real_load_targets(bot.targets_path)  # type: ignore[attr-defined]
    assert targets[0].name == "Mon petit portable"


def test_add_rejects_duplicate(bot: CommandBot) -> None:
    bot._dispatch("/add 18619896 1999")
    assert "déjà" in bot._dispatch("/add 18619896 1800")


def test_add_rejects_unknown_web_code(bot: CommandBot) -> None:
    assert "introuvable" in bot._dispatch("/add 99999999 1999")


def test_add_rejects_bad_price(bot: CommandBot) -> None:
    assert "prix" in bot._dispatch("/add 18619896 pas-un-prix").lower()


def test_remove(bot: CommandBot) -> None:
    bot._dispatch("/add 18619896 1999")
    assert "🗑️" in bot._dispatch("/remove 18619896")
    assert real_load_targets(bot.targets_path) == []  # type: ignore[attr-defined]
    assert "n'est pas" in bot._dispatch("/remove 18619896")


def test_setprice(bot: CommandBot) -> None:
    bot._dispatch("/add 18619896 1999")
    out = bot._dispatch("/setprice 18619896 1750")
    assert "1750" in out
    assert real_load_targets(bot.targets_path)[0].alert_price == 1750  # type: ignore[attr-defined]


def test_check_does_not_add(bot: CommandBot) -> None:
    out = bot._dispatch("/check 20009307")
    assert "Boîte ouverte" in out
    assert not bot.targets_path.exists()  # type: ignore[attr-defined]


def test_run_triggers_cycle(bot: CommandBot) -> None:
    assert "lancé" in bot._dispatch("/run")
    assert bot.triggered == [1]  # type: ignore[attr-defined]


def test_run_when_locked(bot: CommandBot) -> None:
    bot.cycle_lock.acquire()
    try:
        assert "déjà en cours" in bot._dispatch("/run")
    finally:
        bot.cycle_lock.release()


def test_status(bot: CommandBot) -> None:
    out = bot._dispatch("/status")
    assert "Watchlist" in out and "aucun encore" in out


# --- sécurité / boucle -------------------------------------------------------


def test_handle_ignores_unauthorized_chat(bot: CommandBot) -> None:
    bot._handle({"update_id": 1, "message": {"chat": {"id": 999}, "text": "/list"}})
    assert bot.tg.sent == []  # rien envoyé


def test_handle_replies_to_authorized_chat(bot: CommandBot) -> None:
    bot._handle({"update_id": 1, "message": {"chat": {"id": 42}, "text": "/help"}})
    assert len(bot.tg.sent) == 1 and "/add" in bot.tg.sent[0]


def test_run_loop_processes_then_stops(bot: CommandBot) -> None:
    bot.tg.updates = [
        [],  # 1er appel = _skip_backlog (démarrage)
        [{"update_id": 10, "message": {"chat": {"id": 42}, "text": "/help"}}],
    ]

    def stop_after():
        # laisse un tour de boucle puis arrête
        import time

        time.sleep(0.05)
        bot.stop()

    threading.Thread(target=stop_after).start()
    bot.run()
    assert any("/add" in m for m in bot.tg.sent)
    assert bot._offset == 11
