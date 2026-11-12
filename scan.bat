@echo off
REM VividDB gist scanner + tagger (unified pipeline).
REM Records are named GOCHATAPPROLETAG_<name>.txt / GOCHATAPPSTORYTAG_<name>.md
REM (what chatgo uploads -- the kind lives inside the single-token tag so gist
REM search can find it). Only those tag names are imported: gists still named
REM gochatapp.github.io_*, mystupidchatapps_* or vividchatapp_* are SKIPPED and
REM listed in scan_skipped.txt (rename them with tools\fix_gist_prefix.py).
REM Discovery: filename search on the tags plus the gist list of every crawled
REM account (user:<login>), so the older prefixed uploads are still found and
REM reported.
REM Usage: scan.bat [--init] [--refresh] [--owners=a,b] [--prune-dead] [--force] [--limit=N] [--dry-run] [--server=...] [--model=...] [--no-prompt] [--skip-scan] [--skip-tag] [--skip-sfw] [--no-copy]
REM Examples:
REM   scan.bat
REM   scan.bat --skip-tag
REM   scan.bat --refresh --skip-tag --skip-sfw
REM   scan.bat --skip-scan --prune-dead
REM   scan.bat --skip-scan --force --limit=10
REM   scan.bat --no-prompt --no-copy
REM   scan.bat --owners=gochatapp,CharacterXIFan --skip-tag --skip-sfw
cd /d "%~dp0"
python scan_gists.py %*
pause
