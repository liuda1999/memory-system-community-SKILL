#!/usr/bin/env python3
"""
Quick-turn logger: append the current conversation turn to today's daily log.
Call this at the END of each assistant response, BEFORE any other actions.

Usage (safe — content comes from files):
    python3 append_turn.py --workspace <path> --user-file u.txt --reply-file r.txt

Usage (safe — content comes from stdin):
    printf '%s\\n%s\\n%s' "$USER_MSG" '%%REPLY%%' "$REPLY" |
        python3 append_turn.py --workspace <path> --stdin

Why content is never taken from argv
------------------------------------
A reply is arbitrary text that may quote a web page, a file, or a user message.
Passing it on the command line means the shell parses it: a reply containing
`$(...)` or backticks executes as a command. Measured on the previous version,
`append_turn.py <ws> "..." "参考内容：$(touch /tmp/PWNED)"` ran the payload.
So argv carries the workspace only; content arrives by file or stdin.

Exit behaviour: silent by design. Errors go to stderr and the exit code stays 0
so a failed log never breaks the conversation. Pass --strict to get exit 1 on
failure instead (useful in tests and cron).
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Separator between the user block and the reply block when reading stdin.
REPLY_MARKER = "%%REPLY%%"


def read_text(path: str) -> str:
    """Read one block of text from a file, tolerating odd bytes."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def split_stdin(raw: str) -> tuple[str, str]:
    """Split stdin on the first line that is exactly the reply marker."""
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == REPLY_MARKER:
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i + 1:]).strip()
    raise ValueError(f"stdin has no '{REPLY_MARKER}' separator line")


def append_turn(workspace: Path, user_msg: str, reply: str) -> Path:
    """Append one turn to memory/YYYY-MM-DD.md. Returns the log file."""
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    log_file = memory_dir / f"{date_str}.md"

    if not log_file.exists():
        log_file.write_text(f"# Daily log for {date_str}\n\n", encoding="utf-8")

    # One write() call, so two concurrent turns interleave at block boundaries
    # rather than inside a line.
    block = (
        f"## [{time_str}] User\n{user_msg}\n\n"
        f"## [{time_str}] Assistant\n{reply}\n\n"
    )
    with open(log_file, "a", encoding="utf-8", errors="replace") as f:
        f.write(block)
    return log_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append a conversation turn to today's daily log",
        epilog="Turn content must be passed by file or stdin, never by argv.",
    )
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--user-file", help="File containing the user message")
    parser.add_argument("--reply-file", help="File containing the assistant reply")
    parser.add_argument("--stdin", action="store_true",
                        help=f"Read from stdin, blocks separated by a '{REPLY_MARKER}' line")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on failure")
    parser.add_argument("--verbose", action="store_true", help="Print the log path on success")
    args = parser.parse_args()

    try:
        if args.stdin:
            user_msg, reply = split_stdin(sys.stdin.read())
        elif args.user_file and args.reply_file:
            user_msg = read_text(args.user_file)
            reply = read_text(args.reply_file)
        else:
            raise ValueError(
                "no content source: pass --user-file with --reply-file, or --stdin"
            )

        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir():
            raise NotADirectoryError(f"workspace not found: {workspace}")

        log_file = append_turn(workspace, user_msg, reply)
        if args.verbose:
            print(f"[OK] Appended turn to {log_file}")
        return 0
    except Exception as exc:  # a failed log must never kill the turn
        print(f"[WARN] append_turn failed: {exc}", file=sys.stderr)
        return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
