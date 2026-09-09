"""Tests de l'envoi Telegram (`bot/telegram.py`, `bot/notify.py`) — sans réseau."""

from __future__ import annotations

import httpx
import pytest

from bot.alerts import Alert, AlertKind, AlertLevel
from bot.config import Settings
from bot.notify import send_alerts
from bot.telegram import TelegramError, TelegramNotifier


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.telegram.org", transport=httpx.MockTransport(handler)
    )


def test_send_message_posts_expected_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    tg = TelegramNotifier("TOKEN", "42", client=_client(handler))
    tg.send_message("<b>hi</b>")

    assert captured["path"] == "/botTOKEN/sendMessage"
    assert captured["json"]["chat_id"] == "42"
    assert captured["json"]["parse_mode"] == "HTML"


def test_429_is_retried_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"parameters": {"retry_after": 0}})
        return httpx.Response(200, json={"ok": True})

    tg = TelegramNotifier("T", "1", client=_client(handler))
    tg.send_message("x")
    assert len(calls) == 2


def test_http_error_raises_telegram_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request: chat not found")

    tg = TelegramNotifier("T", "1", client=_client(handler))
    with pytest.raises(TelegramError, match="chat not found"):
        tg.send_message("x")


def test_missing_credentials_raise() -> None:
    with pytest.raises(TelegramError):
        TelegramNotifier("", "")


def test_send_alerts_without_config_sends_nothing() -> None:
    settings = Settings(_env_file="none.env")  # pas de token
    alert = Alert(kind=AlertKind.PRICE_DROP, name="x", reason="y", level=AlertLevel.RED)
    assert send_alerts([alert], settings) == 0


def test_send_alerts_continues_after_one_failure(monkeypatch) -> None:
    settings = Settings(_env_file="none.env")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")
    monkeypatch.setattr(settings, "telegram_chat_id", "1")

    sent: list[str] = []

    class FakeTg:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): ...
        def send_message(self, text: str) -> None:
            if len(sent) == 0:
                sent.append(text)
                raise TelegramError("boom")
            sent.append(text)

    monkeypatch.setattr("bot.notify.TelegramNotifier", FakeTg)

    alerts = [
        Alert(kind=AlertKind.PRICE_DROP, name="a", reason="", level=AlertLevel.RED),
        Alert(kind=AlertKind.PRICE_DROP, name="b", reason="", level=AlertLevel.RED),
    ]
    assert send_alerts(alerts, settings) == 1   # la 2e passe malgré l'échec de la 1re
    assert len(sent) == 2
