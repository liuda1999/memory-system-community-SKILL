#!/usr/bin/env python3
"""
Scan MEMORY.md to find matching experience files for the current task.

Usage:
    startup_scan.py --workspace <path> --task "<current task description>"

Examples:
    startup_scan.py --workspace /path/to/workspace \\
        --task "用户要求我帮他配置数据库连接"

The script reads MEMORY.md, parses all indexed entries (trigger keywords + trigger conditions),
scores each entry against the task text using substring matching,
and prints paths to the top-matching experience files.
"""

import argparse
import re
import sys
from pathlib import Path


def find_memory_md(workspace: Path) -> Path | None:
    candidates = [
        workspace / "MEMORY.md",
        workspace / "memory.md",
        workspace / "docs" / "MEMORY.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


ENTRY_RE = re.compile(r"^\s*-\s+([\w\-.]+\.md)\s*$")
KEYWORD_RE = re.compile(r"^.*触发关键词[：:](.*)$")
CONDITION_RE = re.compile(r"^.*触发条件[：:](.*)$")
WEIGHT_RE = re.compile(r"^.*权重[：:]\s*([\d.]+)")
QUOTED_RE = re.compile(r"[“\"](.*?)[”\"]")


def parse_memory_index(memory_text: str) -> list[dict]:
    """Parse MEMORY.md entries. Returns list of dicts with filename, keywords, conditions.

    A single forward scan. The previous version managed the line index by hand
    and decremented it when it saw the next entry; two entries with no keyword
    or condition lines in between made it decrement onto the previous entry and
    spin forever — verified: exit code 124 under `timeout`.
    """
    entries: list[dict] = []
    current: dict | None = None

    for line in memory_text.splitlines():
        m = ENTRY_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {"filename": m.group(1), "keywords": [], "conditions": [],
                       "weight": 1.0}
            continue
        if current is None:
            continue

        stripped = line.strip()
        kw = KEYWORD_RE.match(stripped)
        if kw:
            current["keywords"] = QUOTED_RE.findall(kw.group(1))
            continue
        cond = CONDITION_RE.match(stripped)
        if cond:
            current["conditions"] = QUOTED_RE.findall(cond.group(1))
            continue
        w = WEIGHT_RE.match(stripped)
        if w:
            try:
                current["weight"] = float(w.group(1))
            except ValueError:
                pass
            continue
        if stripped == "" or stripped.startswith("#"):
            entries.append(current)
            current = None

    if current is not None:
        entries.append(current)
    return entries


def _word_parts(text: str) -> set[str]:
    """Split a keyword into individual meaningful parts.
    'CI优化' → {'CI', '优化'}; 'merge-conflict' → {'merge', 'conflict'}"""
    parts = set()
    for token in re.split(r'[\s_\-]+', text):
        # Split English/CJK boundaries
        for seg in re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]+', token):
            if seg:
                parts.add(seg.lower())
    return parts


def score_entry(entry: dict, task_text: str) -> tuple[float, int]:
    """Score relevance using multi-level substring matching.

    Level 1: Exact keyword/condition substring in task text
    Level 2: Partial match — individual word parts of a keyword found in task

    Handles cases like: stored keywords "CI优化", task says "CI太慢了需要优化"
    → "CI优化" doesn't match as substring, but "CI" + "优化" both appear.

    Returns (score, hits). The score is the share of this entry's triggers that
    the task covers, so it rewards entries the task *fully* matches — but it
    also shrinks as an entry gains triggers, which is why `--min-matches` gates
    on the absolute hit count instead of the ratio alone.

    Auto-indexed entries carry a weight below 1.0 (see `merge_experiences`); the
    final score is multiplied by that weight so weak auto matches no longer
    out-rank a curated entry, while a strongly relevant auto entry still surfaces.
    """
    all_triggers = set(entry["keywords"]) | set(entry["conditions"])
    if not all_triggers:
        return 0.0, 0

    task_lower = task_text.lower()
    score = 0.0
    hits = 0

    for t in all_triggers:
        t_lower = t.lower()
        # Level 1: exact substring
        if t_lower in task_lower:
            score += 1.0
            hits += 1
            continue
        # Level 2: partial match — all word parts found in task
        parts = _word_parts(t)
        if parts:
            matched = sum(1 for p in parts if p in task_lower)
            if matched == len(parts) and parts:
                score += 0.5  # partial credit
                hits += 1

    weight = float(entry.get("weight", 1.0))
    return (score / len(all_triggers)) * weight, hits


def main():
    parser = argparse.ArgumentParser(description="Scan MEMORY.md for matching experiences")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--task", required=True, help="Current task description to match against")
    parser.add_argument("--min-score", type=float, default=0.05,
                        help="Minimum match score (0.0-1.0, default 0.05)")
    parser.add_argument("--min-matches", type=int, default=1,
                        help="Minimum number of triggers that must hit (default 1)")
    parser.add_argument("--max-results", type=int, default=5,
                        help="Maximum results to return (default 5)")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"[ERROR] Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    memory_path = find_memory_md(workspace)
    if not memory_path:
        print("[INFO] No MEMORY.md found in workspace.")
        sys.exit(0)

    memory_text = memory_path.read_text(encoding="utf-8")
    entries = parse_memory_index(memory_text)

    if not entries:
        print("[INFO] No experience entries found in MEMORY.md.")
        sys.exit(0)

    scored = [(score_entry(e, args.task), e) for e in entries]
    scored.sort(key=lambda x: (x[0][0], x[0][1]), reverse=True)

    matching = [(s, e) for s, e in scored
                if s[0] >= args.min_score and s[1] >= args.min_matches]
    top_matches = matching[:args.max_results]

    if not top_matches:
        print(f"[INFO] No matching experiences "
              f"(min score: {args.min_score}, min matches: {args.min_matches}).")
        sys.exit(0)

    print(f"Found {len(top_matches)} matching experience(s):\n")
    for (score, hits), entry in top_matches:
        exp_file = workspace / "experience" / entry["filename"]
        status = "✓ exists" if exp_file.exists() else "⚠ missing"
        weight = float(entry.get("weight", 1.0))
        tag = "auto" if weight < 1.0 else "curated"
        print(f"  [{score:.0%}] {entry['filename']}  ({status}, {hits} trigger(s) hit, "
              f"weight {weight:.1f}, {tag})")
        print(f"         Keywords: {', '.join(entry['keywords'][:6])}")
        print(f"         Conditions: {', '.join(entry['conditions'][:4])}")
        print(f"         Path: {exp_file}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
