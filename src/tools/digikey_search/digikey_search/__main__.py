"""Command line: keyword search to a table or CSV.

pixi run python -m digikey_search "365nm UV LED" --max 100 --qty 100 --csv leds.csv
pixi run python -m digikey_search --details LTPL-C034UVH365
"""

import argparse
import csv
import json
import sys

from .client import DigiKeyClient, flatten_product


def main(argv=None):
    p = argparse.ArgumentParser(description="Read-only DigiKey part search.")
    p.add_argument("keywords", nargs="?", help="search text")
    p.add_argument("--max", type=int, default=50, help="most products to fetch")
    p.add_argument("--qty", type=int, default=100, help="quantity for the unit price")
    p.add_argument("--in-stock", action="store_true", help="drop parts with no stock")
    p.add_argument(
        "--csv", help="write every column, including parameters, to this file"
    )
    p.add_argument(
        "--details", metavar="PART", help="print one part's full record as JSON"
    )
    args = p.parse_args(argv)

    client = DigiKeyClient()
    if args.details:
        json.dump(client.product_details(args.details), sys.stdout, indent=2)
        print()
        return
    if not args.keywords:
        p.error("give keywords or --details PART")

    rows = [
        flatten_product(prod, args.qty)
        for prod in client.search_all(args.keywords, args.max)
    ]
    if args.in_stock:
        rows = [r for r in rows if r["stock"]]
    if args.csv:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} parts to {args.csv}")
        return
    price = f"unit_price_at_{args.qty}"
    for r in rows:
        print(
            f"{r['mpn'] or '':28} {r['manufacturer'] or '':22} "
            f"{r['stock'] or 0:>7} {r[price] if r[price] is not None else '-':>8}  "
            f"{r['description'] or ''}"
        )


if __name__ == "__main__":
    main()
