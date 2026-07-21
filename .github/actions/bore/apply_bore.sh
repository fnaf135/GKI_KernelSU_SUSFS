#!/usr/bin/env bash
set -euo pipefail

readonly BORE_REPOSITORY="https://github.com/firelzrd/bore-scheduler.git"
readonly BORE_COMMIT="16bf5baebbb42cdba393c501ba9c2af5f84e4749"
readonly BORE_PATCH_REL="patches/stable/linux-6.12-bore/0001-linux6.12.37-bore-6.6.3.patch"
readonly ACTION_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KERNEL_ROOT="$(pwd)"

log() { printf '[BORE] %s\n' "$*"; }
warn() { printf '::warning title=BORE Scheduler::%s\n' "$*"; }
die() { printf '::error title=BORE Scheduler::%s\n' "$*" >&2; exit 1; }

if [[ "${BORE_KERNEL_VERSION:-}" != "6.12" ]]; then
  die "Unsupported kernel ${BORE_KERNEL_VERSION:-unknown}; this action currently targets Linux 6.12 only."
fi
if [[ "${BORE_ANDROID_VERSION:-}" != "android16" ]]; then
  die "Unsupported Android branch ${BORE_ANDROID_VERSION:-unknown}; this action currently targets android16-6.12."
fi
if [[ ! -f Makefile || ! -f kernel/sched/fair.c ]]; then
  die "Run this action from the root of kernel/common."
fi

if grep -q 'SCHED_BORE_VERSION "6\.6\.3"' include/linux/sched/bore.h 2>/dev/null; then
  log "BORE 6.6.3 is already present; nothing to do."
  exit 0
fi

tmpdir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/bore.XXXXXX")"
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT

log "Downloading official BORE source at ${BORE_COMMIT}"
git -C "$tmpdir" init --quiet bore
git -C "$tmpdir/bore" remote add origin "$BORE_REPOSITORY"
fetched=false
for attempt in 1 2 3; do
  if git -C "$tmpdir/bore" fetch --quiet --depth=1 origin "$BORE_COMMIT"; then
    fetched=true
    break
  fi
  warn "BORE download attempt ${attempt}/3 failed."
  sleep $((attempt * 3))
done
[[ "$fetched" == true ]] || die "Unable to fetch ${BORE_REPOSITORY} at ${BORE_COMMIT}."
git -C "$tmpdir/bore" -c advice.detachedHead=false checkout --quiet --detach FETCH_HEAD \
  || die "Unable to check out pinned BORE commit ${BORE_COMMIT}."

actual_commit="$(git -C "$tmpdir/bore" rev-parse HEAD)"
[[ "$actual_commit" == "$BORE_COMMIT" ]] \
  || die "Pinned BORE commit verification failed: got ${actual_commit}."

patch_file="$tmpdir/bore/$BORE_PATCH_REL"
[[ -s "$patch_file" ]] || die "Official patch not found: ${BORE_PATCH_REL}."
log "Using $(basename "$patch_file") from ${actual_commit}"

# Keep a pristine copy because a failed patch command must never leave fair.c
# in its temporary compatibility form.
cp -- kernel/sched/fair.c "$tmpdir/fair.c.original"
state_file="$tmpdir/android-fair-state.json"
restore_original_fair() {
  cp -- "$tmpdir/fair.c.original" kernel/sched/fair.c
}

apply_patch() {
  patch --batch --forward -p1 "$@" < "$patch_file"
}

if apply_patch --dry-run --fuzz=0 >"$tmpdir/exact-dry-run.log" 2>&1; then
  log "The official patch applies directly."
  apply_patch --fuzz=0
else
  log "Direct application differs at Android scheduler context; enabling compatibility mode."

  python3 "$ACTION_DIR/android_fair_compat.py" prepare \
    --file kernel/sched/fair.c \
    --state "$state_file"

  if ! apply_patch --dry-run --fuzz=3 >"$tmpdir/compat-dry-run.log" 2>&1; then
    restore_original_fair
    {
      echo "Exact dry-run output:"
      cat "$tmpdir/exact-dry-run.log"
      echo
      echo "Compatibility dry-run output:"
      cat "$tmpdir/compat-dry-run.log"
    } >&2
    die "BORE patch still does not apply. The source was restored unchanged; inspect the dry-run output above."
  fi

  if ! apply_patch --fuzz=3; then
    restore_original_fair
    die "BORE patch application failed after a successful dry run; fair.c was restored."
  fi

  python3 "$ACTION_DIR/android_fair_compat.py" restore \
    --file kernel/sched/fair.c \
    --state "$state_file"
fi

# Fail early if any partially applied patch or unresolved merge artifact exists.
if find . -type f \( -name '*.rej' -o -name '*.orig' \) -print -quit | grep -q .; then
  find . -type f \( -name '*.rej' -o -name '*.orig' \) -print >&2
  die "Patch reject/original files were produced."
fi
if grep -RIl --exclude-dir=.git '^<<<<<<<\|^=======\|^>>>>>>>' \
    include/linux/sched.h include/linux/sched kernel init 2>/dev/null | grep -q .; then
  die "Conflict markers remain after applying BORE."
fi

required_files=(
  include/linux/sched/bore.h
  kernel/sched/bore.c
)
for required in "${required_files[@]}"; do
  [[ -s "$required" ]] || die "Expected BORE file is missing: ${required}."
done

grep -q 'config SCHED_BORE' init/Kconfig \
  || die "CONFIG_SCHED_BORE was not added to init/Kconfig."
grep -q 'SCHED_BORE_VERSION "6\.6\.3"' include/linux/sched/bore.h \
  || die "Unexpected or missing BORE version marker."
grep -q 'bore\.o' kernel/sched/Makefile \
  || die "kernel/sched/bore.o was not added to the scheduler Makefile."
grep -q 'update_curr_bore' kernel/sched/fair.c \
  || die "BORE fair-scheduler hooks are missing."

# Whitespace errors commonly become hard-to-read compiler diagnostics later.
bore_touched_files=(
  include/linux/sched.h
  include/linux/sched/bore.h
  init/Kconfig
  kernel/Kconfig.hz
  kernel/exit.c
  kernel/fork.c
  kernel/futex/waitwake.c
  kernel/sched/Makefile
  kernel/sched/bore.c
  kernel/sched/core.c
  kernel/sched/debug.c
  kernel/sched/fair.c
  kernel/sched/sched.h
)
if ! git diff --check -- "${bore_touched_files[@]}"; then
  die "BORE changes contain whitespace errors."
fi

log "BORE Scheduler 6.6.3 applied successfully to ${BORE_ANDROID_VERSION}-${BORE_KERNEL_VERSION}.${BORE_KERNEL_SUBLEVEL:-x}."
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### BORE Scheduler"
    echo "- Version: **6.6.3**"
    echo "- Source commit: \`${BORE_COMMIT}\`"
    echo "- Kernel: \`${BORE_ANDROID_VERSION}-${BORE_KERNEL_VERSION}.${BORE_KERNEL_SUBLEVEL:-x}\`"
    echo "- Config: \`CONFIG_SCHED_BORE=y\`, \`CONFIG_MIN_BASE_SLICE_NS=2000000\`"
  } >> "$GITHUB_STEP_SUMMARY"
fi
