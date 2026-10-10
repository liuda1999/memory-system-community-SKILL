#!/usr/bin/env python3
"""
Create an experience file and update the MEMORY.md index.

Usage (interactive):
    add_experience.py --workspace <path> --interactive

Usage (non-interactive):
    add_experience.py --workspace <path> \\
        --event-type decision \\
        --event-name "api_choice" \\
        --summary "Chose REST over GraphQL for project X" \\
        --description "Long description with full reasoning..." \\
        --design "Technical design details..." \\
        --keywords "API选择,REST,GraphQL,后端决策" \\
        --conditions "API协议选择,数据获取策略评估"

Valid event types: decision, lesson, success, failure, idea, config, note
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


VALID_EVENT_TYPES = {"decision", "lesson", "success", "failure", "idea", "config", "note"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    """Convert to a safe filename stem, keeping CJK and other non-ASCII letters.

    The previous version replaced anything outside `[a-zA-Z0-9_-]` with `_`, so
    a purely Chinese event name collapsed to the empty string: "数据库选型方案"
    became `decision_.md`, and every later Chinese name collided with the first
    one and was rejected as "already exists". Chinese is the common case here,
    so CJK is kept and only characters that are actually unsafe in a path are
    replaced — separators, control characters and the Windows-reserved set.
    """
    # Path separators and anything else that cannot appear in a filename.
    safe = re.sub(r"[\/\\\x00-\x1f<>:\"|?*]", "_", name)
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"_+", "_", safe).strip("_.")
    # Windows reserves these names even with an extension.
    if safe.upper() in {"CON", "PRN", "AUX", "NUL"} or re.match(r"^COM\d$|^LPT\d$", safe.upper()):
        safe = f"_{safe}"
    return safe.lower() or "untitled"


def prompt_input(prompt_text: str, default: str = "") -> str:
    """Prompt for input with optional default.

    Raises a clear error when stdin is not a terminal. The previous version let
    `input()` raise a bare EOFError, which is what an agent calling this with
    --interactive saw — a stack trace and nothing else.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "--interactive needs a terminal; stdin is not a TTY. "
            "An agent should pass --event-type/--event-name/--summary/"
            "--description/--design/--keywords/--conditions instead."
        )
    if default:
        prompt_full = f"{prompt_text} [{default}]: "
    else:
        prompt_full = f"{prompt_text}: "
    value = input(prompt_full).strip()
    return value if value else default


def find_memory_md(workspace: Path) -> Path:
    """Find MEMORY.md in workspace, checking common locations."""
    candidates = [
        workspace / "MEMORY.md",
        workspace / "memory.md",
        workspace / "docs" / "MEMORY.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # default


def append_to_memory_index(memory_path: Path, filename: str, keywords: list[str], conditions: list[str]) -> None:
    """Append a structured entry to MEMORY.md."""
    key_line = "  触发关键词：" + "、".join(f"“{k}”" for k in keywords)
    cond_line = "  触发条件：" + "、".join(f"“{c}”" for c in conditions)

    entry = f"\n- {filename}\n{key_line}\n{cond_line}\n"

    # Check if entry already exists for this file
    if memory_path.exists():
        existing = memory_path.read_text(encoding="utf-8")
        if f"- {filename}" in existing:
            print(f"[WARN] Entry for '{filename}' already exists in MEMORY.md, skipping.")
            return

    with open(memory_path, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"[OK] Added index entry to {memory_path}")


def create_experience(workspace: Path, event_type: str, event_name: str,
                      summary: str, description: str, design: str,
                      keywords: list[str], conditions: list[str]) -> Path | None:
    """Create an experience file and update MEMORY.md."""
    if event_type not in VALID_EVENT_TYPES:
        print(f"[ERROR] Invalid event type: '{event_type}'. Valid: {', '.join(sorted(VALID_EVENT_TYPES))}", file=sys.stderr)
        return None

    safe_name = sanitize_filename(event_name)
    filename = f"{event_type}_{safe_name}.md"

    exp_dir = workspace / "experience"
    ensure_dir(exp_dir)
    exp_file = exp_dir / filename

    if exp_file.exists():
        print(f"[ERROR] Experience file already exists: {exp_file}", file=sys.stderr)
        return None

    today = datetime.now().strftime("%Y-%m-%d")

    content = f"""Event Type: {event_type}
Event Name: {event_name}
Date: {today}
Summary: {summary}

## Full Description / Complete Resolution Process
{description}

## Design / Solution
{design}
"""

    exp_file.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"[OK] Created experience file: {exp_file}")

    # Update MEMORY.md index
    memory_path = find_memory_md(workspace)
    append_to_memory_index(memory_path, filename, keywords, conditions)

    return exp_file


