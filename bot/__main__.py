"""Point d'entrée en ligne de commande.

Sprint 1 : `python -m bot check <sku> [<sku> ...]`
  -> interroge l'API Best Buy et affiche prix / rabais / stock / vendeur.
"""

from __future__ import annotations

import argparse
import logging
import sys

from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.models import Product

# La console Windows est souvent en cp1252 : on force l'UTF-8 pour les accents / emojis.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _format_product(product: Product, detail: Product | None = None) -> str:
    lines = [
        f"  {product.display_name}",
        f"    SKU        : {product.sku}",
    ]
    if product.has_real_discount:
        lines.append(
            f"    Prix       : {product.sale_price:.2f} $  "
            f"(barré {product.regular_price:.2f} $ — "
            f"-{product.discount_amount:.2f} $ / -{product.discount_pct} %)"
        )
    else:
        lines.append(f"    Prix       : {product.sale_price:.2f} $  (pas de rabais annoncé)")

    seller = "Best Buy" if product.sold_by_bestbuy else f"tiers (id {product.seller_id})"
    lines.append(f"    Vendeur    : {seller}")
    if product.is_clearance:
        lines.append("    Liquidation : oui")
    if product.sale_end_date:
        lines.append(f"    Fin promo  : {product.sale_end_date:%Y-%m-%d}")

    avail = (detail or product).availability
    if avail is not None:
        stock = avail.online_availability or "?"
        if avail.online_availability_count is not None:
            stock += f" ({avail.online_availability_count} en stock)"
        buyable = "achetable" if avail.is_purchasable else "non achetable"
        lines.append(f"    Stock      : {stock} — {buyable}")

    lines.append(f"    Lien       : {product.full_url}")
    return "\n".join(lines)


def cmd_check(skus: list[str], *, with_detail: bool) -> int:
    with BestBuyClient(postal_code="M6N 5G8") as client:
        try:
            products = client.get_prices(skus)
        except BestBuyApiError as exc:
            print(f"[ERREUR API] {exc}", file=sys.stderr)
            return 1

        found = {p.sku for p in products}
        for sku in skus:
            if sku not in found:
                print(f"  SKU {sku} : introuvable dans la réponse", file=sys.stderr)

        for product in products:
            detail = None
            if with_detail:
                try:
                    detail = client.get_product(product.sku)
                except BestBuyApiError as exc:
                    print(f"  (détail {product.sku} indisponible : {exc})", file=sys.stderr)
            print(_format_product(product, detail))
            print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="MacBook Price Watcher Bot")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="logs détaillés (retries, etc.)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="interroge l'API pour un ou plusieurs SKU")
    check.add_argument("skus", nargs="+", help="Web Codes / SKU Best Buy")
    check.add_argument(
        "--detail",
        action="store_true",
        help="appel supplémentaire par SKU pour récupérer le stock",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "check":
        return cmd_check(args.skus, with_detail=args.detail)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
