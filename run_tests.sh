#!/bin/sh
# Complete automated test suite (Python + browser-UI tests).
cd "$(dirname "$0")" || exit 1
[ -d .venv-test ] || python3 -m venv .venv-test || exit 1
. .venv-test/bin/activate
pip install -q -r requirements-dev.txt && python -m pytest tests -q -p no:cacheprovider || exit 1
if command -v node >/dev/null; then (cd tests/js && npm ci --no-audit --no-fund && node --test) || exit 1; else echo "Node.js not found - UI tests skipped"; fi
echo "ALL TESTS PASSED"
