#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUB="$ROOT/vendor/th123data"
OUT="$ROOT/server/app/static/data"

if [[ ! -f "$SUB/package.json" ]]; then
  echo "vendor/th123data not found. Run: git submodule update --init --recursive" >&2
  exit 1
fi

cd "$SUB"
(cd search && npm ci)
npm run build:search

rm -rf "$OUT"
mkdir -p "$OUT"
cp -a docs/. "$OUT/"
echo "Built frame data UI -> $OUT"
