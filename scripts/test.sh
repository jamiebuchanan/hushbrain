#!/usr/bin/env bash
# scripts/test.sh: run the whole suite against a throwaway Postgres with pgvector.
#
# Starts a container on 127.0.0.1 with a random password, runs pytest, removes the container
# and its volume, and FAILS if any test was skipped (a skipped database test is a privacy
# test that did not run). Needs docker and uv.
#
#   HUSHBRAIN_TEST_PORT=54330 bash scripts/test.sh     # pick another port
#   HUSHBRAIN_TEST_DSN=postgresql://... bash scripts/test.sh   # use your own server instead
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"

report="$(mktemp)"
cleanup() { rm -f "$report"; [ -n "${started:-}" ] && docker compose -p hushbrain-test down -v >/dev/null 2>&1; }
trap cleanup EXIT

if [ -z "${HUSHBRAIN_TEST_DSN:-}" ]; then
  command -v docker >/dev/null || { echo "docker not found; set HUSHBRAIN_TEST_DSN to a Postgres with pgvector"; exit 2; }
  export HUSHBRAIN_DB_PASSWORD="$(openssl rand -hex 12)"
  export HUSHBRAIN_DB_PORT="${HUSHBRAIN_TEST_PORT:-54329}"
  docker compose -p hushbrain-test up -d --wait db >/dev/null || { echo "could not start the test database"; exit 2; }
  started=1
  export HUSHBRAIN_TEST_DSN="postgresql://hushbrain:${HUSHBRAIN_DB_PASSWORD}@127.0.0.1:${HUSHBRAIN_DB_PORT}/postgres"
fi

uv run pytest "$@" 2>&1 | tee "$report"
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ] && grep -Eq '[0-9]+ skipped' "$report"; then
  echo "FAIL: some tests were skipped, so they did not prove anything"
  rc=1
fi
exit "$rc"
