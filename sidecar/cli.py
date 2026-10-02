"""Phase 0 CLI: classify one local video file (docs/02).

Usage:
    uv run python -m sidecar classify VIDEO [--taxonomy PATH] [--fast]
        [--json PATH] [--no-cache]

Exit codes: 0 ok; 2 config error; 3 terminal media/file error; 4 provider error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sidecar.classify.factory import build_classifier
from sidecar.classify.pipeline import ClassificationError
from sidecar.config import ConfigError, load_settings
from sidecar.providers.errors import ProviderError
from sidecar.taxonomy import TaxonomyError, load_taxonomy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sidecar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_classify = sub.add_parser("classify", help="classify one local video file")
    p_classify.add_argument("video", type=Path)
    p_classify.add_argument("--taxonomy", type=Path, default=None,
                            help="taxonomy JSON path (default: builtin 11-type v1)")
    p_classify.add_argument("--fast", action="store_true",
                            help="skip L2 audio evidence (docs/03 §3.0 quick mode)")
    p_classify.add_argument("--json", type=Path, default=None,
                            help="write full result JSON to this path")
    p_classify.add_argument("--no-cache", action="store_true",
                            help="ignore and overwrite cached stage results")

    args = parser.parse_args(argv)

    try:
        settings = load_settings(os.environ)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    try:
        taxonomy = (
            load_taxonomy(args.taxonomy.read_text(encoding="utf-8"))
            if args.taxonomy else None
        )
    except (OSError, TaxonomyError) as e:
        print(f"TAXONOMY ERROR: {e}", file=sys.stderr)
        return 2

    classifier = build_classifier(settings, taxonomy, use_cache=not args.no_cache)
    try:
        result = classifier.classify(args.video, fast_mode=args.fast)
    except ClassificationError as e:
        print(f"FILE ERROR ({'terminal' if e.terminal else 'retryable'}): {e}", file=sys.stderr)
        return 3
    except ProviderError as e:
        print(f"PROVIDER ERROR [{e.code}] (retryable={e.retryable}): {e}", file=sys.stderr)
        return 4

    print(format_result(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        print(f"\nJSON written to {args.json}")
    return 0


def format_result(result) -> str:
    worthy = "有价值" if result.analysis_worthy else "无价值"
    layer = "VLM 直出（降级）" if result.degraded else "Jev 决策"
    lines = [
        f"文件: {result.file}",
        f"类型: {result.type_name} ({result.type})  [{worthy}]",
        f"置信度: {result.confidence:.2f}   决策层: {layer}",
        f"证据摘要: {result.summary_zh}",
        f"观察标签: {', '.join(result.observations) or '(无)'}",
        f"L2 音频补证: {'触发' if result.l2_triggered else '未触发'}",
    ]
    if result.l2_transcript_excerpt:
        full = result.l2_transcript_excerpt
        excerpt = full[:80] + ("…" if len(full) > 80 else "")
        lines.append(f"  转写节选: {excerpt}")
    if result.other_guess:
        lines.append(f"other_guess: {result.other_guess}")
    top3 = sorted(result.probabilities.items(), key=lambda kv: -kv[1])[:3]
    if len(top3) > 1:
        dist = "  ".join(f"{k} {v:.2f}" for k, v in top3)
        lines.append(f"概率分布(top3): {dist}")
    if result.notes:
        lines.extend(f"注意: {n}" for n in result.notes)
    total_s = sum(result.timings_s.values())
    tokens = sum(result.usage_tokens.values())
    lines.append(f"耗时: {total_s:.1f}s   tokens: {tokens}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
