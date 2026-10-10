#!/usr/bin/env python3
"""
Append a conversation turn to today's raw daily log.

This is the batch/backfill entry point; append_turn.py is the per-turn fast path.
Both write the same format to memory/YYYY-MM-DD.md.

Usage (safe — content comes from files):
    daily_log.py --workspace <path> --user-file u.txt --reply-file r.txt

Usage (safe — content comes from stdin):
    printf '%s\\n%s\\n%s' "$USER_MSG" '%%REPLY%%' "$REPLY" |
        daily_log.py --workspace <path> --stdin

The previous version took --user-msg/--reply on the command line. That is a
command-injection hole: a reply quoting `$(...)` or backticks is expanded by the
shell before Python ever sees it. Content now travels by file or stdin only.
"""

import argparse
import sys
from pathlib import Path

try:
    from append_turn import REPLY_MARKER, append_turn, split_stdin, read_text
except ImportError:  # executed from another directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from append_turn import REPLY_MARKER, append_turn, split_stdin, read_text


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
    args = parser.parse_args()

    if args.stdin:
        user_msg, reply = split_stdin(sys.stdin.read())
    elif args.user_file and args.reply_file:
        user_msg = read_text(args.user_file)
        reply = read_text(args.reply_file)
    else:
        print("[ERROR] no content source: pass --user-file with --reply-file, or --stdin",
              file=sys.stderr)
        return 1

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"[ERROR] Workspace not found: {workspace}", file=sys.stderr)
        return 1

    log_file = append_turn(workspace, user_msg, reply)
    print(f"[OK] Appended turn to {log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
