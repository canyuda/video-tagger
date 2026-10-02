"""Classification regression run over the eval set (docs/03 §8, D52).

Runs the full pipeline on every labeled video (cache-aware: re-runs are cheap),
then writes report.md + results.jsonl under data/eval/<timestamp>/ and prints
the summary. Labels: only `expected_type` and `label_status` are authoritative
(eval-videos README).

Usage:
    uv run python scripts/eval_classify.py [--videos DIR] [--fast] [--no-cache]
        [--only video-01.mp4 video-05.mp4]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sidecar.classify.factory import DATA_ROOT, build_classifier
from sidecar.classify.pipeline import ClassificationError
from sidecar.config import PROJECT_ROOT, load_settings
from sidecar.eval import compute_metrics, make_row
from sidecar.providers.errors import ProviderError

# D45 acceptance tiers.
TIER_OFFICIAL = 0.90
TIER_BETA = 0.85


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", type=Path, default=PROJECT_ROOT / "eval-videos")
    parser.add_argument("--fast", action="store_true", help="skip L2 audio evidence")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--only", nargs="*", default=None,
                        help="restrict to these video file names")
    parser.add_argument("--out", type=Path, default=None,
                        help="output dir (default: data/eval/<timestamp>)")
    args = parser.parse_args(argv)

    labels = load_labels(args.videos / "labels.csv")
    if args.only:
        labels = [r for r in labels if r["new_name"] in args.only]
    if not labels:
        print("no labeled videos to run", file=sys.stderr)
        return 2

    settings = load_settings(os.environ)
    classifier = build_classifier(settings, use_cache=not args.no_cache)

    rows: list[dict] = []
    failures: list[dict] = []
    for i, label in enumerate(labels, 1):
        name = label["new_name"]
        video = args.videos / name
        print(f"[{i}/{len(labels)}] {name}: expected={label['expected_type']} "
              f"({label['label_status']}) ...", flush=True)
        if not video.is_file():
            failures.append({"file": name, "error": "file missing"})
            continue
        t0 = time.perf_counter()
        try:
            result = classifier.classify(video, fast_mode=args.fast)
        except ClassificationError as e:
            failures.append({"file": name, "error": f"terminal: {e}"})
            continue
        except ProviderError as e:
            failures.append({"file": name, "error": f"provider [{e.code}]: {e}"})
            return 4
        wall = time.perf_counter() - t0
        mark = "OK " if result.type == label["expected_type"] else "MISS"
        print(f"    -> {result.type} conf={result.confidence:.2f} "
              f"l2={'Y' if result.l2_triggered else 'N'} {wall:.1f}s {mark}")
        rows.append(make_row(
            name, label["expected_type"], label["label_status"],
            {
                "type": result.type,
                "confidence": result.confidence,
                "l2_triggered": result.l2_triggered,
                "degraded": result.degraded,
                "other_guess": result.other_guess,
                "observations": result.observations,
                "summary_zh": result.summary_zh,
                "decision_layer": result.decision_layer,
                "timings_s": result.timings_s,
                "usage_tokens": result.usage_tokens,
            },
        ))

    metrics = compute_metrics(rows)
    out_dir = args.out or DATA_ROOT / "eval" / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    report = render_report(metrics, failures, fast=args.fast)
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    print("\n" + "=" * 60)
    print(summary_line(metrics, failures))
    print(f"report: {out_dir / 'report.md'}")
    return 0


def load_labels(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"new_name", "expected_type", "label_status"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"labels.csv missing columns: {sorted(missing)}")
        return [row for row in reader if row.get("expected_type")]


def summary_line(metrics: dict, failures: list[dict]) -> str:
    top1 = metrics["top1"]
    if top1 is None:
        return "no results"
    tier = (
        ">=90% 正式版线"
        if top1 >= TIER_OFFICIAL
        else ("85-90% beta 自用线" if top1 >= TIER_BETA else "<85% 不发布线")
    )
    l2 = metrics["l2_trigger_rate"]
    return (
        f"top-1: {top1:.0%} ({metrics['count']} samples) [{tier}]  "
        f"L2 触发率: {l2:.0%}  L2 全部兜底正确: {'是' if metrics['l2_all_correct'] else '否'}  "
        f"降级: {metrics['degraded_count']}  失败: {len(failures)}"
    )


def render_report(metrics: dict, failures: list[dict], *, fast: bool) -> str:
    lines = ["# 分类回归报告（D52）", ""]
    if fast:
        lines.append("> 快速模式：L2 音频补证已关闭", "")
    top1 = metrics["top1"]
    lines += [
        "## 总览",
        "",
        f"- 样本数: {metrics['count']}；top-1: {top1:.1%}" if top1 is not None else "- 无样本",
        f"- L2 触发率: {metrics['l2_trigger_rate']:.0%}；L2 样本全部兜底正确: "
        f"{'是' if metrics['l2_all_correct'] else '否'}",
        f"- 降级判定(VLM 直出): {metrics['degraded_count']} 条",
        "",
        "| label_status | 数量 | top-1 |",
        "|---|---|---|",
    ]
    for status, info in metrics["by_status"].items():
        t = f"{info['top1']:.0%}" if info["top1"] is not None else "-"
        lines.append(f"| {status} | {info['count']} | {t} |")

    lines += [
        "", "## 每类 P/R", "",
        "| 类型 | precision | recall | f1 | support |", "|---|---|---|---|---|",
    ]
    for cls in sorted(metrics["per_class"]):
        pc = metrics["per_class"][cls]

        def fmt(x):
            return f"{x:.2f}" if x is not None else "-"

        lines.append(
            f"| {cls} | {fmt(pc['precision'])} | {fmt(pc['recall'])} | "
            f"{fmt(pc['f1'])} | {pc['support']} |"
        )

    lines += ["", "## 混淆矩阵（行=期望，列=预测）", ""]
    classes = sorted(metrics["confusion"])
    if classes:
        lines.append("| 期望\\预测 | " + " | ".join(classes) + " |")
        lines.append("|---" * (len(classes) + 1) + "|")
        for expected in classes:
            cells = [str(metrics["confusion"][expected].get(c, "")) or "0" for c in classes]
            lines.append(f"| {expected} | " + " | ".join(cells) + " |")

    lines += ["", "## 错例清单", ""]
    if metrics["errors"]:
        lines += ["| 文件 | 期望 | 实判 | 置信度 | L2 | 降级 |", "|---|---|---|---|---|---|"]
        for e in metrics["errors"]:
            lines.append(
                f"| {e['file']} | {e['expected']} | {e['got']} | {e['confidence']:.2f} "
                f"| {'Y' if e['l2_triggered'] else 'N'} | {'Y' if e['degraded'] else 'N'} |"
            )
    else:
        lines.append("（无错例）")

    lines += ["", "## other_guess 聚类", ""]
    if metrics["other_guess"]:
        for guess, files in sorted(metrics["other_guess"].items()):
            lines.append(f"- {guess}: {', '.join(files)}")
    else:
        lines.append("（无）")

    if failures:
        lines += ["", "## 失败样本", ""]
        lines += [f"- {f['file']}: {f['error']}" for f in failures]

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
