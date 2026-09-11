#!/usr/bin/env bash
# Fetch the pinned F1DB release (SQLite build) and verify its SHA-256 against the
# checksums file published with the same release. Never edits the data.
#
# Source: https://github.com/f1db/f1db  (CC BY 4.0)
# Bump F1DB_VERSION deliberately; every number the game shows traces back to this release.
set -euo pipefail

F1DB_VERSION="${F1DB_VERSION:-v2026.13.0}"
BASE="https://github.com/f1db/f1db/releases/download/${F1DB_VERSION}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/data/f1db"
mkdir -p "${DEST}"
cd "${DEST}"

echo "Fetching F1DB ${F1DB_VERSION} ..."
curl -sSfL -o checksums_sha256.txt "${BASE}/checksums_sha256.txt"
curl -sSfL -o f1db-sqlite.zip "${BASE}/f1db-sqlite.zip"

expected="$(grep ' f1db-sqlite.zip$' checksums_sha256.txt | awk '{print $1}')"
actual="$(sha256sum f1db-sqlite.zip | awk '{print $1}')"
if [[ -z "${expected}" || "${expected}" != "${actual}" ]]; then
  echo "SHA-256 mismatch for f1db-sqlite.zip (expected ${expected:-<none>}, got ${actual})" >&2
  exit 1
fi
echo "Checksum OK."

unzip -o -q f1db-sqlite.zip
echo "${F1DB_VERSION}" > VERSION
echo "Ready: ${DEST}/f1db.db (F1DB ${F1DB_VERSION})"
