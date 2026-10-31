#!/bin/sh
# In-process mocked qualification only. This never starts a listening server.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${QUALIFY_PYTHON:-python3}
python_bin=$(command -v "$python_bin")
work=$(mktemp -d /tmp/crownops-adapter-mock-env.XXXXXX)
output=$(mktemp -d /tmp/crownops-adapter-mock-evidence.XXXXXX)
trap 'rm -rf "$work"' EXIT HUP INT TERM
cd "$root"
env -i PATH="$PATH" HOME="$work" TMPDIR="$work" PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$root:$root/tests" CROWNOPS_ENV=test CROWNOPS_ALLOW_EXTERNAL=0 \
  CROWNOPS_RUN_WORKER=0 QBO_ENV=sandbox \
  "$python_bin" -m synthetic_intake.qualify --output "$output"
