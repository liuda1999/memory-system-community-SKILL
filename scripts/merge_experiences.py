#!/usr/bin/env python3
"""
Merge semantically similar experience files and clean up MEMORY.md.

Usage:
    merge_experiences.py --workspace <path> [--dry-run]
    merge_experiences.py --workspace <path> --force-merge <file1,file2>

Modes:
    --dry-run      Scan and report without making changes
    --force-merge  Comma-separated filenames to merge, ignoring the thresholds

Automatic handling (no --dry-run):
  ① Orphan index entries (file missing) → auto-remove from MEMORY.md
  ② Unindexed files (exist but not in MEMORY.md) → auto-parse header and add index
  ③ High-similarity clusters (keywords/conditions ≥80% + content ≥80%) → auto-merge,
     newest file (by Date) keeps identity, every other member's content is appended
  ④ Broken files (missing fields) → report specific missing fields, no auto action

Safety rules that the previous version did not have
---------------------------------------------------
* **Every source file is copied to `experience/.archive/<timestamp>/` before it
  is removed.** A merge is destructive; the only way to run without a backup is
  the explicit `--no-backup` flag.
* **The merged file is written before any source is deleted**, so a crash
  mid-merge cannot leave you with neither copy.
* **Similarity pairs are grouped into clusters before merging.** The old code
  merged pair by pair while re-reading stale in-memory copies, so in a three-way
  similarity (A~B, A~C, B~C) the second merge overwrote the first and silently
  dropped a file's content entirely.
* **Files are written atomically** (temp file + rename), so an interrupted run
  never leaves a half-written MEMORY.md.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Shared Chinese/English keyword extractor (jieba if available, dictionary
# fallback otherwise). Avoids the old sliding-window fragment problem.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _textutil import extract_keywords  # noqa: E402


VALID_EVENT_TYPES = {"decision", "lesson", "success", "failure", "idea", "config", "note"}
AUTO_MERGE_KEYWORD_THRESHOLD = 0.8
AUTO_MERGE_CONTENT_THRESHOLD = 0.8
KEYWORD_SAMPLE = 10
ARCHIVE_DIRNAME = ".archive"
# Auto-generated index entries (from `merge_experiences` back-filling unindexed
# files, or from merges that mix auto + human content) are less trustworthy than
# hand-authored ones, so they score lower in `startup_scan`. 0.5 keeps them
# findable when strongly relevant but prevents them from out-ranking curated
# entries on a weak match. No manual calibration is required.
AUTO_INDEX_WEIGHT = 0.5

ENTRY_RE = re.compile(r"^\s*-\s+([\w\-.]+\.md)\s*$")
KEYWORD_RE = re.compile(r"^.*触发关键词[：:](.*)$")
CONDITION_RE = re.compile(r"^.*触发条件[：:](.*)$")
WEIGHT_RE = re.compile(r"^.*权重[：:]\s*([\d.]+)")
QUOTED_RE = re.compile(r"[“\"](.*?)[”\"]")


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


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def parse_memory_index(memory_text: str) -> list[dict]:
    """Parse MEMORY.md entries.

    A single forward scan. The previous version walked the line list with a
    hand-managed index and backed it up by one when it saw the next entry; with
    two entries that had no keyword or condition lines that backed-up index
    landed on the previous entry and the scan never advanced — an infinite loop
    that hung the script with no output at all.
    """
    entries: list[dict] = []
    current: dict | None = None

    for idx, line in enumerate(memory_text.splitlines()):
        m = ENTRY_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "filename": m.group(1),
                "keywords": [],
                "conditions": [],
                "weight": 1.0,  # default: hand-authored / fully trusted
                "start_line": idx,
                "end_line": idx,
            }
            continue

        if current is None:
            continue

        stripped = line.strip()
        kw = KEYWORD_RE.match(stripped)
        if kw:
            current["keywords"] = QUOTED_RE.findall(kw.group(1))
            current["end_line"] = idx
            continue
        cond = CONDITION_RE.match(stripped)
        if cond:
            current["conditions"] = QUOTED_RE.findall(cond.group(1))
            current["end_line"] = idx
            continue
        w = WEIGHT_RE.match(stripped)
        if w:
            try:
                current["weight"] = float(w.group(1))
            except ValueError:
                pass
            current["end_line"] = idx
            continue
        if stripped == "" or stripped.startswith("#"):
            entries.append(current)
            current = None
            continue
        # Any other indented line belongs to this entry; keep it in range so
        # removing the entry removes its whole block.
        current["end_line"] = idx

    if current is not None:
        entries.append(current)
    return entries


def parse_experience_file(filepath: Path) -> dict | None:
    """Parse an experience file. Returns dict or None if unreadable."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    result = {"filepath": str(filepath), "filename": filepath.name, "text": text}
    result["missing_fields"] = []

    for line in text.splitlines():
        if line.startswith("Event Type:"):
            result["event_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("Event Name:"):
            result["event_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Date:"):
            result["date"] = line.split(":", 1)[1].strip()
        elif line.startswith("Summary:"):
            result["summary"] = line.split(":", 1)[1].strip()

    for field in ["Event Type", "Event Name", "Date", "Summary"]:
        field_key = field.lower().replace(" ", "_")
        if not result.get(field_key):
            result["missing_fields"].append(field)

    desc_match = re.search(
        r"## Full Description / Complete Resolution Process\n(.+?)(?=\n## |\Z)", text, re.DOTALL
    )
    if desc_match and desc_match.group(1).strip():
        result["description"] = desc_match.group(1).strip()
    else:
        result["missing_fields"].append("Full Description")

    design_match = re.search(r"## Design / Solution\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
    if design_match and design_match.group(1).strip():
        result["design"] = design_match.group(1).strip()
    else:
        result["missing_fields"].append("Design / Solution")

    return result


def jaccard_similarity(set1: set, set2: set) -> float:
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)


def extract_keywords_from_experience(pf: dict, limit: int = 12) -> list[str]:
    """Extract keywords from an experience file's content, in a stable order.

    Uses the shared `extract_keywords` segmenter so Chinese text is split into
    word-like tokens (no mid-word fragments) and the result is deterministic.
    """
    text = " ".join(
        str(pf.get(k, "") or "")
        for k in ("summary", "description", "design", "event_name")
    )
    return extract_keywords(text, limit=limit)


def content_similarity(a: dict, b: dict) -> float:
    """Compare two experiences by topic keywords.

    The previous version sampled ten keywords out of a `set`, and Python's
    string hashing is randomised per process, so which ten it picked changed
    from run to run. On a destructive merge that means the same inputs could
    merge on one run and not the next. Sampling an ordered list is stable.
    """
    kw_a = set(extract_keywords_from_experience(a)[:KEYWORD_SAMPLE])
    kw_b = set(extract_keywords_from_experience(b)[:KEYWORD_SAMPLE])
    return jaccard_similarity(kw_a, kw_b)


def trigger_similarity(a: dict, b: dict) -> float:
    ta = set(a.get("keywords", [])) | set(a.get("conditions", []))
    tb = set(b.get("keywords", [])) | set(b.get("conditions", []))
    return jaccard_similarity(ta, tb)


def cluster(names: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Union-find grouping, so a member shared by two pairs merges only once."""
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return list(groups.values())


def build_merged_content(members: list[dict]) -> tuple[str, str, list[str], list[str], float]:
    """Merge a cluster. The newest member keeps the identity; nobody's content is lost.

    The merged entry inherits the lowest member weight: if any source was an
    auto-indexed (lower-trust) entry, the merged entry stays lower-trust too.
    """
    ordered = sorted(members, key=lambda p: (p.get("date", "0000-00-00"), p["filename"]))
    newest = ordered[-1]
    others = ordered[:-1]

    summaries = [m.get("summary", "") for m in ordered if m.get("summary")]
    descriptions = "\n\n---\n\n".join(
        m.get("description", "") for m in ordered if m.get("description")
    )
    designs = "\n\n---\n\n".join(
        m.get("design", "") for m in ordered if m.get("design")
    )

    keywords: list[str] = []
    conditions: list[str] = []
    for m in ordered:
        keywords.extend(m.get("keywords", []))
        conditions.extend(m.get("conditions", []))
    keywords = list(dict.fromkeys(keywords))
    conditions = list(dict.fromkeys(conditions))

    weights = [float(m.get("weight", 1.0)) for m in ordered]
    merged_weight = min(weights) if weights else 1.0

    event_type = newest.get("event_type", "note")
    merged_name = newest.get("event_name", "") or newest["filename"].removesuffix(".md")
    safe = re.sub(r"[^\w\-.]", "_", merged_name).strip("_") or "merged"
    new_filename = f"{event_type}_{safe}.md"

    content = (
        f"Event Type: {event_type}\n"
        f"Event Name: {merged_name}\n"
        f"Date: {newest.get('date', '0000-00-00')}\n"
        f"Summary: {' / '.join(summaries)}\n"
        f"\n## Full Description / Complete Resolution Process\n{descriptions}\n"
        f"\n## Design / Solution\n{designs}\n"
    )
    return content, new_filename, keywords, conditions, merged_weight


def rebuild_memory_index(memory_lines: list[str], entries_to_remove: list[dict],
                         entries_to_add: list[tuple[str, list[str], list[str], float]]) -> str:
    """Remove ranges (descending, so earlier indices stay valid), then append.

    Appended entries carry a weight. We only write the `权重` line when the
    weight is below 1.0 (auto / lower-trust); hand-authored entries stay clean
    and default to full trust on read.
    """
    for start, end in sorted(
        ((e["start_line"], e["end_line"]) for e in entries_to_remove), reverse=True
    ):
        del memory_lines[start:end + 1]

    for filename, keywords, conditions, weight in entries_to_add:
        # Exact match: `filename in line` also matched `x_<filename>` and made
        # the rebuilt index silently drop the entry it had just merged.
        if any(line.strip() == f"- {filename}" for line in memory_lines):
            continue
        key_line = "  触发关键词：" + "、".join(f"“{k}”" for k in keywords)
        cond_line = "  触发条件：" + "、".join(f"“{c}”" for c in conditions)
        weight_line = ""
        if weight is not None and weight < 1.0:
            weight_line = f"\n  权重：{weight:.1f}"
        memory_lines.append(f"\n- {filename}\n{key_line}\n{cond_line}{weight_line}")

    return "\n".join(memory_lines).strip() + "\n"


def backup_files(exp_dir: Path, filenames: list[str], enabled: bool) -> Path | None:
    """Copy the sources aside before anything is deleted."""
    if not enabled:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = exp_dir / ARCHIVE_DIRNAME / stamp
    target.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        src = exp_dir / name
        if src.is_file():
            shutil.copy2(src, target / name)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge similar experiences and cleanup MEMORY.md")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not modify")
    parser.add_argument("--force-merge", help="Comma-separated filenames to merge manually")
    parser.add_argument("--no-backup", action="store_true",
                        help="Delete sources without archiving them first (not recommended)")
    parser.add_argument("--keyword-threshold", type=float, default=AUTO_MERGE_KEYWORD_THRESHOLD)
    parser.add_argument("--content-threshold", type=float, default=AUTO_MERGE_CONTENT_THRESHOLD)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"[ERROR] Workspace not found: {workspace}", file=sys.stderr)
        return 1

    exp_dir = workspace / "experience"
    memory_path = find_memory_md(workspace)

    if not memory_path:
        print("[INFO] No MEMORY.md found. Nothing to do.")
        return 0

    memory_text = memory_path.read_text(encoding="utf-8")
    memory_lines = memory_text.splitlines()
    index_entries = parse_memory_index(memory_text)

    # ================================================================
    # Phase 1: Orphan entries + Unindexed files + Broken files
    # ================================================================
    print("=" * 60)
    print("Phase 1: Scan orphans, unindexed files, broken files")
    print("=" * 60)

    orphan_entries: list[dict] = []
    unindexed_files: list[dict] = []
    broken_files: list[tuple[str, list[str]]] = []

    for entry in index_entries:
        if not (exp_dir / entry["filename"]).exists():
            orphan_entries.append(entry)
            print(f"  [ORPHAN] '{entry['filename']}' — file not found, will remove index")

    if exp_dir.exists():
        existing_files = sorted(
            f.name for f in exp_dir.iterdir() if f.is_file() and f.suffix == ".md"
        )
        indexed_files = {e["filename"] for e in index_entries}

        for fname in existing_files:
            pf = parse_experience_file(exp_dir / fname)
            if pf is None:
                broken_files.append((fname, ["File is unreadable"]))
                print(f"  [BROKEN] '{fname}' — unreadable")
            elif pf.get("missing_fields"):
                broken_files.append((fname, pf["missing_fields"]))
                extra = "indexed but " if fname in indexed_files else ""
                print(f"  [BROKEN] '{fname}' — {extra}missing fields: "
                      f"{', '.join(pf['missing_fields'])}")
            elif fname not in indexed_files:
                unindexed_files.append(pf)
                print(f"  [UNINDEXED] '{fname}' — will auto-index")

    if not orphan_entries and not unindexed_files and not broken_files:
        print("  All clean. ✓")

    # ================================================================
    # Phase 2: Find merge candidates
    # ================================================================
    print()
    print("=" * 60)
    kw_t = args.keyword_threshold
    ct_t = args.content_threshold
    print(f"Phase 2: Find merge candidates (triggers ≥{kw_t:.0%}, content ≥{ct_t:.0%})")
    print("=" * 60)

    parsed_files: list[dict] = []
    for entry in index_entries:
        exp_file = exp_dir / entry["filename"]
        if not exp_file.exists():
            continue
        pf = parse_experience_file(exp_file)
        if pf is None or pf.get("missing_fields"):
            continue
        pf["filename"] = entry["filename"]
        pf["keywords"] = entry["keywords"]
        pf["conditions"] = entry["conditions"]
        pf["weight"] = entry.get("weight", 1.0)
        parsed_files.append(pf)

    by_name = {p["filename"]: p for p in parsed_files}
    pairs: list[tuple[str, str]] = []
    pair_detail: dict[tuple[str, str], tuple[float, float]] = {}

    if args.force_merge:
        wanted = [n.strip() for n in args.force_merge.split(",") if n.strip()]
        missing = [n for n in wanted if n not in by_name]
        if missing:
            print(f"  [ERROR] Not indexed or unreadable: {', '.join(missing)}", file=sys.stderr)
            return 1
        if len(wanted) < 2:
            print("  [ERROR] --force-merge needs at least two filenames", file=sys.stderr)
            return 1
        for i in range(len(wanted)):
            for j in range(i + 1, len(wanted)):
                pairs.append((wanted[i], wanted[j]))
        print(f"  [FORCED] {len(wanted)} files requested for merge")
    else:
        by_type: dict[str, list[dict]] = {}
        for pf in parsed_files:
            by_type.setdefault(pf.get("event_type", "note"), []).append(pf)

        for group in by_type.values():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    t_sim = trigger_similarity(a, b)
                    if t_sim < kw_t:
                        continue
                    c_sim = content_similarity(a, b)
                    if c_sim < ct_t:
                        print(f"  [BELOW THRESHOLD] {a['filename']} ↔ {b['filename']}")
                        print(f"      Triggers: {t_sim:.0%}, Content: {c_sim:.0%}")
                        continue
                    pairs.append((a["filename"], b["filename"]))
                    pair_detail[(a["filename"], b["filename"])] = (t_sim, c_sim)
                    print(f"  ✓ {a['filename']} ↔ {b['filename']}")
                    print(f"      Triggers: {t_sim:.0%}, Content: {c_sim:.0%}")

    groups = [g for g in cluster(list(by_name), pairs) if len(g) > 1]

    if not groups and not orphan_entries and not unindexed_files and not broken_files:
        print("  Nothing to do. ✓")

    if args.dry_run:
        print()
        print("=" * 60)
        print(f"DRY RUN — no changes made. ({len(groups)} cluster(s) would merge)")
        print("=" * 60)
        return 0

    # ================================================================
    # Phase 3: Execute
    # ================================================================
    entries_to_remove: list[dict] = []
    entries_to_add: list[tuple[str, list[str], list[str]]] = []

    if orphan_entries:
        print()
        print("=" * 60)
        print("Phase 3: ① Auto-cleaning orphan index entries")
        print("=" * 60)
        for entry in orphan_entries:
            print(f"  Removed: {entry['filename']} (file missing)")
            entries_to_remove.append(entry)

    if unindexed_files:
        print()
        print("=" * 60)
        print("Phase 3: ② Auto-indexing unindexed experience files")
        print("=" * 60)
        for pf in unindexed_files:
            keywords = extract_keywords_from_experience(pf)
            # A placeholder like "关于<name>的事项" carries no real trigger
            # semantics. Use the event name (or filename stem) as the condition:
            # it fires when a task mentions the topic, which is meaningful.
            topic = pf.get("event_name") or pf["filename"].removesuffix(".md")
            conditions = [topic]
            entries_to_add.append((pf["filename"], keywords, conditions, AUTO_INDEX_WEIGHT))
            print(f"  Indexed: {pf['filename']} ({len(keywords)} keywords, "
                  f"auto-generated, weight {AUTO_INDEX_WEIGHT})")

    merge_count = 0
    if groups:
        print()
        print("=" * 60)
        print(f"Phase 3: ③ Merging {len(groups)} cluster(s)")
        print("=" * 60)
        for group in groups:
            members = [by_name[n] for n in group]
            content, new_filename, keywords, conditions, merged_weight = build_merged_content(members)

            target = exp_dir / new_filename
            if target.exists() and new_filename not in group:
                stem = new_filename.removesuffix(".md")
                new_filename = f"{stem}_merged.md"
                target = exp_dir / new_filename

            archive = backup_files(exp_dir, group, enabled=not args.no_backup)
            if archive:
                print(f"  Archived {len(group)} source(s) → {archive}")

            # Write the result before removing anything.
            write_atomic(target, content)
            print(f"  Created: {new_filename} (weight {merged_weight})")

            for name in group:
                src = exp_dir / name
                if src != target and src.is_file():
                    src.unlink()
                    print(f"  Removed: {name}")

            for entry in index_entries:
                if entry["filename"] in group and entry not in entries_to_remove:
                    entries_to_remove.append(entry)
            entries_to_add.append((new_filename, keywords, conditions, merged_weight))
            merge_count += 1

    if entries_to_remove or entries_to_add:
        write_atomic(memory_path, rebuild_memory_index(
            memory_lines, entries_to_remove, entries_to_add))
        print(f"\n  Updated: {memory_path}")

    if broken_files:
        print()
        print("=" * 60)
        print("Phase 3: ④ Broken files (report only, no auto action)")
        print("=" * 60)
        for fname, missing in broken_files:
            print(f"  ⚠ {fname}")
            for field in missing:
                print(f"       Missing: {field}")

    print(f"\n[DONE] Orphans: {len(orphan_entries)} cleaned, "
          f"Unindexed: {len(unindexed_files)} indexed, "
          f"Merges: {merge_count} done, "
          f"Broken: {len(broken_files)} reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
