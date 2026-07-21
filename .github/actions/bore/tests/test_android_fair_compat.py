#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "android_fair_compat.py"
SPEC = importlib.util.spec_from_file_location("android_fair_compat", MODULE_PATH)
assert SPEC and SPEC.loader
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


class AndroidFairCompatTest(unittest.TestCase):
    def test_prepare_ignores_runtime_assignments_and_restore_preserves_them(self) -> None:
        original = """/* scheduler tunables */
unsigned int sysctl_sched_base_slice = 700000ULL;
EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);
static unsigned int normalized_sysctl_sched_base_slice = 700000ULL;

static void update_sysctl(void)
{
\tsysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();
\tnormalized_sysctl_sched_base_slice = sysctl_sched_base_slice;
\tsysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);
}
"""
        patched = """/* scheduler tunables */
#ifdef CONFIG_SCHED_BORE
unsigned int sysctl_sched_base_slice = 0ULL;
static unsigned int normalized_sysctl_sched_base_slice = 0ULL;
static u64 nsecs_per_tick = 1000000ULL;
#else
unsigned int sysctl_sched_base_slice = 700000ULL;
static unsigned int normalized_sysctl_sched_base_slice = 700000ULL;
#endif

static void update_sysctl(void)
{
\tsysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();
\tnormalized_sysctl_sched_base_slice = sysctl_sched_base_slice;
\tsysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);
}
"""

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fair.c"
            state = Path(temporary) / "state.json"
            source.write_text(original, encoding="utf-8")

            compat.prepare(source, state)
            prepared = source.read_text(encoding="utf-8")
            self.assertNotIn("EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);", prepared)
            self.assertIn(
                "sysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();",
                prepared,
            )
            self.assertIn(
                "sysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);",
                prepared,
            )

            source.write_text(patched, encoding="utf-8")
            compat.restore(source, state)
            restored = source.read_text(encoding="utf-8")
            self.assertIn("EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);", restored)
            self.assertIn(
                "sysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();",
                restored,
            )
            self.assertIn(
                "sysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);",
                restored,
            )
            self.assertFalse(state.exists())

    def test_typed_declaration_matcher_rejects_plain_assignment(self) -> None:
        declaration = "unsigned int sysctl_sched_base_slice = 700000ULL;\n"
        assignment = "sysctl_sched_base_slice = 700000ULL * factor;\n"
        pattern = compat.declaration_re("sysctl_sched_base_slice")
        self.assertIsNotNone(pattern.match(declaration))
        self.assertIsNone(pattern.match(assignment))


if __name__ == "__main__":
    unittest.main()
