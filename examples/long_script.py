"""Inventory report: load, clean, summarize."""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

DATA = Path("inventory.csv")
LOW_STOCK = 5


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def clean(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        try:
            qty = int(row["qty"])
            price = float(row["price"])
        except (KeyError, ValueError):
            continue
        items.append({
            "sku": row["sku"].strip().upper(),
            "cat": row.get("category", "misc"),
            "qty": qty,
            "price": price,
        })
    return items


def summarize(items: list[dict]) -> dict:
    by_cat = defaultdict(list)
    for item in items:
        by_cat[item["cat"]].append(item)
    report = {}
    for cat, group in sorted(by_cat.items()):
        prices = [i["price"] for i in group]
        report[cat] = {
            "count": len(group),
            "median": statistics.median(prices),
            "value": sum(i["qty"] * i["price"]
                         for i in group),
            "low": [i["sku"] for i in group
                    if i["qty"] < LOW_STOCK],
        }
    return report


if __name__ == "__main__":
    report = summarize(clean(load(DATA)))
    for cat, stats in report.items():
        print(f"{cat:<12} {stats['value']:10.2f}")
