# BORE Scheduler action

This composite action applies the official BORE 6.6.3 patch for Linux 6.12,
pinned to upstream commit `16bf5baebbb42cdba393c501ba9c2af5f84e4749`.

The upstream patch targets Linux 6.12.37. Android common has a small ABI/export
difference around `sysctl_sched_base_slice`, so the action uses a narrow
compatibility pass:

1. Try the official patch unchanged with zero fuzz.
2. If that fails, temporarily normalize only the two base-slice declarations
   and remove their Android export line.
3. Apply the same official patch with limited fuzz.
4. Restore the Android declaration and export in the non-BORE branch.
5. Verify the BORE files, hooks, Kconfig entry, Makefile entry, conflict markers,
   reject files, and whitespace.

BORE is enabled for `android16-6.12` when the workflow feature set contains
`BORE`, or when `FULL` is selected. The action explicitly enables:

```text
CONFIG_SCHED_BORE=y
CONFIG_MIN_BASE_SLICE_NS=2000000
```

The optional upstream SMT preference patch is intentionally not applied because
GKI arm64 phone targets generally do not use SMT and it is not required for BORE.
