#!/usr/bin/env python3
"""Create an Android-compatible BORE patch.

BORE 6.8.0-rc1's second kernel/sched/fair.c hunk rewrites the EEVDF
base-slice declaration area. Android common carries ABI/export changes in the
same area, so matching that hunk by surrounding text is inherently brittle.

This helper removes only that declaration hunk. All other official upstream
hunks are retained verbatim. android_fair_compat.py then applies the equivalent
declaration changes structurally to Android fair.c.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DIFF_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$")
HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")
TARGET = "kernel/sched/fair.c"


def adapt_patch(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    current_file: str | None = None
    removed = 0
    index = 0

    while index < len(lines):
        diff_match = DIFF_RE.match(lines[index].rstrip("\r\n"))
        if diff_match:
            current_file = diff_match.group(2)
            output.append(lines[index])
            index += 1
            continue

        if current_file == TARGET and HUNK_RE.match(lines[index]):
            end = index + 1
            while end < len(lines):
                stripped = lines[end].rstrip("\r\n")
                if HUNK_RE.match(stripped) or DIFF_RE.match(stripped):
                    break
                end += 1

            hunk = "".join(lines[index:end])
            if (
                "sysctl_sched_tunable_scaling" in hunk
                and "sysctl_sched_base_slice" in hunk
                and "normalized_sysctl_sched_base_slice" in hunk
                and "sysctl_sched_min_base_slice" in hunk
                and "nsecs_per_tick" in hunk
            ):
                removed += 1
                index = end
                continue

        output.append(lines[index])
        index += 1

    return "".join(output), removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source = args.input.read_text(encoding="utf-8")
        adapted, removed = adapt_patch(source)
        if removed != 1:
            raise RuntimeError(
                "Expected to remove exactly one BORE fair.c declaration hunk, "
                f"removed {removed}"
            )
        args.output.write_text(adapted, encoding="utf-8")
    except (OSError, UnicodeError, RuntimeError) as error:
        print(f"::error title=BORE patch adapter::{error}", file=sys.stderr)
        return 1

    print("[BORE] Removed one upstream fair.c declaration hunk for Android-aware insertion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
