"""Classification regression metrics (D52: top-1, per-class P/R, confusion
matrix, error list with confidence, L2 trigger rate, other_guess clustering).

Pure functions over rows built by the eval harness — no I/O here.
"""

from __future__ import annotations

from typing import Any


def make_row(file: str, expected: str, label_status: str, result: dict) -> dict:
    return {
        "file": file,
        "expected": expected,
        "label_status": label_status,
        "result": result,
    }


def compute_metrics(rows: list[dict]) -> dict[str, Any]:
    total = len(rows)
    correct = [r for r in rows if r["result"]["type"] == r["expected"]]

    confusion: dict[str, dict[str, int]] = {}
    for r in rows:
        confusion.setdefault(r["expected"], {})
        confusion[r["expected"]][r["result"]["type"]] = (
            confusion[r["expected"]].get(r["result"]["type"], 0) + 1
        )

    predicted_count: dict[str, int] = {}
    for r in rows:
        t = r["result"]["type"]
        predicted_count[t] = predicted_count.get(t, 0) + 1

    per_class: dict[str, dict[str, float | int]] = {}
    for cls in {*confusion.keys(), *predicted_count.keys()}:
        support = sum(confusion.get(cls, {}).values())
        tp = confusion.get(cls, {}).get(cls, 0)
        precision = tp / predicted_count[cls] if predicted_count.get(cls) else None
        recall = tp / support if support else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall else None
        )
        per_class[cls] = {
            "precision": precision, "recall": recall, "f1": f1, "support": support,
        }

    errors = [
        {
            "file": r["file"],
            "expected": r["expected"],
            "got": r["result"]["type"],
            "confidence": r["result"]["confidence"],
            "l2_triggered": r["result"]["l2_triggered"],
            "degraded": r["result"]["degraded"],
        }
        for r in rows if r["result"]["type"] != r["expected"]
    ]

    other_guess: dict[str, list[str]] = {}
    for r in rows:
        guess = r["result"].get("other_guess")
        if guess:
            other_guess.setdefault(guess, []).append(r["file"])

    l2_rows = [r for r in rows if r["result"]["l2_triggered"]]
    by_status: dict[str, dict] = {}
    for status in ("confirmed", "draft"):
        subset = [r for r in rows if r["label_status"] == status]
        by_status[status] = {
            "count": len(subset),
            "top1": (
                sum(1 for r in subset if r["result"]["type"] == r["expected"]) / len(subset)
                if subset else None
            ),
        }

    return {
        "count": total,
        "top1": len(correct) / total if total else None,
        "by_status": by_status,
        "per_class": per_class,
        "confusion": confusion,
        "errors": errors,
        "l2_trigger_rate": len(l2_rows) / total if total else None,
        "l2_all_correct": all(r["result"]["type"] == r["expected"] for r in l2_rows),
        "other_guess": other_guess,
        "degraded_count": sum(1 for r in rows if r["result"]["degraded"]),
    }
