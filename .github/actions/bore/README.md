# BORE Scheduler action

This composite action applies the official BORE 6.8.0-rc1 testing patch for
Linux 6.12, pinned to upstream commit
`507dca0bbc4db73f1a08ef03ea6d36e8cb1b8156`.

The upstream release-candidate patch targets Linux 6.12.37. Android common has
ABI/export and comment-layout differences around `sysctl_sched_base_slice`.
The action therefore uses a narrow structural compatibility path:

1. Try the official patch unchanged with zero fuzz.
2. When Android context differs, generate a temporary copy of the official
   patch with only the second `kernel/sched/fair.c` declaration hunk removed.
3. Apply every other official BORE hunk with limited fuzz.
4. Insert the omitted tunable/base-slice declarations by locating the actual C
   variable definitions, not comments or line numbers.
5. Preserve Android's `EXPORT_SYMBOL[_GPL](sysctl_sched_base_slice)` after the
   complete `CONFIG_SCHED_BORE` block.
6. Verify BORE files, hooks, Kconfig, Makefile, declarations, conflict markers,
   rejects, and whitespace.
7. Restore every touched kernel file automatically if any mutation fails.

This avoids both previously observed failures:

- runtime assignments being counted as declarations;
- the official early `fair.c` hunk failing because Android changed its comments
  and protected-export layout.

BORE is enabled for `android16-6.12` when the workflow feature set contains
`BORE`, or when `FULL` is selected. The action explicitly enables:

```text
CONFIG_SCHED_BORE=y
CONFIG_MIN_BASE_SLICE_NS=2000000
```

This is an upstream testing release candidate, not the stable BORE branch. The
optional upstream SMT preference patch is intentionally not applied because GKI
arm64 phone targets generally do not use SMT and it is not required for BORE.

## Regression tests

```bash
python3 .github/actions/bore/tests/test_android_fair_compat.py
```
