#!/usr/bin/env python3
"""
Generate a decomposed summary (memory/YYYY-MM-DD-decompose.md) from a raw daily log.

Usage:
    decompose.py --workspace <path> --date YYYY-MM-DD

Examples:
    decompose.py --workspace /path/to/workspace --date 2026-05-10

The script reads memory/YYYY-MM-DD.md, extracts keywords from section headers,
writes a compact summary, and creates memory/YYYY-MM-DD-decompose.md.
"""

import argparse
import re
import sys
from pathlib import Path

# Shared Chinese/English keyword extractor (jieba if available, dictionary
# fallback otherwise). Keeps this script's behaviour identical to
# merge_experiences.py and avoids the old sliding-window fragment problem.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _textutil import extract_keywords  # noqa: E402


def summarize(text: str) -> str:
    """Generate a concise 1-2 sentence summary from the log."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Look for meaningful lines (not headers, not timestamps)
    meaningful = []
    for line in lines:
        if line.startswith("#") or line.startswith("[") or line.startswith("---"):
            continue
        if len(line) > 10:
            meaningful.append(line)
    if not meaningful:
        return "No substantial content found."
    # Take first 3 substantive lines as rough summary
    summary_lines = meaningful[:3]
    summary = ". ".join(sl.rstrip(".") for sl in summary_lines)
    if len(summary) > 300:
        summary = summary[:297] + "..."
    if summary and not summary.endswith("."):
        summary += "."
    return summary


def main():
    parser = argparse.ArgumentParser(description="Generate decomposed summary from daily raw log")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    # Validate date format
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        print(f"[ERROR] Invalid date format: {args.date}. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    workspace = Path(args.workspace).resolve()
    raw_file = workspace / "memory" / f"{args.date}.md"
    output_file = workspace / "memory" / f"{args.date}-decompose.md"

    if not raw_file.exists():
        print(f"[ERROR] Daily log not found: {raw_file}", file=sys.stderr)
        sys.exit(1)

    raw_text = raw_file.read_text(encoding="utf-8")

    # Extract keywords
    keywords = extract_keywords(raw_text)
    # Deduplicate while preserving order, limit to 15
    seen = set()
    unique_keywords = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)
    top_keywords = unique_keywords[:15]

    # Warn if decomposing today's log (design intent is next-day)
    from datetime import date
    if args.date == date.today().strftime("%Y-%m-%d"):
        print("[WARN] Decomposing today's log. The day may not be complete yet.", file=sys.stderr)
        print("       Design intent: run this on the NEXT day for the previous day's log.", file=sys.stderr)

    # Generate summary
    summary = summarize(raw_text)

    # Count turns. Each turn is one User block and one Assistant block, so
    # counting every "## [" header reported twice the real number — a four-block
    # log (two turns) was summarised as "共 4 轮对话".
    user_turns = raw_text.count("## [") // 2

    # Write decomposition
    content_parts = [
        f"# Decompose for {args.date}\n",
        "## 预读指南",
        "此文件帮助你快速决定是否需要阅读完整的原始日志。它包含关键词和一份摘要。\n",
        "## 关键词",
    ]
    for kw in top_keywords:
        content_parts.append(f"- {kw}")
    content_parts.append("")
    content_parts.append("## 摘要")
    content_parts.append(f"共 {user_turns} 轮对话。{summary}")
    content_parts.append("")

    output_file.write_text("\n".join(content_parts), encoding="utf-8")
    print(f"[OK] Created decomposition: {output_file}")
    print(f"     Keywords: {len(top_keywords)}, Summary: {len(summary)} chars")

    return 0


if __name__ == "__main__":
    sys.exit(main())
