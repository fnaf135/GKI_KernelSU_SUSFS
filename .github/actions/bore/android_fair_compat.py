#!/usr/bin/env python3
"""Insert BORE's base-slice declarations into Android common fair.c.

The official BORE patch targets upstream Linux 6.12.37. Android common keeps
ABI/export changes around sysctl_sched_base_slice, so the declaration hunk is
applied structurally instead of by textual patch context.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

TUNABLE_NAME = "sysctl_sched_tunable_scaling"
BASE_NAME = "sysctl_sched_base_slice"
NORMALIZED_NAME = "normalized_sysctl_sched_base_slice"
MIN_BASE_NAME = "sysctl_sched_min_base_slice"
EXPORT_RE = re.compile(
    r"^\s*EXPORT_SYMBOL(?:_GPL)?\(\s*sysctl_sched_base_slice\s*\);\s*$"
)

# Match only C variable definitions, never runtime assignments. Android fair.c
# assigns these scheduler tunables again while rescaling them.
TYPE_RE = (
    r"(?:unsigned\s+int|unsigned\s+long(?:\s+long)?|uint|"
    r"u(?:8|16|32|64))"
)
QUALIFIER_RE = r"(?:static|const|volatile|const_debug|__read_mostly|__ro_after_init)"
ATTRIBUTE_RE = r"__\w+(?:\([^;]*?\))?"


def declaration_re(symbol: str) -> re.Pattern[str]:
    return re.compile(
        rf"""^\s*
        (?:{QUALIFIER_RE}\s+)*
        {TYPE_RE}
        (?:\s+{ATTRIBUTE_RE})*
        \s+{re.escape(symbol)}\b
        (?:\s+{ATTRIBUTE_RE})*
        \s*=\s*[^;]+;\s*(?://.*)?$
        """,
        re.VERBOSE,
    )


def atomic_write(path: Path, text: str) -> None:
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
    pattern = declaration_re(symbol)
    matches = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(matches) != 1:
        references = [
            f"{index + 1}:{line.strip()}"
            for index, line in enumerate(lines)
            if re.search(rf"\b{re.escape(symbol)}\b", line)
        ][:10]
        detail = f" References: {' | '.join(references)}" if references else ""
        raise RuntimeError(
            f"Expected exactly one typed declaration of {symbol}, "
            f"found {len(matches)}.{detail}"
        )
    return matches[0]


def line_ending(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else "\n"


def indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def apply_android_declarations(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    if "sysctl_sched_min_base_slice = CONFIG_MIN_BASE_SLICE_NS" in text:
        print("[BORE] Android fair.c declaration block is already installed.")
        return

    lines = text.splitlines(keepends=True)
    tunable_index = declaration_index(lines, TUNABLE_NAME)
    base_index = declaration_index(lines, BASE_NAME)
    normalized_index = declaration_index(lines, NORMALIZED_NAME)

    if not (tunable_index < base_index and base_index < normalized_index):
        raise RuntimeError(
            "Unexpected scheduler declaration order: expected tunable scaling, "
            "base slice, then normalized base slice"
        )

    exports = [line.rstrip("\r\n") for line in lines if EXPORT_RE.match(line)]
    lines = [line for line in lines if not EXPORT_RE.match(line)]

    # Export removal may shift indexes; locate definitions again.
    tunable_index = declaration_index(lines, TUNABLE_NAME)
    base_index = declaration_index(lines, BASE_NAME)
    normalized_index = declaration_index(lines, NORMALIZED_NAME)

    tunable = lines[tunable_index].rstrip("\r\n")
    newline = line_ending(lines[tunable_index])
    indent = indent_of(lines[tunable_index])
    tunable_block = [
        f"{indent}#ifdef CONFIG_SCHED_BORE{newline}",
        f"{indent}unsigned int {TUNABLE_NAME} = SCHED_TUNABLESCALING_NONE;{newline}",
        f"{indent}#else /* !CONFIG_SCHED_BORE */{newline}",
        f"{tunable}{newline}",
        f"{indent}#endif /* CONFIG_SCHED_BORE */{newline}",
    ]
    lines[tunable_index : tunable_index + 1] = tunable_block

    # Re-locate after tunable block insertion.
    base_index = declaration_index(lines, BASE_NAME)
    normalized_index = declaration_index(lines, NORMALIZED_NAME)
    newline = line_ending(lines[base_index])
    indent = indent_of(lines[base_index])

    bore_prefix = [
        f"{indent}#ifdef CONFIG_SCHED_BORE{newline}",
        f"{indent}static const unsigned int nsecs_per_tick = 1000000000ULL / HZ;{newline}",
        f"{indent}unsigned int {MIN_BASE_NAME} = CONFIG_MIN_BASE_SLICE_NS;{newline}",
        f"{indent}__read_mostly uint {BASE_NAME} = nsecs_per_tick;{newline}",
        f"{indent}#else /* !CONFIG_SCHED_BORE */{newline}",
    ]
    lines[base_index:base_index] = bore_prefix

    # The normalized declaration moved by the inserted prefix.
    normalized_index = declaration_index(lines, NORMALIZED_NAME)
    newline = line_ending(lines[normalized_index])
    footer = [f"{indent}#endif /* CONFIG_SCHED_BORE */{newline}"]
    footer.extend(f"{export}{newline}" for export in exports)
    lines[normalized_index + 1 : normalized_index + 1] = footer

    result = "".join(lines)
    required = (
        "unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_NONE;",
        "static const unsigned int nsecs_per_tick = 1000000000ULL / HZ;",
        "unsigned int sysctl_sched_min_base_slice = CONFIG_MIN_BASE_SLICE_NS;",
        "__read_mostly uint sysctl_sched_base_slice = nsecs_per_tick;",
    )
    for marker in required:
        if result.count(marker) != 1:
            raise RuntimeError(f"Generated fair.c has an invalid marker count: {marker}")
    for export in exports:
        if result.count(export) != 1:
            raise RuntimeError(f"Failed to preserve Android export: {export}")

    atomic_write(source, result)
    print(
        "[BORE] Installed Android-aware tunable/base-slice declarations "
        f"and preserved {len(exports)} export line(s)."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        apply_android_declarations(args.file)
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f"::error title=BORE Android compatibility::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
