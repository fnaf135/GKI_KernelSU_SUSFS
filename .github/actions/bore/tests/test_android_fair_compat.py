#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, filename: str):
    path = ACTION_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = load_module("android_fair_compat", "android_fair_compat.py")
adapter = load_module("adapt_bore_patch", "adapt_bore_patch.py")


class AndroidFairCompatTest(unittest.TestCase):
    def test_structural_insertion_preserves_android_export_and_assignments(self) -> None:
        original = """/* scheduler tunables */
unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;

/* Android keeps a protected export in this area. */
unsigned int sysctl_sched_base_slice = 700000ULL;
EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);
static unsigned int normalized_sysctl_sched_base_slice = 700000ULL;
const_debug unsigned int sysctl_sched_migration_cost = 500000UL;

static void update_sysctl(void)
{
\tsysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();
\tnormalized_sysctl_sched_base_slice = sysctl_sched_base_slice;
\tsysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);
}
"""

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fair.c"
            source.write_text(original, encoding="utf-8")
            compat.apply_android_declarations(source)
            result = source.read_text(encoding="utf-8")

        self.assertIn("#ifdef CONFIG_SCHED_BORE", result)
        self.assertIn(
            "unsigned int sysctl_sched_tunable_scaling = "
            "SCHED_TUNABLESCALING_NONE;",
            result,
        )
        self.assertIn(
            "unsigned int sysctl_sched_tunable_scaling = "
            "SCHED_TUNABLESCALING_LOG;",
            result,
        )
        self.assertIn(
            "static const unsigned int nsecs_per_tick = 1000000000ULL / HZ;",
            result,
        )
        self.assertIn(
            "unsigned int sysctl_sched_min_base_slice = CONFIG_MIN_BASE_SLICE_NS;",
            result,
        )
        self.assertIn(
            "__read_mostly uint sysctl_sched_base_slice = nsecs_per_tick;",
            result,
        )
        self.assertEqual(result.count("EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);"), 1)
        self.assertIn(
            "sysctl_sched_base_slice = 700000ULL * get_update_sysctl_factor();",
            result,
        )
        self.assertIn(
            "sysctl_sched_base_slice = max(sysctl_sched_base_slice, 1U);",
            result,
        )
        self.assertLess(
            result.index("#endif /* CONFIG_SCHED_BORE */\nEXPORT_SYMBOL_GPL"),
            result.index("const_debug unsigned int sysctl_sched_migration_cost"),
        )

    def test_structural_insertion_is_idempotent(self) -> None:
        source_text = """unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;
unsigned int sysctl_sched_base_slice = 700000ULL;
static unsigned int normalized_sysctl_sched_base_slice = 700000ULL;
"""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fair.c"
            source.write_text(source_text, encoding="utf-8")
            compat.apply_android_declarations(source)
            once = source.read_text(encoding="utf-8")
            compat.apply_android_declarations(source)
            twice = source.read_text(encoding="utf-8")
        self.assertEqual(once, twice)

    def test_typed_declaration_matcher_rejects_plain_assignment(self) -> None:
        declaration = "unsigned int sysctl_sched_base_slice = 700000ULL;\n"
        assignment = "sysctl_sched_base_slice = 700000ULL * factor;\n"
        pattern = compat.declaration_re("sysctl_sched_base_slice")
        self.assertIsNotNone(pattern.match(declaration))
        self.assertIsNone(pattern.match(assignment))


class BorePatchAdapterTest(unittest.TestCase):
    def test_removes_only_the_fair_declaration_hunk(self) -> None:
        fixture = """From test Mon Sep 17 00:00:00 2001
diff --git a/kernel/sched/fair.c b/kernel/sched/fair.c
--- a/kernel/sched/fair.c
+++ b/kernel/sched/fair.c
@@ -55,2 +55,3 @@
 context
+include bore
@@ -64,4 +68,8 @@
 unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;
 unsigned int sysctl_sched_base_slice = 700000ULL;
 static unsigned int normalized_sysctl_sched_base_slice = 700000ULL;
+unsigned int sysctl_sched_min_base_slice = CONFIG_MIN_BASE_SLICE_NS;
+static const unsigned int nsecs_per_tick = 1000000000ULL / HZ;
@@ -188,2 +205,3 @@
 update function
+BORE update function
diff --git a/kernel/sched/sched.h b/kernel/sched/sched.h
--- a/kernel/sched/sched.h
+++ b/kernel/sched/sched.h
@@ -1,1 +1,2 @@
 context
+change
"""
        adapted, removed = adapter.adapt_patch(fixture)
        self.assertEqual(removed, 1)
        self.assertIn("@@ -55,2 +55,3 @@", adapted)
        self.assertIn("@@ -188,2 +205,3 @@", adapted)
        self.assertIn("diff --git a/kernel/sched/sched.h", adapted)
        self.assertNotIn("@@ -64,4 +68,8 @@", adapted)
        self.assertNotIn("sysctl_sched_min_base_slice", adapted)


if __name__ == "__main__":
    unittest.main()