def interactive_mode(workspace: Path) -> Path | None:
    """Run in interactive mode, prompting user for each field."""
    print("\n=== Create New Experience (Interactive) ===\n")

    print(f"Valid event types: {', '.join(sorted(VALID_EVENT_TYPES))}")
    event_type = prompt_input("Event type")
    while event_type not in VALID_EVENT_TYPES:
        print(f"  Must be one of: {', '.join(sorted(VALID_EVENT_TYPES))}")
        event_type = prompt_input("Event type")

    event_name = prompt_input("Event name (short, snake_case)")
    while not event_name:
        event_name = prompt_input("Event name")

    summary = prompt_input("Summary (one line)")
    description = prompt_input("Complete description (several lines)")
    design = prompt_input("Design / solution details")

    keywords_raw = prompt_input("Trigger keywords (comma-separated, at least 3)")
    keywords = [k.strip().strip('"') for k in keywords_raw.split(",") if k.strip()]
    while len(keywords) < 3:
        print(f"  Need at least 3 keywords, got {len(keywords)}")
        keywords_raw = prompt_input("Trigger keywords (comma-separated)")
        keywords = [k.strip().strip('"') for k in keywords_raw.split(",") if k.strip()]

    conditions_raw = prompt_input("Trigger conditions (comma-separated, at least 1)")
    conditions = [c.strip().strip('"') for c in conditions_raw.split(",") if c.strip()]
    while len(conditions) < 1:
        conditions_raw = prompt_input("Trigger conditions (comma-separated)")
        conditions = [c.strip().strip('"') for c in conditions_raw.split(",") if c.strip()]

    print("\nSummary:")
    print(f"  Type: {event_type}")
    print(f"  Name: {event_name}")
    print(f"  Keywords: {keywords}")
    print(f"  Conditions: {conditions}")
    confirm = input("\nCreate this experience? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("[INFO] Cancelled.")
        return None

    return create_experience(workspace, event_type, event_name,
                             summary, description, design,
                             keywords, conditions)


def main():
    parser = argparse.ArgumentParser(description="Create experience file and update MEMORY.md index")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode")
    parser.add_argument("--event-type", choices=sorted(VALID_EVENT_TYPES), help="Type of event")
    parser.add_argument("--event-name",
                        help="Name of the event; Chinese is kept as-is "
                             "(only path separators and Windows reserved "
                             "chars are stripped)")
    parser.add_argument("--summary", help="One-line summary")
    parser.add_argument("--description", help="Full description / resolution")
    parser.add_argument("--design", help="Design and solution details")
    parser.add_argument("--keywords", help="Comma-separated trigger keywords")
    parser.add_argument("--conditions", help="Comma-separated trigger conditions")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"[ERROR] Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    if args.interactive:
        try:
            result = interactive_mode(workspace)
        except RuntimeError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        if not all([args.event_type, args.event_name, args.summary,
                     args.description, args.design, args.keywords, args.conditions]):
            print("[ERROR] Non-interactive mode requires all flags: "
                  "--event-type, --event-name, --summary, --description, --design, "
                  "--keywords, --conditions", file=sys.stderr)
            sys.exit(1)

        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]

        result = create_experience(workspace, args.event_type, args.event_name,
                                    args.summary, args.description, args.design,
                                    keywords, conditions)

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
