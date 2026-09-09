"""Envoi de messages via l'API HTTP de Telegram Bot.

En V1 on n'utilise PAS la librairie `python-telegram-bot` (grosse, asyncio) :
envoyer un message = un simple POST sur
`https://api.telegram.org/bot<token>/sendMessage`.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        if not bot_token or not chat_id:
            raise TelegramError("bot_token et chat_id sont requis")
        self.chat_id = chat_id
        self.max_retries = max_retries
        self._base = f"/bot{bot_token}"
        self._client = client or httpx.Client(base_url=API_ROOT, timeout=timeout)
        self._owns_client = client is None

    def __enter__(self) -> "TelegramNotifier":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def send_message(self, text: str, *, disable_preview: bool = True) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.post(f"{self._base}/sendMessage", json=payload)
            except httpx.HTTPError as exc:
                logger.warning("Telegram : erreur réseau (tentative %d) : %s", attempt, exc)
                if attempt == self.max_retries:
                    raise TelegramError(f"envoi impossible : {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            if response.status_code == 200:
                return

            # 429 : Telegram indique combien de temps attendre
            if response.status_code == 429:
                retry_after = int(
                    response.json().get("parameters", {}).get("retry_after", 2 ** attempt)
                )
                logger.warning("Telegram 429 : nouvelle tentative dans %ds", retry_after)
                time.sleep(retry_after)
                continue

            raise TelegramError(
                f"HTTP {response.status_code} : {response.text[:300]}"
            )

        raise TelegramError(f"envoi abandonné après {self.max_retries} tentatives")

    def check(self) -> str:
        """Appelle getMe pour vérifier que le token est valide. Retourne le @username."""
        response = self._client.get(f"{self._base}/getMe")
        if response.status_code != 200:
            raise TelegramError(f"token invalide ? HTTP {response.status_code}")
        return response.json().get("result", {}).get("username", "?")
