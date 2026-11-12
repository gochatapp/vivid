#!/usr/bin/env bash
# VividDB gist scanner + tagger (unified pipeline) - Linux/macOS version of scan.bat.
# Records are named GOCHATAPPROLETAG_<name>.txt / GOCHATAPPSTORYTAG_<name>.md
# (what chatgo uploads -- the kind lives inside the single-token tag so gist
# search can find it). Only those tag names are imported: gists still named
# gochatapp.github.io_*, mystupidchatapps_* or vividchatapp_* are SKIPPED and
# listed in scan_skipped.txt (rename them with tools/fix_gist_prefix.py).
# Discovery: filename search on the tags plus the gist list of every crawled
# account (user:<login>), so the older prefixed uploads are still found and
# reported.
# Usage: ./scan.sh [--init] [--refresh] [--owners=a,b] [--prune-dead] [--force] [--limit=N] [--dry-run] [--server=...] [--model=...] [--no-prompt] [--skip-scan] [--skip-tag] [--skip-sfw] [--no-copy]
# Examples:
#   ./scan.sh
#   ./scan.sh --skip-tag
#   ./scan.sh --refresh --skip-tag --skip-sfw
#   ./scan.sh --skip-scan --prune-dead
#   ./scan.sh --skip-scan --force --limit=10
#   ./scan.sh --no-prompt --no-copy
#   ./scan.sh --owners=gochatapp,CharacterXIFan --skip-tag --skip-sfw
set -euo pipefail
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi
exec "$PY" scan_gists.py "$@"
