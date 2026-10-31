#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${QUALIFY_PYTHON:-python3}
python_bin=$(command -v "$python_bin")
work=$(mktemp -d /tmp/current-mail-qualification.XXXXXX)
output=$(mktemp -d /tmp/current-mail-evidence.XXXXXX)
trap 'rm -rf "$work"' EXIT HUP INT TERM
cd "$root"
env -i PATH="$PATH" HOME="$work" TMPDIR="$work" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root:$root/tests" \
 CROWNOPS_ENV=test CROWNOPS_ALLOW_EXTERNAL=0 CROWNOPS_RUN_WORKER=0 \
 "$python_bin" -m unittest test_current_styled_mail -v > "$output/tests.log" 2>&1
env -i PATH="$PATH" HOME="$work" TMPDIR="$work" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root:$root/tests" \
 CROWNOPS_ENV=test CROWNOPS_ALLOW_EXTERNAL=0 CROWNOPS_RUN_WORKER=0 \
 "$python_bin" tests/replay_current_styled_mail.py "$output/results.json"
printf 'Evidence: %s\n' "$output"
