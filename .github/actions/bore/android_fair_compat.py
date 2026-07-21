#!/usr/bin/env python3
"""Adapt Android's exported EEVDF base-slice declaration for the BORE patch.

The official BORE patch targets upstream Linux 6.12.37. Android common keeps
small ABI/export changes around sysctl_sched_base_slice. This tool temporarily
normalizes only those declarations, lets GNU patch apply the official diff,
and then restores Android's declarations/export in the BORE #else branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

BASE_NAME = "sysctl_sched_base_slice"
NORMALIZED_NAME = "normalized_sysctl_sched_base_slice"
EXPORT_RE = re.compile(
    r"^\s*EXPORT_SYMBOL(?:_GPL)?\(\s*sysctl_sched_base_slice\s*\);\s*$"
)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def declaration_index(lines: list[str], symbol: str) -> int:
    candidates: list[int] = []
    token = re.compile(rf"\b{re.escape(symbol)}\b")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if (
            token.search(line)
            and "=" in line
            and stripped.endswith(";")
            and not stripped.startswith("#")
            and "(" not in line.split("=", 1)[0]
        ):
            candidates.append(index)
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one declaration of {symbol}, found {len(candidates)}"
        )
    return candidates[0]


def prepare(source: Path, state_path: Path) -> None:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    base_index = declaration_index(lines, BASE_NAME)
    normalized_index = declaration_index(lines, NORMALIZED_NAME)
    exports = [line.rstrip("\r\n") for line in lines if EXPORT_RE.match(line)]

    state: dict[str, Any] = {
        "base_declaration": lines[base_index].rstrip("\r\n"),
        "normalized_declaration": lines[normalized_index].rstrip("\r\n"),
        "exports": exports,
    }

    base_indent = lines[base_index][: len(lines[base_index]) - len(lines[base_index].lstrip())]
    normalized_indent = lines[normalized_index][
        : len(lines[normalized_index]) - len(lines[normalized_index].lstrip())
    ]
    newline = "\r\n" if lines[base_index].endswith("\r\n") else "\n"
    lines[base_index] = (
        f"{base_indent}unsigned int sysctl_sched_base_slice = 700000ULL;{newline}"
    )
    lines[normalized_index] = (
        f"{normalized_indent}static unsigned int "
        f"normalized_sysctl_sched_base_slice = 700000ULL;{newline}"
    )
    lines = [line for line in lines if not EXPORT_RE.match(line)]

    atomic_write(state_path, json.dumps(state, indent=2) + "\n")
    atomic_write(source, "".join(lines))
    print(
        "[BORE] Normalized Android base-slice declarations "
        f"(saved {len(exports)} export line(s))."
    )


def find_bore_declaration_block(lines: list[str]) -> tuple[int, int, int]:
    for start, line in enumerate(lines):
        if line.strip() != "#ifdef CONFIG_SCHED_BORE":
            continue

        depth = 0
        else_index: int | None = None
        for index in range(start, len(lines)):
            directive = lines[index].lstrip()
            if directive.startswith(("#if ", "#if\t", "#ifdef ", "#ifndef ")):
                depth += 1
            elif directive.startswith("#endif"):
                depth -= 1
                if depth == 0:
                    block = "".join(lines[start : index + 1])
                    if (
                        "nsecs_per_tick" in block
                        and BASE_NAME in block
                        and NORMALIZED_NAME in block
                        and else_index is not None
                    ):
                        return start, else_index, index
                    break
            elif directive.startswith("#else") and depth == 1:
                else_index = index

    raise RuntimeError("Unable to locate the BORE base-slice declaration block")


def replace_one_declaration(
    lines: list[str], start: int, end: int, symbol: str, replacement: str
) -> None:
    matches = [
        index
        for index in range(start, end)
        if re.search(rf"\b{re.escape(symbol)}\b", lines[index])
        and "=" in lines[index]
        and lines[index].strip().endswith(";")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one {symbol} declaration in BORE #else branch, "
            f"found {len(matches)}"
        )
    newline = "\r\n" if lines[matches[0]].endswith("\r\n") else "\n"
    lines[matches[0]] = replacement + newline


def restore(source: Path, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    _, else_index, end_index = find_bore_declaration_block(lines)
    replace_one_declaration(
        lines,
        else_index + 1,
        end_index,
        BASE_NAME,
        state["base_declaration"],
    )
    replace_one_declaration(
        lines,
        else_index + 1,
        end_index,
        NORMALIZED_NAME,
        state["normalized_declaration"],
    )

    # Re-find after replacements for defensive correctness, then preserve the
    # Android ABI export after the entire CONFIG_SCHED_BORE declaration block.
    _, _, end_index = find_bore_declaration_block(lines)
    exports: list[str] = state.get("exports", [])
    existing_text = "".join(lines)
    additions = [export for export in exports if export not in existing_text]
    if additions:
        newline = "\r\n" if lines[end_index].endswith("\r\n") else "\n"
        lines[end_index + 1 : end_index + 1] = [
            f"{export}{newline}" for export in additions
        ]

    atomic_write(source, "".join(lines))
    state_path.unlink(missing_ok=True)
    print(
        "[BORE] Restored Android base-slice declaration and "
        f"{len(additions)} export line(s)."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "restore"))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.mode == "prepare":
            prepare(args.file, args.state)
        else:
            restore(args.file, args.state)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"::error title=BORE Android compatibility::{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
