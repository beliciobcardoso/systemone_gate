#!/usr/bin/env bash
# Local quality gate (intentionally not wired to CI): ruff lint, ruff format, mypy, pytest.
# Stops at the first failure. Usage: scripts/check.sh   (FORMAT_ALL=1 to format-check every file)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

for mod in ruff mypy pytest; do
    if ! "$PY" -m "$mod" --version >/dev/null 2>&1; then
        echo "error: '$mod' is not installed for $PY. Install the dev extra: pip install -e \".[dev]\"" >&2
        exit 1
    fi
done

step() { printf '\n==> %s\n' "$1"; }

# Formatting is enforced only on Python files added since the base branch (a ratchet): the
# existing files predate the formatter and are not mass-reformatted. FORMAT_ALL=1 checks everything.
format_targets() {
    if [ "${FORMAT_ALL:-0}" = "1" ]; then
        echo "."
        return
    fi
    local base="" ref
    for ref in origin/dev dev origin/main main; do
        if git rev-parse --verify --quiet "$ref" >/dev/null; then
            base="$(git merge-base HEAD "$ref" 2>/dev/null || true)"
            [ -n "$base" ] && break
        fi
    done
    [ -n "$base" ] || return 0
    { git diff --name-only --diff-filter=A "$base" -- '*.py'
      git ls-files --others --exclude-standard -- '*.py'; } | sort -u
}

step "ruff check"
"$PY" -m ruff check .

step "ruff format --check"
targets="$(format_targets)"
if [ -n "$targets" ]; then
    # shellcheck disable=SC2086
    "$PY" -m ruff format --check $targets
else
    echo "no new Python files to format-check (FORMAT_ALL=1 checks everything)"
fi

step "mypy"
"$PY" -m mypy

step "pytest"
"$PY" -m pytest -q

printf '\nAll checks passed.\n'
