#!/usr/bin/env bash
set -euo pipefail

readonly BORE_REPOSITORY="https://github.com/firelzrd/bore-scheduler.git"
readonly BORE_COMMIT="507dca0bbc4db73f1a08ef03ea6d36e8cb1b8156"
readonly BORE_PATCH_REL="patches/testing/0001-linux6.12.37-bore-6.8.0-rc1.patch"
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

if grep -q 'SCHED_BORE_VERSION "6\.8\.0-rc1"' include/linux/sched/bore.h 2>/dev/null; then
  log "BORE 6.8.0-rc1 is already present; nothing to do."
  exit 0
fi

tmpdir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/bore.XXXXXX")"
mutation_started=false
completed=false

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

backup_tree() {
  mkdir -p "$tmpdir/backup"
  : > "$tmpdir/originally-missing"
  local file
  for file in "${bore_touched_files[@]}"; do
    if [[ -e "$file" || -L "$file" ]]; then
      mkdir -p "$tmpdir/backup/$(dirname "$file")"
      cp -a -- "$file" "$tmpdir/backup/$file"
    else
      printf '%s\n' "$file" >> "$tmpdir/originally-missing"
    fi
  done
}

restore_tree() {
  local file
  for file in "${bore_touched_files[@]}"; do
    if [[ -e "$tmpdir/backup/$file" || -L "$tmpdir/backup/$file" ]]; then
      mkdir -p "$(dirname "$file")"
      rm -rf -- "$file"
      cp -a -- "$tmpdir/backup/$file" "$file"
    else
      rm -rf -- "$file"
    fi
  done
  for file in "${bore_touched_files[@]}"; do
    rm -f -- "${file}.rej" "${file}.orig"
  done
}

cleanup() {
  local status=$?
  if [[ "$mutation_started" == true && "$completed" != true ]]; then
    warn "BORE integration did not complete; restoring every touched kernel file."
    restore_tree
  fi
  rm -rf -- "$tmpdir"
  return "$status"
}
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

apply_patch_file() {
  local file=$1
  shift
  patch --batch --forward --no-backup-if-mismatch -p1 "$@" < "$file"
}

backup_tree

if apply_patch_file "$patch_file" --dry-run --fuzz=0 >"$tmpdir/exact-dry-run.log" 2>&1; then
  log "The official patch applies directly."
  mutation_started=true
  if ! apply_patch_file "$patch_file" --fuzz=0; then
    die "Official BORE patch failed after a successful dry run."
  fi
else
  log "Direct application differs at Android scheduler context; using structural Android compatibility."

  adapted_patch="$tmpdir/bore-android-adapted.patch"
  python3 "$ACTION_DIR/adapt_bore_patch.py" \
    --input "$patch_file" \
    --output "$adapted_patch"

  if ! apply_patch_file "$adapted_patch" --dry-run --fuzz=3 >"$tmpdir/compat-dry-run.log" 2>&1; then
    {
      echo "Exact dry-run output:"
      cat "$tmpdir/exact-dry-run.log"
      echo
      echo "Android-adapted dry-run output:"
      cat "$tmpdir/compat-dry-run.log"
    } >&2
    die "BORE patch still does not apply after removing only the Android declaration hunk."
  fi

  mutation_started=true
  if ! apply_patch_file "$adapted_patch" --fuzz=3; then
    die "Android-adapted BORE patch failed after a successful dry run."
  fi

  python3 "$ACTION_DIR/android_fair_compat.py" --file kernel/sched/fair.c \
    || die "Unable to install the Android-aware BORE declaration block."
fi

# Fail early if any partially applied patch or unresolved merge artifact exists.
for file in "${bore_touched_files[@]}"; do
  if [[ -e "${file}.rej" || -e "${file}.orig" ]]; then
    printf '%s\n' "${file}.rej" "${file}.orig" >&2
    die "Patch reject/original files were produced."
  fi
done
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
grep -q 'SCHED_BORE_VERSION "6\.8\.0-rc1"' include/linux/sched/bore.h \
  || die "Unexpected or missing BORE version marker."
grep -q 'bore\.o' kernel/sched/Makefile \
  || die "kernel/sched/bore.o was not added to the scheduler Makefile."
grep -q 'update_curr_bore' kernel/sched/fair.c \
  || die "BORE fair-scheduler hooks are missing."
grep -q 'sysctl_sched_min_base_slice = CONFIG_MIN_BASE_SLICE_NS' kernel/sched/fair.c \
  || die "BORE base-slice declaration was not installed."
grep -q 'sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_NONE' kernel/sched/fair.c \
  || die "BORE tunable-scaling declaration was not installed."

# Whitespace errors commonly become hard-to-read compiler diagnostics later.
if ! git diff --check -- "${bore_touched_files[@]}"; then
  die "BORE changes contain whitespace errors."
fi

completed=true
log "BORE Scheduler 6.8.0-rc1 applied successfully to ${BORE_ANDROID_VERSION}-${BORE_KERNEL_VERSION}.${BORE_KERNEL_SUBLEVEL:-x}."
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### BORE Scheduler"
    echo "- Version: **6.8.0-rc1 (testing)**"
    echo "- Source commit: \`${BORE_COMMIT}\`"
    echo "- Kernel: \`${BORE_ANDROID_VERSION}-${BORE_KERNEL_VERSION}.${BORE_KERNEL_SUBLEVEL:-x}\`"
    echo "- Config: \`CONFIG_SCHED_BORE=y\`, \`CONFIG_MIN_BASE_SLICE_NS=2000000\`"
    echo "- Android compatibility: structural fair.c declaration insertion"
  } >> "$GITHUB_STEP_SUMMARY"
fi
