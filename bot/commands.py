"""Commandes Telegram (F9).

Le bot lit les messages qu'il reçoit par **long-polling** (`getUpdates`) — pas de
webhook, donc aucun port à exposer, ça marche derrière Dokploy sans config réseau.

Sécurité : seules les commandes venant du `TELEGRAM_CHAT_ID` configuré sont
exécutées. Tout le reste est ignoré (et loggé).

Commandes :
  /help                       — cette aide
  /list                       — la watchlist
  /add <web_code> <prix> [nom]— ajoute un produit à surveiller
  /remove <web_code>          — retire un produit
  /setprice <web_code> <prix> — change le seuil d'alerte
  /check <web_code>           — prix actuel d'un produit (sans l'ajouter)
  /run                        — force un cycle maintenant
  /status                     — état du bot
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.config import Settings, Target, load_targets, save_targets
from bot.telegram import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)

COMMAND_MENU: list[tuple[str, str]] = [
    ("help", "Aide"),
    ("list", "Voir la watchlist"),
    ("add", "Ajouter : /add <web_code> <prix> [nom]"),
    ("remove", "Retirer : /remove <web_code>"),
    ("setprice", "Changer un seuil : /setprice <web_code> <prix>"),
    ("check", "Prix actuel : /check <web_code>"),
    ("run", "Forcer un cycle maintenant"),
    ("status", "État du bot"),
]


class CommandError(Exception):
    """Erreur « attendue » d'une commande — le message est renvoyé tel quel à l'utilisateur."""


class CommandBot:
    def __init__(
        self,
        settings: Settings,
        notifier: TelegramNotifier,
        *,
        cycle_lock: threading.Lock,
        trigger_cycle: Callable[[], None],
        heartbeat_path,
        poll_timeout: int = 15,
    ) -> None:
        self.settings = settings
        self.tg = notifier
        self.authorized_chat_id = str(settings.telegram_chat_id)
        self.cycle_lock = cycle_lock
        self.trigger_cycle = trigger_cycle
        self.heartbeat_path = heartbeat_path
        self.poll_timeout = poll_timeout
        self._offset: int | None = None
        self._stop = threading.Event()

    # --- boucle -----------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Boucle de long-polling. Bloque jusqu'à `stop()`."""
        self._skip_backlog()
        logger.info("Écoute des commandes Telegram (chat %s).", self.authorized_chat_id)

        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(self._offset, timeout=self.poll_timeout)
            except TelegramError as exc:
                logger.warning("getUpdates : %s", exc)
                self._stop.wait(5)
                continue

            for update in updates:
                self._offset = update["update_id"] + 1
                if self._stop.is_set():
                    break
                self._handle(update)

    def _skip_backlog(self) -> None:
        """Au démarrage : ignore les commandes reçues pendant que le bot était éteint."""
        try:
            updates = self.tg.get_updates(timeout=0)
        except TelegramError:
            return
        if updates:
            self._offset = updates[-1]["update_id"] + 1

    # --- traitement -----------------------------------------------------------

    def _handle(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = str(message.get("chat", {}).get("id"))
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        if chat_id != self.authorized_chat_id:
            logger.warning("Commande ignorée d'un chat non autorisé (%s) : %r", chat_id, text)
            return

        reply = self._dispatch(text)
        if reply:
            try:
                self.tg.send_message(reply)
            except TelegramError as exc:
                logger.error("Envoi de la réponse à %r : %s", text, exc)

    def _dispatch(self, text: str) -> str:
        parts = text.split()
        # "/list@MonBot" -> "list"
        name = parts[0].lstrip("/").split("@", 1)[0].lower()
        args = parts[1:]

        handler = _HANDLERS.get(name)
        if handler is None:
            return f"Commande inconnue : /{name}\nTape /help."
        try:
            return handler(self, args)
        except CommandError as exc:
            return f"⚠️ {exc}"
        except Exception:  # noqa: BLE001
            logger.exception("Commande /%s en erreur", name)
            return "⚠️ Erreur interne (voir les logs)."

    # --- helpers partagés par les handlers -----------------------------------

    def _client(self) -> BestBuyClient:
        return BestBuyClient(
            postal_code=self.settings.postal_code, user_agent=self.settings.user_agent
        )

    def _lookup_price(self, web_code: str):
        """Retourne le `Product` Best Buy, ou lève CommandError si introuvable."""
        try:
            with self._client() as client:
                products = client.get_prices([web_code])
        except BestBuyApiError as exc:
            raise CommandError(f"Impossible d'interroger Best Buy : {exc}") from exc
        if not products:
            raise CommandError(f"Web Code {web_code} introuvable sur Best Buy.")
        return products[0]


# --- handlers de commandes -------------------------------------------------------


def _cmd_help(bot: CommandBot, args: list[str]) -> str:
    lines = ["<b>Commandes disponibles</b>"]
    lines += [f"/{name} — {desc}" for name, desc in COMMAND_MENU]
    return "\n".join(lines)


def _cmd_list(bot: CommandBot, args: list[str]) -> str:
    targets = load_targets()
    if not targets:
        return "Watchlist vide. Ajoute un produit avec /add."
    lines = [f"<b>Watchlist ({len(targets)})</b>"]
    for target in targets:
        pct = (
            f", ou −{target.min_discount_pct:g}%"
            if target.min_discount_pct is not None
            else ""
        )
        lines.append(
            f"• {target.name}\n"
            f"  <code>{target.web_code}</code> — alerte si ≤ {target.alert_price:.0f} $ {pct}"
        )
    return "\n".join(lines)


def _cmd_add(bot: CommandBot, args: list[str]) -> str:
    if len(args) < 2:
        raise CommandError("Usage : /add &lt;web_code&gt; &lt;prix&gt; [nom]")
    web_code = args[0]
    alert_price = _parse_price(args[1])
    custom_name = " ".join(args[2:]).strip()

    with bot.cycle_lock:
        targets = load_targets()
        if any(t.web_code == web_code for t in targets):
            raise CommandError(f"{web_code} est déjà dans la watchlist.")

        product = bot._lookup_price(web_code)
        name = custom_name or product.display_name
        targets.append(
            Target(name=name[:120], web_code=web_code, alert_price=alert_price)
        )
        path = save_targets(targets)

    logger.info("Commande /add : %s ajouté (%s), seuil %.0f", web_code, name, alert_price)
    return (
        f"✅ Ajouté : <b>{name[:120]}</b>\n"
        f"<code>{web_code}</code> — prix actuel {product.sale_price:.2f} $, "
        f"alerte si ≤ {alert_price:.0f} $\n"
        f"<i>({path.name} mis à jour)</i>"
    )


def _cmd_remove(bot: CommandBot, args: list[str]) -> str:
    if not args:
        raise CommandError("Usage : /remove &lt;web_code&gt;")
    web_code = args[0]
    with bot.cycle_lock:
        targets = load_targets()
        kept = [t for t in targets if t.web_code != web_code]
        if len(kept) == len(targets):
            raise CommandError(f"{web_code} n'est pas dans la watchlist.")
        save_targets(kept)
    logger.info("Commande /remove : %s retiré", web_code)
    return f"🗑️ Retiré : <code>{web_code}</code> ({len(kept)} produit(s) restant(s))."


def _cmd_setprice(bot: CommandBot, args: list[str]) -> str:
    if len(args) < 2:
        raise CommandError("Usage : /setprice &lt;web_code&gt; &lt;prix&gt;")
    web_code = args[0]
    new_price = _parse_price(args[1])
    with bot.cycle_lock:
        targets = load_targets()
        target = next((t for t in targets if t.web_code == web_code), None)
        if target is None:
            raise CommandError(f"{web_code} n'est pas dans la watchlist.")
        old = target.alert_price
        target.alert_price = new_price
        save_targets(targets)
    return f"✏️ <code>{web_code}</code> : seuil {old:.0f} $ → {new_price:.0f} $."


def _cmd_check(bot: CommandBot, args: list[str]) -> str:
    if not args:
        raise CommandError("Usage : /check &lt;web_code&gt;")
    product = bot._lookup_price(args[0])
    lines = [f"<b>{product.display_name}</b>", f"💰 {product.sale_price:.2f} $ HT"]
    if product.has_real_discount:
        lines.append(
            f"📉 −{product.discount_amount:.0f} $ / −{product.discount_pct:g} % "
            f"(barré {product.regular_price:.2f} $)"
        )
    lines.append(f"🏷️ {product.condition_label}")
    lines.append(
        "🏪 Best Buy" if product.sold_by_bestbuy else f"🏪 tiers ({product.seller_id})"
    )
    lines.append(f'🔗 <a href="{product.full_url}">Voir</a>')
    return "\n".join(lines)


def _cmd_run(bot: CommandBot, args: list[str]) -> str:
    if bot.cycle_lock.locked():
        return "⏳ Un cycle est déjà en cours."
    bot.trigger_cycle()
    return "▶️ Cycle lancé. Les alertes arriveront s'il y en a."


def _cmd_status(bot: CommandBot, args: list[str]) -> str:
    targets = load_targets()
    lines = [
        "<b>État du bot</b>",
        f"• Watchlist : {len(targets)} produit(s)",
        f"• Intervalle : {bot.settings.interval_minutes} min",
        f"• Découverte : {'activée' if bot.settings.discovery_enabled else 'désactivée'}",
    ]
    if bot.heartbeat_path.exists():
        age_min = (
            datetime.now(tz=timezone.utc).timestamp()
            - bot.heartbeat_path.stat().st_mtime
        ) / 60
        lines.append(f"• Dernier cycle : il y a {age_min:.0f} min")
    else:
        lines.append("• Dernier cycle : aucun encore")
    return "\n".join(lines)


def _parse_price(raw: str) -> float:
    try:
        value = float(raw.replace(",", ".").replace("$", "").strip())
    except ValueError:
        raise CommandError(f"« {raw} » n'est pas un prix valide.") from None
    if value <= 0:
        raise CommandError("Le prix doit être positif.")
    return value


_HANDLERS: dict[str, Callable[[CommandBot, list[str]], str]] = {
    "help": _cmd_help,
    "start": _cmd_help,
    "list": _cmd_list,
    "add": _cmd_add,
    "remove": _cmd_remove,
    "setprice": _cmd_setprice,
    "check": _cmd_check,
    "run": _cmd_run,
    "status": _cmd_status,
}
