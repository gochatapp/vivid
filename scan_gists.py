"""VividChatApp gist scanner + tagger + SFW gate (unified pipeline).

Gists are discovered through search and classified by filename. The record
kind is folded into a single searchable tag (ROLE_TAG_PREFIX /
STORY_TAG_PREFIX), so the tag and the kind are one filename token:
  GOCHATAPPROLETAG_<name>.txt|md    current uploads (chatgo writes these:
  GOCHATAPPSTORYTAG_<name>.md       role_maker.html, story_maker.html,
                                    store_api.go)
  gochatapp.github.io_role_<name>.txt|md    uploads published with the dotted
  gochatapp.github.io_story_<name>.txt|md   prefix before the tag move
  mystupidchatapps_role_<name>.txt|md       gists published before the dotted
  mystupidchatapps_story_<name>.txt|md      prefix
  vividchatapp_role_<name>.txt|md           gists uploaded before the account
  vividchatapp_story_<name>.txt|md          rename (incl. VividChatFan's)

Legacy uploads (the three prefixed shapes below the tags) are recognised but
NOT imported: they predate the searchable tags, so each one is skipped and
listed in scan_skipped.txt, and the run ends with a reminder to look there.
Fix them by hand, or rename them with tools/fix_gist_prefix.py --apply and
rescan. Only the tag names above end up in the database. Rows that are
already stored under a legacy name (from an older database) are left
untouched, but they are flagged in scan_skipped.txt too so they can be
cleaned up.

Discovery: gist search breaks filename tokens on "." and "_", so the old
"gochatapp.github.io" prefix could not itself be searched -- filename:
gochatapp.github.io returned tens of thousands of unrelated *.github.io
gists while filename:gochatapp returned nothing. The tags (SEARCH_TERMS)
carry no separator, so they are searched directly; the crawler also lists
every gist of the known owner accounts with user:<login>
(DEFAULT_SEARCH_OWNERS plus the owners already in the database, override
with --owners=a,b), which finds the legacy names too.

On startup it asks two questions (flags can auto-answer them):
  1. Database - scan & add/update (default), init (wipe + rescan),
     or skip scan. (--init / --skip-scan / --no-prompt)
  2. LLM setup - server + model pickers from scan_gist.yaml.
     (--server / --model / --no-prompt / --skip-tag / --skip-sfw)

Then runs up to three phases in order:
  1. SCAN - crawl gist.github.com search, sync roles/stories into
     db_vivid.sqlite, write root db_chatgo.json + scan_skipped.txt.
     Gists whose master file still carries a legacy prefix are skipped and
     listed in scan_skipped.txt (the run ends with a reminder to check it);
     only GOCHATAPPROLETAG_* / GOCHATAPPSTORYTAG_* names are imported.
  2. TAG  - retag new/changed records with llama.cpp (SuperGemma):
     tags, 1-5 quality (roles), 50-word description backfill. Rows whose
     tags are current but lack a quality rating or description are
     backfilled without re-asking for tags.
  3. SFW  - download unchecked/changed thumbs, ask vision LLM
     (gemma4:31b-cloud) YES/NO safe-for-work, flag image_sfw in DB.
     Same-byte images (sha256 match) skip the LLM call.
Flags:
  --init         wipe DB before scanning
  --refresh      re-fetch every discovered gist instead of trusting the
                 cached gist_updated_at timestamps; repairs owner, gist
                 URL, raw URLs and filenames from the live API (use after
                 an account/gist rename; keeps tags/quality/SFW intact)
  --owners=a,b   gist accounts crawled with user:<login>, in addition to
                 DEFAULT_SEARCH_OWNERS and the owners already in the DB
  --prune-dead   drop roles/stories whose gist no longer exists (API 404
                 plus raw URL 404). Gists that are merely missing from
                 search results (index lag, secret) are kept.
  --force        retag everything (default: only new/changed)
  --limit=N      cap records tagged this run
  --dry-run      list what would be tagged; no LLM calls, no JSON write
  --server=N|name|url   LLM server, skips the picker
  --model=NAME   LLM model, skips the picker
  --no-prompt    accept all defaults (first server, default model)
  --skip-scan    run the tag phase only
  --skip-tag     run the scan phase only
                 (both together = just re-export JSON)
  --skip-sfw     skip the SFW vision phase (runs after tag by default)
  --force-sfw    re-check every image even if already flagged
  --sfw-limit=N  cap images checked this run
  --sfw-server=N|name|url  vision server override (default: same as --server
                  when that is Ollama; else first Ollama server in config)
  --sfw-model=NAME  vision model override (default gemma4:31b-cloud)

Servers/models come from scan_gist.yaml next to this script. The GitHub PAT
used for the API calls and for the final db_chatgo.json push comes from
gist_token.txt next to this script (git-ignored), or the GH_TOKEN /
GITHUB_TOKEN environment variable.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db_vivid.sqlite"
JSON_PATH = BASE_DIR / "db_chatgo.json"         # root export (chatgo store name)
SKIPPED_PATH = BASE_DIR / "scan_skipped.txt"
TAGS_PATH = BASE_DIR / "tag_allowed.txt"
CONFIG_PATH = BASE_DIR / "scan_gist.yaml"
LEGACY_CONFIG_PATH = BASE_DIR / "tag.yaml"  # fallback if renamed file missing
GIST_LOCAL_PATH = BASE_DIR / "gist_local.yaml"  # optional: github.token
GIST_TOKEN_PATH = BASE_DIR / "gist_token.txt"   # preferred: PAT, git-ignored

# Gist filename tags. Gists are classified by filename, so the current tags
# and the names of already-published gists must all be accepted
# (ROLE_RE / STORY_RE below):
#   GOCHATAPPROLETAG_<name>.txt|md   roles, what chatgo writes today
#                                    (role_maker.html, store_api.go)
#   GOCHATAPPSTORYTAG_<name>.md      stories (story_maker.html)
#                                    -- the kind lives INSIDE the tag, because
#                                    gist search breaks tokens on "." and "_":
#                                    a tag with no separator is one token
#   gochatapp.github.io_*            dotted-prefix uploads, before the tag move
#   mystupidchatapps_*               gists published before the dotted prefix
#   vividchatapp_*                   everything uploaded before the account
#                                    rename, incl. the gists owned by
#                                    VividChatFan
# The legacy prefixes are still parsed (ROLE_RE / STORY_RE) only so those
# gists can be spotted, logged and skipped instead of being imported under a
# dead name -- see legacy_prefix_of() and the skip branch in run_scan().
ROLE_TAG_PREFIX = "GOCHATAPPROLETAG"
STORY_TAG_PREFIX = "GOCHATAPPSTORYTAG"
TAG_PREFIXES = (ROLE_TAG_PREFIX, STORY_TAG_PREFIX)
LEGACY_PREFIXES = ("gochatapp.github.io", "mystupidchatapps", "vividchatapp")

# Gist **search**: the tags are single tokens, so filename:GOCHATAPPROLETAG
# finds our records. Legacy names are still crawled through user:<login>
# (see collect_search_gids), not through a filename query.
SEARCH_TERMS = TAG_PREFIXES

# Owner accounts whose gists are listed with user:<login>. This finds
# dotted-prefix uploads that the filename search can no longer see, and it
# keeps working after gist_fix_names.py renames every file. More owners are
# added at scan time from the database (see run_scan) and from --owners=a,b.
DEFAULT_SEARCH_OWNERS = ("gochatapp",)

# Fallbacks when scan_gist.yaml is missing/unreadable.
DEFAULT_LLAMA_URL = "http://192.168.1.252:11434"
DEFAULT_MODEL = "gemma4:31b-cloud"
MAX_TAGS = 10
MAX_CHARS = 12000  # concatenated text budget per record sent to the LLM
TIMEOUT_FETCH = 30
TIMEOUT_LLM = 300


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY, rolename TEXT NOT NULL, gist_id TEXT NOT NULL,
  gist_url TEXT, owner TEXT, description TEXT,
  data_filename TEXT, data_raw_url TEXT, data_size INTEGER,
  thumb_filename TEXT, thumb_raw_url TEXT, image_url TEXT,
  image_sfw TEXT, image_sfw_at TEXT, image_sfw_model TEXT, image_hash TEXT,
  image_checked_url TEXT,
  gist_created_at TEXT, gist_updated_at TEXT,
  first_seen TEXT, last_scanned TEXT, last_changed TEXT
);
CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY, storyname TEXT NOT NULL, gist_id TEXT NOT NULL,
  gist_url TEXT, owner TEXT, description TEXT,
  story_filename TEXT, story_raw_url TEXT, story_size INTEGER,
  thumb_filename TEXT, thumb_raw_url TEXT, image_url TEXT,
  image_sfw TEXT, image_sfw_at TEXT, image_sfw_model TEXT, image_hash TEXT,
  image_checked_url TEXT,
  gist_created_at TEXT, gist_updated_at TEXT,
  first_seen TEXT, last_scanned TEXT, last_changed TEXT
);
CREATE TABLE IF NOT EXISTS story_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT NOT NULL
  REFERENCES stories(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, name TEXT NOT NULL, filename TEXT NOT NULL,
  raw_url TEXT, size INTEGER, UNIQUE(story_id, filename)
);
CREATE TABLE IF NOT EXISTS gist_files (
  gist_id TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
  filename TEXT NOT NULL, raw_url TEXT, size INTEGER,
  PRIMARY KEY (gist_id, filename)
);
CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  gists_found INTEGER, roles_seen INTEGER, stories_seen INTEGER,
  added INTEGER, updated INTEGER, unchanged INTEGER
);
"""

MIGRATIONS = [
    "ALTER TABLE roles ADD COLUMN gist_created_at TEXT",
    "ALTER TABLE roles ADD COLUMN gist_updated_at TEXT",
    "ALTER TABLE roles ADD COLUMN first_seen TEXT",
    "ALTER TABLE roles ADD COLUMN last_scanned TEXT",
    "ALTER TABLE roles ADD COLUMN last_changed TEXT",
    "ALTER TABLE stories ADD COLUMN gist_created_at TEXT",
    "ALTER TABLE stories ADD COLUMN gist_updated_at TEXT",
    "ALTER TABLE stories ADD COLUMN first_seen TEXT",
    "ALTER TABLE stories ADD COLUMN last_scanned TEXT",
    "ALTER TABLE stories ADD COLUMN last_changed TEXT",
    "ALTER TABLE roles ADD COLUMN image_sfw TEXT",
    "ALTER TABLE roles ADD COLUMN image_sfw_at TEXT",
    "ALTER TABLE roles ADD COLUMN image_sfw_model TEXT",
    "ALTER TABLE roles ADD COLUMN image_hash TEXT",
    "ALTER TABLE stories ADD COLUMN image_sfw TEXT",
    "ALTER TABLE stories ADD COLUMN image_sfw_at TEXT",
    "ALTER TABLE stories ADD COLUMN image_sfw_model TEXT",
    "ALTER TABLE stories ADD COLUMN image_hash TEXT",
    "ALTER TABLE roles ADD COLUMN image_checked_url TEXT",
    "ALTER TABLE stories ADD COLUMN image_checked_url TEXT",
]

def connect(path=None):
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    has_roles = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='roles'"
    ).fetchone()
    conn.executescript(SCHEMA)
    if has_roles:
        cols = {x[1] for x in conn.execute("PRAGMA table_info(roles)")}
        if "created_at" in cols and "gist_created_at" not in cols:
            for sql in MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            conn.execute("UPDATE roles SET gist_created_at=created_at,"
                         " gist_updated_at=updated_at,"
                         " first_seen=COALESCE(first_seen, last_scanned,"
                         " updated_at), last_scanned=COALESCE(last_scanned,"
                         " updated_at), last_changed=COALESCE(last_changed,"
                         " updated_at)")
            conn.execute("UPDATE stories SET gist_created_at=created_at,"
                         " gist_updated_at=updated_at,"
                         " first_seen=COALESCE(first_seen, last_scanned,"
                         " updated_at), last_scanned=COALESCE(last_scanned,"
                         " updated_at), last_changed=COALESCE(last_changed,"
                         " updated_at)")
            conn.commit()
    # One-time re-key: ids used to be normalized names (colliding on dups).
    # Now id == gist_id (unique per gist). Migrate any old rows once.
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM roles WHERE id != gist_id").fetchone()[0]
        m = conn.execute(
            "SELECT COUNT(*) FROM stories WHERE id != gist_id").fetchone()[0]
    except sqlite3.OperationalError:
        n = m = 0
    if n or m:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM roles WHERE id != gist_id AND gist_id IN "
                     "(SELECT id FROM roles)")
        conn.execute("DELETE FROM stories WHERE id != gist_id AND gist_id IN "
                     "(SELECT id FROM stories)")
        conn.execute("UPDATE story_files SET story_id=(SELECT gist_id FROM "
                     "stories WHERE id=story_id) WHERE story_id IN "
                     "(SELECT id FROM stories WHERE id != gist_id)")
        conn.execute("UPDATE roles SET id=gist_id WHERE id != gist_id")
        conn.execute("UPDATE stories SET id=gist_id WHERE id != gist_id")
        conn.execute("UPDATE gist_files SET record_id=gist_id "
                     "WHERE record_id != gist_id")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    return conn

def wipe_all(conn):
    conn.execute("DELETE FROM story_files")
    conn.execute("DELETE FROM gist_files")
    conn.execute("DELETE FROM roles")
    conn.execute("DELETE FROM stories")
    conn.execute("DELETE FROM scan_runs")
    conn.commit()

CORE_ROLE = ["rolename", "gist_id", "gist_url", "owner", "description",
  "data_filename", "data_raw_url", "data_size", "thumb_filename",
  "thumb_raw_url", "image_url", "gist_created_at", "gist_updated_at"]
CORE_STORY = ["storyname", "gist_id", "gist_url", "owner", "description",
  "story_filename", "story_raw_url", "story_size", "thumb_filename",
  "thumb_raw_url", "image_url", "gist_created_at", "gist_updated_at"]

def _changed(old, new, keys):
    if old is None:
        return True
    for k in keys:
        a = old[k] if k in old.keys() else None
        b = new.get(k)
        if k in ("data_raw_url", "thumb_raw_url", "story_raw_url",
                 "gist_url"):
            continue  # embed rotating commit hashes; not real changes
        if a != b:
            return True
    return False

def sync_role(conn, r):
    """Returns 'added' | 'updated' | 'unchanged'. Replaces file list."""
    ts = now_iso()
    old = conn.execute("SELECT * FROM roles WHERE id=?", (r["id"],)).fetchone()
    changed = _changed(old, r, CORE_ROLE)
    if old is None:
        r = dict(r, first_seen=ts, last_scanned=ts, last_changed=ts)
        cols = (list(r.keys()))
        conn.execute(f"INSERT INTO roles ({','.join(cols)}) VALUES "
                     f"({','.join(':'+c for c in cols)})", r)
    else:
        lc = ts if changed else old["last_changed"]
        r = dict(r, first_seen=old["first_seen"], last_scanned=ts,
                 last_changed=lc)
        cols = [c for c in r.keys() if c != "id"]
        conn.execute(f"UPDATE roles SET {','.join(c+'=:'+c for c in cols)}"
                     " WHERE id=:id", r)
    conn.execute("DELETE FROM gist_files WHERE gist_id=?", (r["gist_id"],))
    return ("added" if old is None else ("updated" if changed else "unchanged"))

def sync_story(conn, s, files):
    """Returns (status, added_files, removed_files). Replaces file lists."""
    ts = now_iso()
    old = conn.execute("SELECT * FROM stories WHERE id=?",
                       (s["id"],)).fetchone()
    old_files = {x["filename"]: dict(x) for x in conn.execute(
        "SELECT kind, name, filename, raw_url, size FROM story_files "
        "WHERE story_id=?", (s["id"],))}
    new_files = {f["filename"]: f for f in files}
    core_changed = _changed(old, s, CORE_STORY)
    # NOTE: gist raw_urls embed a per-file commit hash that changes every
    # API fetch, so identity for change detection is (kind, name, size).
    # raw_url is still stored fresh each scan so links stay current.
    def _sig(f):
        return (f.get("kind"), f.get("name"), f.get("size"))
    files_changed = ({k: _sig(v) for k, v in old_files.items()} !=
                     {k: _sig(v) for k, v in new_files.items()})
    changed = core_changed or files_changed or old is None
    added_f = sorted(set(new_files) - set(old_files))
    removed_f = sorted(set(old_files) - set(new_files))
    if old is None:
        s = dict(s, first_seen=ts, last_scanned=ts, last_changed=ts)
        cols = list(s.keys())
        conn.execute(f"INSERT INTO stories ({','.join(cols)}) VALUES "
                     f"({','.join(':'+c for c in cols)})", s)
    else:
        lc = ts if changed else old["last_changed"]
        s = dict(s, first_seen=old["first_seen"], last_scanned=ts,
                 last_changed=lc)
        cols = [c for c in s.keys() if c != "id"]
        conn.execute(f"UPDATE stories SET {','.join(c+'=:'+c for c in cols)}"
                     " WHERE id=:id", s)
    conn.execute("DELETE FROM story_files WHERE story_id=?", (s["id"],))
    for f in files:
        conn.execute("INSERT INTO story_files (story_id, kind, name, filename,"
                     " raw_url, size) VALUES (?,?,?,?,?,?)",
                     (s["id"], f["kind"], f["name"], f["filename"],
                      f.get("raw_url"), f.get("size")))
    conn.execute("DELETE FROM gist_files WHERE gist_id=?", (s["gist_id"],))
    status = "added" if old is None else ("updated" if changed else "unchanged")
    return status, added_f, removed_f

def record_gist_files(conn, gist_id, record_type, record_id, files):
    for fn, det in (files or {}).items():
        conn.execute("INSERT OR REPLACE INTO gist_files (gist_id, record_type,"
                     " record_id, filename, raw_url, size)"
                     " VALUES (?,?,?,?,?,?)",
                     (gist_id, record_type, record_id, fn,
                      det.get("raw_url"), det.get("size")))


def search_url(tag, page=1):
    """Search URL for one filename tag (SEARCH_TERMS).

    GitHub gist search matches whole filename tokens, not substrings
    (filename:vividchat returns nothing), so each tag needs its own query --
    hence the per-tag crawl in collect_search_gids(). The tags carry no "."
    or "_", so they are single tokens and are searched directly; legacy
    dotted prefixes are not searched (they would match unrelated
    *.github.io gists) and are found through user:<login> instead.
    """
    base = "https://gist.github.com/search?q=" + quote("filename:" + tag)
    return base + "&p=%d" % page if page > 1 else base


def owner_search_url(owner, page=1):
    """Search URL listing every gist of one account (user:<login>).

    Tag- and prefix-agnostic, so it still finds gists whose files were
    renamed to the dotted gochatapp.github.io prefix, or published under
    the older mystupidchatapps_* / vividchatapp_* names.
    """
    base = "https://gist.github.com/search?q=" + quote("user:" + owner)
    return base + "&p=%d" % page if page > 1 else base


HEADERS = {"User-Agent": "VividChat-Gist-Scanner/1.0",
           "Accept": "application/vnd.github+json"}


def read_gist_token_file(path=GIST_TOKEN_PATH):
    """Return the PAT from gist_token.txt, or "" when missing/empty.

    The file holds one line (ghp_...). Blank lines and #-comments are
    skipped so the line can be annotated, and wrapping quotes are
    stripped, so both ghp_... and "ghp_..." work. The file is git-ignored
    (.gitignore) -- keep it out of the repo.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip().strip("\"'").strip()
        if line and not line.startswith("#"):
            return line
    return ""


def resolve_token():
    """Return (token, source) for GitHub API calls.

    Order: GH_TOKEN / GITHUB_TOKEN env first (keeps secrets out of the
    repo), then gist_token.txt (git-ignored plain-text PAT, the normal
    setup), then gist_local.yaml (github.token, also git-ignored), then
    scan_gist.yaml (gisttoken) for old checkouts. Commented-out keys are
    ignored, so a dead token can be parked instead of deleted.
    """
    for env in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = (os.environ.get(env) or "").strip()
        if val:
            return val, env
    val = read_gist_token_file()
    if val:
        return val, GIST_TOKEN_PATH.name
    for path, label in ((GIST_LOCAL_PATH, "gist_local.yaml"),
                        (CONFIG_PATH, "scan_gist.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^\s*(?:token|gisttoken):\s*[\"']?([A-Za-z0-9_\-]+)",
                      text, re.M)
        if m:
            return m.group(1), label
    return None, None


TOKEN, TOKEN_SOURCE = resolve_token()


def require_gist_token():
    """Stop with a clear message when no GitHub gist token is configured.

    Every run except --dry-run ends by PATCHing db_chatgo.json into the
    published gist (push_json_to_gist), which needs a PAT with the "gist"
    scope. Failing at startup beats crawling and tagging for a long time
    only to die on the upload.
    """
    token, source = resolve_token()
    if token:
        return token, source
    print("")
    print("ERROR: no GitHub gist token found -- cannot update the gist.")
    print("  This pipeline needs a GitHub personal access token (PAT) with")
    print("  the \"gist\" scope to read gists and to push db_chatgo.json.")
    print("  Fix: put the token on one line in:")
    print("       %s" % GIST_TOKEN_PATH)
    print("  Or set the GH_TOKEN / GITHUB_TOKEN environment variable.")
    print("  (--dry-run needs no token: it writes nothing.)")
    sys.exit(1)


PAGE_PAUSE = 1.0  # seconds between gist API fetches
SEARCH_PAGE_PAUSE = 5.0  # minimum seconds between gist search requests

RATE_LIMIT_PAUSE = 60  # seconds to wait between rate-limit retries
MAX_RATE_RETRIES = 3   # retries per gist on 403/429 before giving up
MAX_SEARCH_RATE_RETRIES = 3


def _search_rate_limit_wait(response, attempt):
    """Return a retry delay, honoring server hints when available."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except (ValueError, OverflowError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(1, int((retry_at - datetime.now(
                    timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass

    reset = response.headers.get("X-RateLimit-Reset")
    if reset and reset.isdigit():
        return max(1, int(reset) - int(time.time()) + 5)
    return RATE_LIMIT_PAUSE * (2 ** attempt)


def get_search_page(s, url, label):
    """GET a gist search page, retrying rate limits before raising errors."""
    for attempt in range(MAX_SEARCH_RATE_RETRIES + 1):
        response = s.get(url, timeout=30)
        if response.status_code == 200:
            return response

        body = (response.text or "")[:200]
        rate_limited = response.status_code == 429 or (
            response.status_code == 403 and (
                response.headers.get("Retry-After") is not None
                or response.headers.get("X-RateLimit-Remaining") == "0"
                or "rate limit" in body.lower()
                or "abuse detection" in body.lower()
                or "secondary rate limit" in body.lower()
                or "whoa there" in body.lower()))
        if not rate_limited:
            response.raise_for_status()

        if attempt == MAX_SEARCH_RATE_RETRIES:
            response.raise_for_status()

        wait = _search_rate_limit_wait(response, attempt)
        print("  search rate limited (HTTP %d, %s), waiting %ds "
              "(retry %d/%d)..." %
              (response.status_code, label, wait, attempt + 1,
               MAX_SEARCH_RATE_RETRIES), flush=True)
        time.sleep(wait)

    raise RuntimeError("Search retry loop exited unexpectedly")

def api_get_json(s, url, ah):
    """GET a GitHub API URL, returning (data, error).

    Handles 403/429 rate limits with a wait-and-retry (honoring
    X-RateLimit-Reset when present). Never raises for HTTP errors --
    callers get error=<human reason> and can label the skip correctly
    instead of mistaking an error body for a gist with no files.
    """
    last_err = None
    for attempt in range(MAX_RATE_RETRIES + 1):
        try:
            r = s.get(url, timeout=30, headers=ah)
        except requests.RequestException as e:
            return None, "fetch error: %s" % e
        if r.status_code == 200:
            try:
                return r.json(), None
            except ValueError as e:
                return None, "bad JSON: %s" % e
        body = (r.text or "")[:200]
        if r.status_code in (403, 429) and (
                "rate limit" in body.lower()
                or "x-ratelimit-remaining: 0" in
                str(r.headers).lower()):
            reset = r.headers.get("X-RateLimit-Reset")
            wait = RATE_LIMIT_PAUSE
            if reset and reset.isdigit():
                try:
                    wait = max(1, int(reset) - int(time.time()) + 5)
                except ValueError:
                    pass
            remain = r.headers.get("X-RateLimit-Remaining", "?")
            print(f"  rate limited (HTTP {r.status_code}, "
                  f"remaining={remain}), waiting {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RATE_RETRIES + 1})...",
                  flush=True)
            time.sleep(wait)
            last_err = ("rate limited (HTTP %d, remaining=%s)"
                        % (r.status_code, remain))
            continue
        return None, "HTTP %d: %s" % (r.status_code, body)
    return None, last_err or "rate limited: retries exhausted"

def _parse_scraped_dt(raw):
    """Normalize a search-page datetime to UTC 'YYYY-MM-DDTHH:MM:SSZ'.

    Search HTML gives e.g. '2026-10-17T15:21:09-04:00' (local offset)
    while the API/DB stores '2026-10-17T19:21:09Z'. Returns None if
    unparseable so callers fall back to a full fetch.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def collect_search_gids(s, owners=()):
    """Walk every discovery query's result pages and collect gist IDs.

    Two kinds of query, because no single term covers everything:
      * filename:<tag> (SEARCH_TERMS) -- the tag is one token
        (GOCHATAPPROLETAG / GOCHATAPPSTORYTAG); GitHub gist search matches
        whole filename tokens, not substrings, so each tag needs its own
        query.
      * user:<login> (DEFAULT_SEARCH_OWNERS + owners) -- lists every gist
        of a known account, so uploads still published under the dotted
        gochatapp.github.io prefix or the older mystupidchatapps_* /
        vividchatapp_* names are discovered too (those prefixes cannot be
        searched, see SEARCH_TERMS).

    Returns (gids, scraped_updated) where gids is a sorted list of ids
    and scraped_updated maps gid -> UTC 'YYYY-MM-DDTHH:MM:SSZ' parsed
    from the search page's <relative-time datetime="...">, or None when
    the page had no timestamp for that gist.
    """
    gids = set()
    scraped_updated = {}
    gid_re = re.compile(
        r'href="/[^/"?#]+/([0-9a-f]{20,})(?=[/?#"]|$)', re.I)
    time_re = re.compile(
        r'<relative-time[^>]+datetime="([^"]+)"', re.I)
    queries = [("filename:" + t, lambda p, t=t: search_url(t, p))
               for t in SEARCH_TERMS]
    for owner in owners:
        if owner and owner != "?":
            queries.append(("user:" + owner,
                            lambda p, o=owner: owner_search_url(o, p)))
    print("Searching: %s" % ", ".join(label for label, _f in queries))
    requested_page = False
    for label, url_for in queries:
        page = 1
        while True:
            url = url_for(page)
            if requested_page:
                time.sleep(SEARCH_PAGE_PAUSE)
            r = get_search_page(s, url, label)
            requested_page = True
            found = set(gid_re.findall(r.text))
            print(f"  search page {page} ({label}): {len(found)} gists")
            # Pair each gist link with the next <relative-time> after it on
            # the page (each search result block is link ... Last active time
            # ... description, in that order).
            gid_hits = list(gid_re.finditer(r.text))
            time_hits = list(time_re.finditer(r.text))
            for idx, m in enumerate(gid_hits):
                gid = m.group(1).lower()
                start = m.start()
                end = gid_hits[idx + 1].start() if idx + 1 < len(gid_hits) else len(r.text)
                best = None
                for t in time_hits:
                    if start <= t.start() < end:
                        best = t.group(1)
                        break  # first timestamp inside this result block
                norm = _parse_scraped_dt(best)
                # Keep the newest timestamp seen if a gist appears twice.
                if gid in scraped_updated and scraped_updated[gid] and norm:
                    scraped_updated[gid] = max(scraped_updated[gid], norm)
                elif gid not in scraped_updated:
                    scraped_updated[gid] = norm
            new = found - gids
            if not new:
                break
            gids |= found
            page += 1
            if page > 20:  # safety cap
                print("  stopped at 20 pages")
                break
        print(f"  {label}: {len(gids)} gists so far")
    return sorted(gids), scraped_updated


def search_owners(conn, extra=()):
    """Accounts to crawl with user:<login>: defaults + database + CLI.

    The database is consulted because contributors other than the default
    account published legacy gists; those keep the dotted or older prefix
    and can only be found through their owner. Read before wipe_all() so
    --init still rediscovers them.
    """
    owners = list(DEFAULT_SEARCH_OWNERS)
    try:
        rows = conn.execute(
            "SELECT DISTINCT owner FROM roles "
            "UNION SELECT DISTINCT owner FROM stories").fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        owner = row[0]
        if owner and owner != "?":
            owners.append(owner)
    owners.extend(o.strip() for o in extra if o and o.strip())
    seen, out = set(), []
    for owner in owners:
        if owner.lower() in seen:
            continue
        seen.add(owner.lower())
        out.append(owner)
    return out


_PREFIX_ALT = "|".join(re.escape(p) for p in LEGACY_PREFIXES)
# Current uploads: the kind is inside the tag (GOCHATAPPROLETAG_<name>.txt), so
# only the name is captured and each regex accepts only its own tag -- the tag
# is what decides role vs story. An extra kind word after the tag is tolerated
# (GOCHATAPPROLETAG_role_<name>.txt) so a half-migrated filename still lands.
# Legacy uploads keep their "<prefix>_role_<name>" shape.
ROLE_RE = re.compile(r"^(?:(?:%s)[\s._-]+(?:roles?[\s._-]+)?"
                     r"|(?:%s)[\s._-]+roles?[\s._-]+)(.+)\.(txt|md)$"
                     % (re.escape(ROLE_TAG_PREFIX), _PREFIX_ALT), re.I)
STORY_RE = re.compile(r"^(?:(?:%s)[\s._-]+(?:stor(?:y|ies)[\s._-]+)?"
                      r"|(?:%s)[\s._-]+stor(?:y|ies)[\s._-]+)(.+)\.(txt|md)$"
                      % (re.escape(STORY_TAG_PREFIX), _PREFIX_ALT), re.I)
SCENE_RE = re.compile(r"^scene_(.+)\.(txt|md)$", re.I)
CHAR_RE = re.compile(r"^character_(.+)\.(txt|md)$", re.I)
THUMB_RE = re.compile(r"^thumb\.(txt|md)$", re.I)

# A filename starting with one of the retired prefixes. ROLE_RE / STORY_RE
# accept those names so the gist is fetched and can be reported, but nothing
# with a legacy prefix is imported any more: it belongs to the pre-tag era
# and has to be renamed (tools/fix_gist_prefix.py --apply) or fixed by hand.
LEGACY_RE = re.compile(r"^(%s)[\s._-]" % _PREFIX_ALT, re.I)


def legacy_prefix_of(filename):
    """Return the legacy prefix a master filename uses, or None for tag names.

    >>> legacy_prefix_of("gochatapp.github.io_role_x.txt")
    'gochatapp.github.io'
    >>> legacy_prefix_of("GOCHATAPPROLETAG_x.txt") is None
    True
    """
    m = LEGACY_RE.match(filename or "")
    return m.group(1) if m else None


IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", re.I)
BARE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)

def parse_image_url(text):
    if not text:
        return None
    for rx, g in ((IMG_SRC_RE, 1), (MD_IMG_RE, 1), (BARE_URL_RE, 0)):
        m = rx.search(text)
        if m:
            return m.group(g).strip()
    return None

def norm(name):
    return re.sub(r"\s+", " ", name.strip().lower())

def classify_files(files):
    role_file, story_file, thumb = None, None, None
    scenes, chars, others = [], [], []
    for fn, det in (files or {}).items():
        if THUMB_RE.match(fn):
            if thumb is None:
                thumb = (fn, det)
            continue
        m = ROLE_RE.match(fn)
        if m:
            role_file = (m.group(1), fn, det)
            continue
        m = STORY_RE.match(fn)
        if m:
            story_file = (m.group(1), fn, det)
            continue
        m = SCENE_RE.match(fn)
        if m:
            scenes.append({"kind": "scene", "name": norm(m.group(1)),
                           "filename": fn, "raw_url": det.get("raw_url"),
                           "size": det.get("size")})
            continue
        m = CHAR_RE.match(fn)
        if m:
            chars.append({"kind": "character", "name": norm(m.group(1)),
                          "filename": fn, "raw_url": det.get("raw_url"),
                          "size": det.get("size")})
            continue
        others.append({"kind": "other", "name": norm(Path(fn).stem),
                       "filename": fn, "raw_url": det.get("raw_url"),
                       "size": det.get("size")})
    return role_file, story_file, thumb, scenes, chars, others


def load_config(path=CONFIG_PATH):
    """Read scan_gist.yaml -> (servers, models, default_server, default_model).

    servers: list of {"name": ..., "url": ...}; models: list of names.
    Falls back to legacy tag.yaml, then to built-ins, so old
    behaviour keeps working.
    """
    fallback = ([{"name": "default", "url": DEFAULT_LLAMA_URL}],
                [DEFAULT_MODEL], DEFAULT_LLAMA_URL, DEFAULT_MODEL)
    try:
        import yaml
    except ImportError:
        print("warn: pyyaml not installed, using built-in defaults.")
        return fallback
    cfg = Path(path)
    if not cfg.exists() and LEGACY_CONFIG_PATH.exists():
        print("warn: %s not found, using legacy %s."
              % (cfg.name, LEGACY_CONFIG_PATH.name))
        cfg = LEGACY_CONFIG_PATH
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        print("warn: %s not found, using built-in defaults." % Path(path).name)
        return fallback
    except Exception as e:
        print("warn: could not parse %s (%s), using defaults."
              % (Path(path).name, e))
        return fallback
    servers = []
    for entry in data.get("servers") or []:
        if isinstance(entry, dict) and entry.get("url"):
            srv = {"name": str(entry.get("name") or entry["url"]),
                   "url": str(entry["url"]).rstrip("/")}
            if entry.get("model"):
                srv["model"] = str(entry["model"]).strip()
            servers.append(srv)
        elif isinstance(entry, str) and entry.strip():
            servers.append({"name": entry.strip(), "url": entry.strip()})
    models = [str(m) for m in (data.get("models") or []) if str(m).strip()]
    if not servers:
        servers = [{"name": "default", "url": DEFAULT_LLAMA_URL}]
    if not models:
        models = [DEFAULT_MODEL]
    defaults = data.get("defaults") or {}
    default_model = str(defaults.get("model") or models[0])
    if default_model not in models:
        models = [default_model] + models
    default_server = str(defaults.get("server") or servers[0]["name"])
    # Resolve default server name -> url; allow a raw URL there too.
    url = next((s["url"] for s in servers if s["name"] == default_server),
               None)
    if url is None:
        url = (default_server if default_server.startswith("http")
               else servers[0]["url"])
    return servers, models, url, default_model


def fetch_server_models(base_url, timeout=10):
    """Ask a server which models it has. Works for llama.cpp + Ollama.

    Tries OpenAI-style GET {base}/v1/models, then Ollama-native
    GET {base}/api/tags. Returns list of names (may be empty).
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return []
    # 1. OpenAI-compatible (llama.cpp + Ollama both speak this).
    try:
        r = requests.get(base + "/v1/models", timeout=timeout)
        r.raise_for_status()
        ids = [str(m.get("id")) for m in r.json().get("data", [])
               if m.get("id")]
        if ids:
            return sorted(set(ids))
    except (requests.RequestException, ValueError):
        pass
    # 2. Ollama native.
    try:
        r = requests.get(base + "/api/tags", timeout=timeout)
        r.raise_for_status()
        names = [str(m.get("name")) for m in r.json().get("models", [])
                 if m.get("name")]
        if names:
            return sorted(set(names))
    except (requests.RequestException, ValueError):
        pass
    return []


def pick_from_list(title, items, default_idx=0, show_default_hint=True):
    """Numbered picker on stdin; '' keeps default, int picks, else raw text."""
    print(title)
    for i, label in enumerate(items, 1):
        marker = " (default)" if (show_default_hint and i - 1 == default_idx) else ""
        print("  %d) %s%s" % (i, label, marker))
    try:
        raw = input("Pick [default %d]: " % (default_idx + 1)).strip()
    except EOFError:
        raw = ""
    if not raw:
        return default_idx, None
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return int(raw) - 1, None
    return default_idx, raw  # free-typed override (name or URL)


def resolve_server(servers, default_url, cli_value, no_prompt):
    if cli_value:
        if cli_value.isdigit() and 1 <= int(cli_value) <= len(servers):
            return servers[int(cli_value) - 1]["url"]
        hit = next((s["url"] for s in servers if s["name"] == cli_value), None)
        if hit:
            return hit
        return cli_value.rstrip("/")  # raw URL override
    if no_prompt:
        return default_url
    labels = ["%s -> %s" % (s["name"], s["url"]) for s in servers]
    try:
        default_idx = next(i for i, s in enumerate(servers)
                           if s["url"] == default_url)
    except StopIteration:
        default_idx = 0
    idx, typed = pick_from_list("LLM servers (from %s):" % CONFIG_PATH.name,
                                labels, default_idx)
    if typed is not None:
        hit = next((s["url"] for s in servers if s["name"] == typed), None)
        return hit or typed.rstrip("/")
    return servers[idx]["url"]


def resolve_model(models, default_model, cli_value, no_prompt):
    if cli_value:
        if cli_value.isdigit() and 1 <= int(cli_value) <= len(models):
            return models[int(cli_value) - 1]
        return cli_value
    if no_prompt:
        return default_model
    try:
        default_idx = models.index(default_model)
    except ValueError:
        default_idx = 0
    idx, typed = pick_from_list("Models (from %s):" % CONFIG_PATH.name,
                                models, default_idx)
    return typed or models[idx]


def probe_llm_server(base_url, timeout=8):
    """One quick GET /api/tags; True when the LLM host answers.

    Keeps a dead host from burning 37x TIMEOUT_LLM retries downstream.
    """
    try:
        r = requests.get(base_url.rstrip("/") + "/api/tags", timeout=timeout,
                         headers={"User-Agent": "VividChat-Tagger/1.0"})
        return r.status_code < 500
    except requests.RequestException:
        return False


def ensure_columns(conn):
    for table in ("roles", "stories"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "tags" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN tags TEXT")
        if "tagged_at" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN tagged_at TEXT")
        for col, typ in (("image_sfw", "TEXT"), ("image_sfw_at", "TEXT"),
                         ("image_sfw_model", "TEXT"), ("image_hash", "TEXT"),
                         ("image_checked_url", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # quality: 1-5 completeness rating; roles only for now (stories later)
    role_cols = {r[1] for r in conn.execute("PRAGMA table_info(roles)")}
    if "quality" not in role_cols:
        conn.execute("ALTER TABLE roles ADD COLUMN quality INTEGER")
    conn.commit()


RATING_TAGS = ["SFW", "NSFW", "Mature", "18+"]


def _split_table_row(line):
    """Split a markdown table row with or without leading/trailing pipes."""
    text = line.strip()
    if "|" not in text:
        return []
    # tolerate rows like 'Tag | Meaning' (no outer pipes)
    text = text.strip().strip("|")
    return [c.strip() for c in text.split("|")]


def load_allowed_tags(path=TAGS_PATH):
    """Parse tag names (first column) from markdown tables in tag_allowed.txt."""
    allowed = []
    seen = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = _split_table_row(line)
        if len(cells) < 2 or not cells[0]:
            continue
        name = cells[0]
        if name.lower() == "tag" or set(name) <= set("-: "):
            continue
        if name not in seen:
            seen.add(name)
            allowed.append(name)
    # Rating tags must exist even if their table formatting drifts.
    for r in RATING_TAGS:
        if r not in seen:
            seen.add(r)
            allowed.append(r)
    return allowed


def load_tag_ranks(path=TAGS_PATH):
    """Map each allowed tag to a display rank group for the web UI.

    0 = content rating, 1 = relationship, 2 = everything else.
    Groups come from the '## N. Title' section headers in tag_allowed.txt,
    so the ranking follows that file automatically. On duplicate tag names
    the first (most prominent) section wins.
    """
    ranks = {}
    group = 2
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            title = re.sub(r"^\d+\.\s*", "", stripped[3:]).strip().lower()
            if "rating" in title:
                group = 0
            elif "relationship" in title or "lead" in title:
                group = 1
            else:
                group = 2
            continue
        cells = _split_table_row(line)
        if len(cells) < 2 or not cells[0]:
            continue
        name = cells[0]
        if name.lower() == "tag" or set(name) <= set("-: "):
            continue
        if name not in ranks:
            ranks[name] = group
    for r in RATING_TAGS:
        ranks.setdefault(r, 0)
    return ranks


def fetch_text(url):
    if not url:
        return ""
    r = requests.get(url, timeout=TIMEOUT_FETCH,
                     headers={"User-Agent": "VividChat-Tagger/1.0"})
    r.raise_for_status()
    return r.text or ""


def needs_tag(row):
    if not row["tags"]:
        return True
    # Old rows tagged before the rating guarantee need a retag.
    try:
        import json as _json
        _tags = _json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
        if isinstance(_tags, list):
            _low = {str(t).lower() for t in _tags}
            if not _low.intersection({r.lower() for r in RATING_TAGS}):
                return True
    except (ValueError, TypeError):
        return True
    tagged_at = row["tagged_at"] or ""
    last_changed = row["last_changed"] or ""
    return bool(last_changed) and last_changed > tagged_at


def needs_description(row):
    return not (row["description"] or "").strip()


def needs_quality(row):
    """True when a role has no usable 1-5 completeness rating yet.

    Re-tagged rows always re-rate, so this only catches roles whose rating
    was never stored (quality added later, LLM answer unparseable, ...).
    Self-heals on the next run instead of waiting for --force.
    """
    try:
        return not 1 <= int(row.get("quality")) <= 5
    except (TypeError, ValueError):
        return True


def build_prompt(allowed, blob, kind, name):
    tag_list = ", ".join(allowed)
    return (
        "You are a content tagging assistant for a roleplay character/story library.\n"
        f"Classify the following {kind} named '{name}'.\n"
        f"Pick at most {MAX_TAGS} tags ONLY from this exact allowed list:\n"
        f"{tag_list}\n"
        "Rules:\n"
        "- Reply with ONLY a JSON array of tag strings, e.g. [\"NSFW\", \"Romance\"].\n"
        "- Every tag must match an allowed tag EXACTLY (same spelling/case).\n"
        "- Most important tags first. At most "
        f"{MAX_TAGS} tags. At least 1 tag.\n"
        "- Always include exactly one content-rating tag (SFW, NSFW, Mature or 18+).\n"
        "- Include one lead tag (Female Lead, Male Lead or Nonbinary Lead) when the lead's gender is clear.\n"
        "- No explanation, no extra text, just the JSON array.\n\n"
        f"--- {kind.upper()} TEXT START ---\n{blob}\n--- {kind.upper()} TEXT END ---"
    )


def build_quality_prompt(blob, kind, name):
    return (
        "You rate how complete and usable a roleplay character card is.\n"
        f"Judge the {kind} named '{name}' below on DETAIL and COMPLETENESS, "
        "not writing quality.\n"
        "Consider: personality and behavior, physical appearance, speech style "
        "or example dialogue, backstory, scenario/situation, motivations, and "
        "relationships. A card that is just one line like 'Aiko is tall and shy' "
        "leaves almost nothing to roleplay with; a full card gives plenty to "
        "work with.\n"
        "Rating scale (integer 1-5):\n"
        "5 = fully fleshed-out card: personality, appearance, scenario, "
        "backstory and/or example dialogue all present and detailed.\n"
        "4 = solid and mostly complete, only minor gaps.\n"
        "3 = decent but missing important pieces (e.g. no scenario or "
        "example dialogue).\n"
        "2 = thin: a short paragraph, mostly one or two attributes.\n"
        "1 = stub: a single sentence or bare name/looks, nothing to go on.\n"
        "Reply with ONLY a JSON object like {\"quality\": 3}. No explanation.\n\n"
        f"--- {kind.upper()} TEXT START ---\n{blob}\n--- {kind.upper()} TEXT END ---"
    )


def build_summary_prompt(blob, kind, name):
    return (
        "Write a 50-word summary about this %s named '%s': who they are, "
        "appearance, personality and appeal. Aim for exactly 50 words, "
        "one paragraph, plain text. No headings, no commentary, "
        "just the summary.\n\n"
        % (kind, name)
        + f"--- {kind.upper()} TEXT START ---\n{blob}\n--- {kind.upper()} TEXT END ---"
    )


def clean_summary(raw):
    """Collapse LLM summary output to a single ~50-word paragraph."""
    text = re.sub(r"\s+", " ", (raw or "")).strip().strip("\"'")
    words = text.split()
    if len(words) > 60:
        text = " ".join(words[:50])
    return text


def query_llm(prompt, max_tokens=300, temperature=0.2,
              base_url=None, model=None):
    body = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    r = requests.post(f"{base_url or DEFAULT_LLAMA_URL}/v1/chat/completions",
                      json=body, timeout=TIMEOUT_LLM)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_tags(raw, allowed):
    """Map free-form LLM output onto canonical allowed tags, max MAX_TAGS.

    Guarantees at least one content-rating tag (SFW/NSFW/Mature/18+)
    placed first, as the prompt requires.
    """
    canon = {a.lower(): a for a in allowed}
    rating_set = {r.lower() for r in RATING_TAGS}
    found = []
    text = (raw or "").strip()
    m = re.search(r"\[.*?\]", text, re.S)
    cands = []
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                cands = [str(x) for x in arr]
        except ValueError:
            cands = []
    if not cands:
        cands = re.split(r"[,;\n]+", text)
    for c in cands:
        key = str(c).strip().strip("\"'").lower()
        if key in canon and canon[key] not in found:
            found.append(canon[key])
        if len(found) >= MAX_TAGS:
            break
    # Keep exactly one rating, moved to the front; default to SFW if missing.
    ratings = [t for t in found if t.lower() in rating_set]
    rest = [t for t in found if t.lower() not in rating_set]
    rating = ratings[0] if ratings else "SFW"
    if rating not in canon.values() and rating.lower() in canon:
        rating = canon[rating.lower()]
    found = [rating] + [t for t in rest if t != rating]
    return found[:MAX_TAGS]


def parse_quality(raw):
    """Extract a 1-5 completeness rating from LLM output; None if unusable."""
    text = (raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            q = int(json.loads(m.group(0)).get("quality"))
            return min(max(q, 1), 5)
        except (ValueError, AttributeError, TypeError):
            pass
    m = re.search(r"\b([1-5])\b", text)
    return int(m.group(1)) if m else None


# --- SFW image gate (vision) ---
SFW_PROMPT = (
    "You are an SFW image moderator for a public gallery. Look at the "
    "image and answer NO if it shows exposed nudity (genitals, "
    "breasts/nipples), sexual acts, sheer/see-through clothing exposing "
    "intimate areas, or overtly sexual posing. Lingerie, underwear, or "
    "swimwear that fully covers intimate areas without exposure is YES. "
    "Use your best judgment for borderline cases. "
    "Reply with ONLY the word YES or NO. No explanation."
)
SFW_MAX_BYTES = 8 * 1024 * 1024  # skip giant files before base64


def parse_sfw(raw):
    """Map vision LLM output to 'safe' | 'unsafe' | None.

    Hedged answers (MAYBE/UNCERTAIN/BORDERLINE/SUGGESTIVE) return None
    so borderline images stay unchecked instead of auto-condemned.
    """
    text = (raw or "").strip().upper()
    m = re.search(r"\b(YES|NO|SAFE|UNSAFE|SFW|NSFW|MAYBE|UNCERTAIN|"
                  r"BORDERLINE|SUGGESTIVE)\b", text)
    if not m:
        return None
    tok = m.group(1)
    if tok in ("YES", "SAFE", "SFW"):
        return "safe"
    if tok in ("NO", "UNSAFE", "NSFW"):
        return "unsafe"
    return None


def fetch_image_bytes(url):
    """Download image bytes; raises on non-image / oversize / HTTP error."""
    r = requests.get(url, timeout=TIMEOUT_FETCH,
                     headers={"User-Agent": "VividChat-Tagger/1.0"},
                     stream=True)
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    buf = bytearray()
    for chunk in r.iter_content(65536):
        buf.extend(chunk)
        if len(buf) > SFW_MAX_BYTES:
            raise ValueError("image exceeds %d bytes" % SFW_MAX_BYTES)
    data = bytes(buf)
    if ctype and not (ctype.startswith("image/")
                      or ctype == "application/octet-stream"):
        raise ValueError("not an image: %s" % ctype)
    if data[:4] == b"\x89PNG":
        mime = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return data, mime


def query_llm_vision(image_b64, mime, base_url=None, model=None,
                     prompt=SFW_PROMPT):
    """Send one base64 image to a vision model; return raw YES/NO text."""
    base = (base_url or DEFAULT_LLAMA_URL).rstrip("/")
    mdl = model or DEFAULT_MODEL
    # 1) Ollama native /api/chat (best for gemma vision).
    try:
        r = requests.post(base + "/api/chat", json={
            "model": mdl, "stream": False,
            "messages": [{"role": "user", "content": prompt,
                          "images": [image_b64]}]},
            timeout=TIMEOUT_LLM)
        r.raise_for_status()
        msg = (r.json().get("message") or {}).get("content", "")
        if msg.strip():
            return msg
    except requests.RequestException:
        pass  # fall through to OpenAI-compatible endpoint
    # 2) OpenAI-compatible /v1/chat/completions (llama.cpp + Ollama).
    body = {"model": mdl, "temperature": 0.0, "max_tokens": 5,
            "stream": False, "messages": [{
                "role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:%s;base64,%s" % (mime, image_b64)}}]}]}
    r = requests.post(base + "/v1/chat/completions", json=body,
                      timeout=TIMEOUT_LLM)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def needs_sfw_check(row):
    """True when image is new (no verdict) or URL changed since check."""
    url = (row.get("image_url") or "").strip()
    if not url:
        return False
    if not (row.get("image_sfw") or "").strip():
        return True
    checked = (row.get("image_checked_url") or "").strip()
    return checked != url  # photo changed -> re-download + re-ask LLM


def collect_role_text(conn, row):
    parts = ["Name: " + row["rolename"],
             "Description: " + (row["description"] or "")]
    try:
        body = fetch_text(row["data_raw_url"])
        if body:
            parts.append("Role file (%s):\n%s" % (row["data_filename"], body))
    except requests.RequestException as e:
        print("    warn: role file fetch failed: %s" % e)
    return "\n\n".join(parts)[:MAX_CHARS]


def collect_story_text(conn, row):
    parts = ["Name: " + row["storyname"],
             "Description: " + (row["description"] or "")]
    try:
        body = fetch_text(row["story_raw_url"])
        if body:
            parts.append("Story file (%s):\n%s" % (row["story_filename"], body))
    except requests.RequestException as e:
        print("    warn: story file fetch failed: %s" % e)
    files = conn.execute(
        "SELECT kind, name, filename, raw_url FROM story_files "
        "WHERE story_id=? ORDER BY kind, name", (row["id"],)).fetchall()
    for f in files:  # scenes + characters concatenated
        try:
            body = fetch_text(f["raw_url"])
        except requests.RequestException as e:
            print("    warn: %s fetch failed: %s" % (f["filename"], e))
            continue
        if body:
            parts.append("[%s: %s] (%s):\n%s"
                         % (f["kind"], f["name"], f["filename"], body))
        if sum(len(p) for p in parts) >= MAX_CHARS:
            break
    return "\n\n".join(parts)[:MAX_CHARS]

def write_skipped_txt(skipped, started, finished, out=None):
    out = Path(out or SKIPPED_PATH)
    legacy = [s for s in skipped if s.get("legacy")]
    lines = ["Skipped gists (%d)" % len(skipped),
             "Scan %s -> %s" % (started, finished), ""]
    if legacy:
        lines += ["Legacy-prefixed gists (%d) -- rename, or clean up by hand:"
                  % len(legacy),
                  "  rename them all with",
                  "    python tools/fix_gist_prefix.py --apply",
                  "  (or fix each file by hand), then rescan with",
                  "    python scan_gists.py",
                  "  to import them under the GOCHATAPP*TAG names.", ""]
    for s in skipped:
        gist_url = s.get("gist_url") or \
            ("https://gist.github.com/%s" % s["gist_id"])
        lines.append("- %s  %s%s" % (s["gist_id"], gist_url,
                                     "  [LEGACY]" if s.get("legacy") else ""))
        lines.append("    owner:  %s" % (s.get("owner") or "?"))
        lines.append("    reason: %s" % (s.get("reason") or "?"))
        lines.append("    files:  %s"
                     % (", ".join(s.get("files") or []) or "(none)"))
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out

def export_json(conn):
    """Write root db_chatgo.json; web/static copy happens at end of main()."""
    roles = [dict(x) for x in conn.execute("SELECT * FROM roles ORDER BY id")]
    stories = [dict(x) for x in conn.execute(
        "SELECT * FROM stories ORDER BY id")]
    for s in stories:
        for key, cond in (("scenes", "='scene'"),
                          ("characters", "='character'")):
            s[key] = [dict(x) for x in conn.execute(
                "SELECT kind, name, filename, raw_url, size FROM story_files"
                " WHERE story_id=? AND kind" + cond + " ORDER BY name",
                (s["id"],))]
        s["other_files"] = [dict(x) for x in conn.execute(
            "SELECT kind, name, filename, raw_url, size FROM story_files"
            " WHERE story_id=? AND kind NOT IN ('scene','character')"
            " ORDER BY filename", (s["id"],))]
        s["all_files"] = [dict(x) for x in conn.execute(
            "SELECT filename, raw_url, size FROM gist_files WHERE gist_id=?"
            " ORDER BY filename", (s["gist_id"],))]
    for r in roles:
        r["all_files"] = [dict(x) for x in conn.execute(
            "SELECT filename, raw_url, size FROM gist_files WHERE gist_id=?"
            " ORDER BY filename", (r["gist_id"],))]
    runs = [dict(x) for x in conn.execute(
        "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 5")]
    data = {"roles": roles, "stories": stories, "recent_scans": runs,
            "tag_rank": load_tag_ranks()}
    JSON_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data

def load_gist_target(path=CONFIG_PATH):
    """Return (gist_id, gist_url) from scan_gist.yaml.

    gist_id wins; gist_url is parsed as a fallback, so only one of the
    two has to be set. The token is no longer read from this file -- it
    comes from gist_token.txt / the environment (see resolve_token).
    """
    try:
        import yaml
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except ImportError:
        print("warn: pyyaml not installed, cannot read %s."
              % Path(path).name)
        return None, None
    except Exception as e:
        print("warn: could not read %s (%s)." % (Path(path).name, e))
        return None, None
    gist_id = str(cfg.get("gist_id") or "").strip()
    gist_url = str(cfg.get("gist_url") or "").strip()
    if not gist_id and gist_url:
        m = re.search(r"/([0-9a-f]{20,})(?=[/?#]|$)", gist_url, re.I)
        gist_id = m.group(1).lower() if m else ""
    return gist_id or None, gist_url or None


def push_json_to_gist(json_path: Path, gist_id: str, token: str):
    """Upload json_path into the gist, preserving every other file.

    Only the uploaded filename appears in the PATCH, so untouched gist
    files stay as they are (echoing the whole files dict back would
    re-send truncated content for large files).
    """
    import requests
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json"}
    content = json_path.read_text(encoding="utf-8")
    patch = {"files": {json_path.name: {"content": content}}}
    r = requests.patch(url, headers=headers, json=patch, timeout=60)
    r.raise_for_status()
    print(f"Updated gist {gist_id} with {json_path.name}")
    try:
        names = r.json().get("files") or {}
    except ValueError:
        names = {}
    if names and "db_vivid.json" in names and json_path.name != "db_vivid.json":
        print("  note: gist still holds the old db_vivid.json; nothing reads "
              "it any more, safe to delete by hand.")

def run_prune_dead(conn, dry=False, seen=None):
    """Drop records whose gist is provably gone.

    A record is only dropped when the GitHub API returns 404/410 for the
    gist *and* its stored raw URL 404s too, so a gist that is merely
    absent from search results (index lag, secret gist) is kept. When
    `seen` is given, rows whose gist the scan just found are never
    probed.
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    if TOKEN:
        s.headers["Authorization"] = "Bearer %s" % TOKEN
    seen = set(seen or ())
    dropped = kept = 0
    for table, name_col, url_col in (("roles", "rolename", "data_raw_url"),
                                     ("stories", "storyname", "story_raw_url")):
        rows = conn.execute("SELECT id, %s AS nm, %s AS u FROM %s"
                            % (name_col, url_col, table)).fetchall()
        for gid, nm, url in rows:
            if gid in seen:
                kept += 1
                continue
            try:
                api = s.get("https://api.github.com/gists/%s" % gid,
                            timeout=30)
                code = api.status_code
            except requests.RequestException as e:
                print("  keep %s %s: probe failed (%s)" % (table, gid, e))
                kept += 1
                continue
            if code not in (404, 410):
                kept += 1
                continue
            raw_code = 404
            if url:
                try:
                    raw_code = s.get(url, timeout=30).status_code
                except requests.RequestException:
                    raw_code = 0
            if raw_code not in (404, 410):
                print("  keep %s %s: API %d but raw URL %s"
                      % (table, gid, code, raw_code))
                kept += 1
                continue
            label = table[:-1].upper()
            if dry:
                print("  [dry-run] would drop %s %s (%s): gist deleted"
                      % (label, gid, nm))
            else:
                conn.execute("DELETE FROM %s WHERE id=?" % table, (gid,))
                conn.execute("DELETE FROM gist_files WHERE gist_id=?", (gid,))
                if table == "stories":
                    conn.execute("DELETE FROM story_files WHERE story_id=?",
                                 (gid,))
                print("  dropped %s %s (%s): gist deleted" % (label, gid, nm))
            dropped += 1
    if not dry:
        conn.commit()
    print("Prune: dropped=%d kept=%d%s"
          % (dropped, kept, " (dry-run, nothing written)" if dry else ""))


def run_scan(conn, do_init, refresh=False, owners=()):
    s = requests.Session()
    s.headers.update(HEADERS)
    ah = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    print("GitHub API auth: %s" % (TOKEN_SOURCE if TOKEN
                                   else "none (unauthenticated, 60 req/hr)"),
          flush=True)
    crawl_owners = search_owners(conn, owners)
    print("Owner crawl (user:...): %s" % ", ".join(crawl_owners), flush=True)
    gids, scraped_updated = collect_search_gids(s, crawl_owners)
    print(f"Found {len(gids)} gists", flush=True)
    print(f"Fetching details for {len(gids)} gists, this takes a while...",
          flush=True)
    if do_init:
        wipe_all(conn)
        print("Wiped all data (--init)")
    if refresh and not do_init:
        print("Refresh: re-fetching every gist, ignoring cached timestamps",
              flush=True)
    started = now_iso()
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    n_roles = n_stories = 0
    # Phase 1: fetch all gists, group records by id, keep newest gist first
    # so the newest version wins deterministically (not scrape order).
    pending_roles = {}
    pending_stories = {}
    skipped = []
    ts_now = now_iso()

    def _touch_unchanged(gid):
        """Mark known records as rescanned without a full API fetch.

        Returns True if the gist was already in the DB and only its
        last_scanned was bumped (so the caller can skip the fetch).
        """
        hit = False
        for table in ("roles", "stories"):
            row = conn.execute(f"SELECT id FROM {table} WHERE id=?",
                               (gid,)).fetchone()
            if row:
                conn.execute(f"UPDATE {table} SET last_scanned=? WHERE id=?",
                             (ts_now, gid))
                hit = True
        return hit

    def _db_master_names(gid):
        """(master filename, owner) this gist is already stored under.

        Used to report legacy rows that are already in the database: they are
        left untouched (the scan no longer imports or refreshes legacy names)
        but still listed in scan_skipped.txt so they can be cleaned up.
        """
        out = []
        for table, col in (("roles", "data_filename"),
                           ("stories", "story_filename")):
            row = conn.execute("SELECT %s, owner FROM %s WHERE id=?"
                               % (col, table), (gid,)).fetchone()
            if row and row[0]:
                out.append((row[0], row[1] or "?"))
        return out

    for i, gid in enumerate(gids):
        if not do_init and not refresh:
            scraped_ts = (scraped_updated or {}).get(gid)
            if scraped_ts:
                known_ts = conn.execute(
                    "SELECT gist_updated_at FROM roles WHERE id=?"
                    " UNION ALL "
                    "SELECT gist_updated_at FROM stories WHERE id=?",
                    (gid, gid)).fetchall()
                known = [r[0] for r in known_ts if r[0]]
                if known and all(k == scraped_ts for k in known):
                    # Matches our record(s) -> unchanged, skip API + thumb
                    # fetches entirely, just bump last_scanned.
                    _touch_unchanged(gid)
                    counts["unchanged"] += 1
                    sel = "role" if conn.execute(
                        "SELECT 1 FROM roles WHERE id=?",
                        (gid,)).fetchone() else "story"
                    if sel == "role":
                        n_roles += 1
                    else:
                        n_stories += 1
                    print(f"  {sel.upper()} {gid}: unchanged (cached)")
                    # A legacy row already in the DB is never refreshed again,
                    # so name it in the skip log instead of dropping it
                    # silently -- cleanup stays a manual decision.
                    db_names = [(fn, ow) for fn, ow in _db_master_names(gid)
                                if legacy_prefix_of(fn)]
                    if db_names:
                        prefixes = sorted({legacy_prefix_of(fn)
                                           for fn, _ow in db_names})
                        reason = ("legacy filename prefix (%s) already in the "
                                  "database: left untouched, not refreshed -- "
                                  "rename with python tools/fix_gist_prefix.py "
                                  "--apply, or drop the row by hand, then "
                                  "rescan" % ", ".join(prefixes))
                        print(f"  LEGACY {gid}: stored as "
                              f"{', '.join(fn for fn, _ow in db_names)}")
                        skipped.append({
                            "gist_id": gid,
                            "gist_url": "https://gist.github.com/%s" % gid,
                            "owner": db_names[0][1], "reason": reason,
                            "legacy": True,
                            "files": sorted(fn for fn, _ow in db_names)})
                    continue
        g, err = api_get_json(s, f"https://api.github.com/gists/{gid}", ah)
        if err or not isinstance(g, dict):
            reason = err or "empty response"
            print(f"  SKIP {gid}: {reason}")
            skipped.append({"gist_id": gid,
                            "gist_url": "https://gist.github.com/%s" % gid,
                            "owner": "?", "reason": reason,
                            "files": []})
            continue
        files = g.get("files", {}) or {}
        role_f, story_f, thumb, scenes, chars, others = classify_files(files)
        owner = (g.get("owner") or {}).get("login", "Unknown")
        url = g.get("html_url") or ("https://gist.github.com/%s" % gid)
        # Legacy-prefixed masters are never imported: drop them here, before
        # anything reaches pending_roles/pending_stories, and log the gist so
        # it shows up in scan_skipped.txt. A gist that mixes a legacy file
        # with a properly tagged one still imports the tagged record.
        legacy_hits = []
        if role_f and legacy_prefix_of(role_f[1]):
            legacy_hits.append(role_f[1])
            role_f = None
        if story_f and legacy_prefix_of(story_f[1]):
            legacy_hits.append(story_f[1])
            story_f = None
        if legacy_hits:
            prefixes = sorted({legacy_prefix_of(fn) for fn in legacy_hits})
            reason = ("legacy filename prefix (%s): not imported -- rename "
                      "with python tools/fix_gist_prefix.py --apply, or fix "
                      "the file by hand, then rescan"
                      % ", ".join(prefixes))
            if _db_master_names(gid):
                reason += " (an old copy is already in the database and is" \
                          " left untouched)"
            print(f"  SKIP {gid}: legacy prefix ({', '.join(prefixes)}): "
                  f"{', '.join(sorted(legacy_hits))}")
            if role_f or story_f:
                reason += " (the tagged file(s) in this gist were imported)"
            skipped.append({"gist_id": gid, "gist_url": url, "owner": owner,
                            "reason": reason, "legacy": True,
                            "files": sorted(legacy_hits)})
        if not role_f and not story_f:
            if not legacy_hits:
                print(f"  SKIP {gid}: no role/story master file")
                skipped.append({"gist_id": gid, "gist_url": url,
                                "owner": owner,
                                "reason": "no role/story master file "
                                "(need %s_<name>.txt or %s_<name>.md)"
                                % (ROLE_TAG_PREFIX, STORY_TAG_PREFIX),
                                "files": sorted(files.keys())})
            continue
        img = None
        if thumb and thumb[1].get("raw_url"):
            try:
                t = s.get(thumb[1].get("raw_url"), timeout=30, headers=ah)
                t.raise_for_status()
                img = parse_image_url(t.text)
            except requests.RequestException:
                pass
        base = dict(gist_id=gid, gist_url=url, owner=owner,
                    description=g.get("description"),
                    thumb_filename=thumb[0] if thumb else None,
                    thumb_raw_url=thumb[1].get("raw_url") if thumb else None,
                    image_url=img, gist_created_at=g.get("created_at"),
                    gist_updated_at=g.get("updated_at"))
        if role_f:
            raw_name, fn, det = role_f
            rec = dict(base, id=gid, rolename=raw_name.strip(),
                       data_filename=fn, data_raw_url=det.get("raw_url"),
                       data_size=det.get("size"))
            # keyed by gist so duplicate names from different gists all survive
            pending_roles[gid] = (rec, files, gid)
        if story_f:
            raw_name, fn, det = story_f
            rec = dict(base, id=gid, storyname=raw_name.strip(),
                       story_filename=fn, story_raw_url=det.get("raw_url"),
                       story_size=det.get("size"))
            # keyed by gist so duplicate names from different gists all survive
            pending_stories[gid] = (rec, scenes + chars + others, files)
        # Slower without auth (60 req/hr unauthenticated): 2s vs 1s pause.
        if i < len(gids) - 1:
            time.sleep(PAGE_PAUSE if ah else 2.0)
    conn.commit()  # persist cached last_scanned bumps + keep Phase 2 atomic
    # Phase 2: sync newest-only records
    for rid in sorted(pending_roles):
        rec, files, gid = pending_roles[rid]
        st = sync_role(conn, rec)
        record_gist_files(conn, gid, "role", rec["id"], files)
        counts[st] += 1
        n_roles += 1
        print(f"  ROLE {rec['id']}: {st}"
              f" img={'Y' if rec['image_url'] else 'N'}")
        conn.commit()
    for sid in sorted(pending_stories):
        rec, sfiles, files = pending_stories[sid]
        st, af, rf = sync_story(conn, rec, sfiles)
        record_gist_files(conn, rec["gist_id"], "story", sid, files)
        counts[st] += 1
        n_stories += 1
        extra = ""
        if af:
            extra += f" +files{af}"
        if rf:
            extra += f" -files{rf}"
        nsc = sum(1 for f in sfiles if f["kind"] == "scene")
        nch = sum(1 for f in sfiles if f["kind"] == "character")
        print(f"  STORY {sid}: {st}{extra} sc={nsc} ch={nch}")
        conn.commit()
    finished = now_iso()
    conn.execute("INSERT INTO scan_runs (started_at, finished_at, gists_found,"
                 " roles_seen, stories_seen, added, updated, unchanged)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (started, finished, len(gids), n_roles, n_stories,
                  counts["added"], counts["updated"], counts["unchanged"]))
    conn.commit()
    print(f"Scan: roles={n_roles} stories={n_stories}"
          f" added={counts['added']} updated={counts['updated']}"
          f" unchanged={counts['unchanged']}")
    skipped_path = write_skipped_txt(skipped, started, finished)
    n_legacy = sum(1 for s in skipped if s.get("legacy"))
    print(f"Skipped: {len(skipped)} -> {skipped_path}"
          + (f" ({n_legacy} legacy-prefix)" if n_legacy else ""))
    return set(gids), {"total": len(skipped), "legacy": n_legacy,
                       "path": skipped_path}


def run_tag(conn, llama_url, model, force=False, dry=False, limit=None):
    def ask(prompt, max_tokens=300, temperature=0.2):
        return query_llm(prompt, max_tokens=max_tokens,
                         temperature=temperature,
                         base_url=llama_url, model=model)

    allowed = load_allowed_tags()
    print("Allowed tags: %d from %s" % (len(allowed), TAGS_PATH.name))
    if not allowed:
        print("No tags parsed from tag_allowed.txt; skipping tag phase.")
        return
    ensure_columns(conn)

    roles = [dict(r) for r in conn.execute("SELECT * FROM roles ORDER BY id")]
    stories = [dict(r) for r in conn.execute("SELECT * FROM stories ORDER BY id")]
    targets = [("role", r) for r in roles] + [("story", s) for s in stories]
    if not force:
        targets = [(k, r) for k, r in targets
                   if needs_tag(r) or needs_description(r)
                   or (k == "role" and needs_quality(r))]
    if limit is not None:
        targets = targets[:limit]
    nr = sum(1 for k, _ in targets if k == "role")
    ns = sum(1 for k, _ in targets if k == "story")
    print("To process: %d (roles=%d, stories=%d)%s"
          % (len(targets), nr, ns, " [dry-run]" if dry else ""))

    done = failed = 0
    for kind, rec in targets:
        rid = rec["id"]
        nm = rec["rolename"] if kind == "role" else rec["storyname"]
        print("  %s %s (%s) last_changed=%s"
              % (kind.upper(), rid, nm, rec["last_changed"]))
        if dry:
            continue
        blob = (collect_role_text(conn, rec) if kind == "role"
                else collect_story_text(conn, rec))
        table = "roles" if kind == "role" else "stories"
        ts = now_iso()
        # Tags are current -> only backfill what is missing (empty gist
        # description, missing 1-5 quality) instead of re-asking for tags.
        if not needs_tag(rec) and (needs_description(rec) or
                                   (kind == "role" and needs_quality(rec))):
            wrote = False
            if needs_description(rec):
                try:
                    summary = clean_summary(ask(
                        build_summary_prompt(blob, kind, nm),
                        max_tokens=200, temperature=0.7))
                except (requests.RequestException, KeyError) as e:
                    print("    summary LLM error: %s" % e)
                    summary = ""
                if summary:
                    conn.execute("UPDATE %s SET description=? WHERE id=?"
                                 % table, (summary, rid))
                    conn.commit()
                    wrote = True
                    print("    +description (%d words)"
                          % len(summary.split()))
                else:
                    print("    empty summary returned")
            if kind == "role" and needs_quality(rec):
                quality = None
                try:
                    quality = parse_quality(
                        ask(build_quality_prompt(blob, kind, nm)))
                except (requests.RequestException, KeyError) as e:
                    print("    quality LLM error: %s" % e)
                if quality is None:
                    print("    quality unparseable; will retry next run")
                else:
                    conn.execute("UPDATE roles SET quality=? WHERE id=?",
                                 (quality, rid))
                    conn.commit()
                    wrote = True
                    print("    +quality=%s" % quality)
            if wrote:
                done += 1
            else:
                failed += 1
            continue
        try:
            raw = ask(build_prompt(allowed, blob, kind, nm))
        except (requests.RequestException, KeyError) as e:
            print("    LLM error: %s" % e)
            failed += 1
            continue
        tags = parse_tags(raw, allowed)
        if not tags:
            print("    no valid tags parsed; raw=%r" % raw[:200])
            failed += 1
            continue
        conn.execute("UPDATE %s SET tags=?, tagged_at=? WHERE id=?"
                     % table, (json.dumps(tags), ts, rid))
        # Backfill empty gist description with a 50-word LLM summary
        desc_done = False
        if not (rec.get("description") or "").strip():
            try:
                summary = clean_summary(ask(
                    build_summary_prompt(blob, kind, nm),
                    max_tokens=200, temperature=0.7))
            except (requests.RequestException, KeyError) as e:
                print("    summary LLM error: %s" % e)
                summary = ""
            if summary:
                conn.execute("UPDATE %s SET description=? WHERE id=?"
                             % table, (summary, rid))
                desc_done = True
        quality = None
        if kind == "role":
            try:
                quality = parse_quality(
                    ask(build_quality_prompt(blob, kind, nm)))
            except (requests.RequestException, KeyError) as e:
                print("    quality LLM error: %s" % e)
            conn.execute("UPDATE roles SET quality=? WHERE id=?",
                         (quality, rid))
        conn.commit()
        print("    tags=%s%s%s" % (tags, " quality=%s" % quality
                                 if kind == "role" else "",
                                 " +description" if desc_done else ""))
        done += 1

    print("Tagged: %d failed=%d" % (done, failed))


def run_sfw(conn, llama_url, model, force=False, dry=False, limit=None):
    """Download unchecked/changed thumbs, ask vision LLM, flag DB.

    Only images with no verdict (or --force) are downloaded; same-byte
    images (sha256 match) keep their verdict without an LLM call.
    """
    import base64
    import hashlib
    ensure_columns(conn)
    roles = [dict(r) for r in conn.execute("SELECT * FROM roles ORDER BY id")]
    stories = [dict(r) for r in conn.execute("SELECT * FROM stories ORDER BY id")]
    targets = [("role", r) for r in roles] + [("story", s) for s in stories]
    targets = [(k, r) for k, r in targets if (r.get("image_url") or "").strip()]
    if not force:
        targets = [(k, r) for k, r in targets if needs_sfw_check(r)]
    if limit is not None:
        targets = targets[:limit]
    nr = sum(1 for k, _ in targets if k == "role")
    ns = sum(1 for k, _ in targets if k == "story")
    print("SFW to check: %d (roles=%d, stories=%d)%s"
          % (len(targets), nr, ns, " [dry-run]" if dry else ""))
    if dry:
        for kind, rec in targets:
            nm = rec["rolename"] if kind == "role" else rec["storyname"]
            print("  %s %s (%s) img=%s" % (kind.upper(), rec["id"], nm,
                                           (rec["image_url"] or "")[:80]))
        return
    done = failed = skipped = 0
    for kind, rec in targets:
        rid = rec["id"]
        nm = rec["rolename"] if kind == "role" else rec["storyname"]
        url = (rec["image_url"] or "").strip()
        table = "roles" if kind == "role" else "stories"
        print("  %s %s (%s)" % (kind.upper(), rid, nm))
        try:
            data, mime = fetch_image_bytes(url)
        except Exception as e:
            print("    download failed: %s" % e)
            failed += 1
            continue
        digest = hashlib.sha256(data).hexdigest()
        if not force and rec.get("image_hash") == digest and rec.get("image_sfw"):
            print("    unchanged image (hash match), keeping %s" % rec["image_sfw"])
            skipped += 1
            continue
        try:
            raw = query_llm_vision(base64.b64encode(data).decode("ascii"),
                                   mime, base_url=llama_url, model=model)
        except Exception as e:
            print("    vision LLM error: %s" % e)
            failed += 1
            continue
        verdict = parse_sfw(raw)
        if not verdict:
            print("    unparseable verdict: %r" % (raw or "")[:100])
            failed += 1
            continue
        conn.execute("UPDATE %s SET image_sfw=?, image_sfw_at=?, "
                     "image_sfw_model=?, image_hash=?, image_checked_url=? "
                     "WHERE id=?"
                     % table, (verdict, now_iso(), model, digest, url, rid))
        conn.commit()
        print("    image_sfw=%s" % verdict)
        done += 1
    print("SFW: flagged=%d failed=%d skipped=%d" % (done, failed, skipped))

def main():
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv
    no_prompt = "--no-prompt" in sys.argv
    do_init = "--init" in sys.argv
    refresh = "--refresh" in sys.argv
    prune_dead = "--prune-dead" in sys.argv
    skip_scan = "--skip-scan" in sys.argv
    skip_tag = "--skip-tag" in sys.argv
    skip_sfw = "--skip-sfw" in sys.argv
    force_sfw = "--force-sfw" in sys.argv
    limit = None
    sfw_limit = None
    cli_server = cli_model = None
    cli_sfw_server = cli_sfw_model = None
    cli_owners = []
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            try:
                limit = int(a.split("=", 1)[1])
            except ValueError:
                print("bad %s; want --limit=N" % a)
                return
        elif a.startswith("--sfw-limit="):
            try:
                sfw_limit = int(a.split("=", 1)[1])
            except ValueError:
                print("bad %s; want --sfw-limit=N" % a)
                return
        elif a.startswith("--server="):
            cli_server = a.split("=", 1)[1].strip()
        elif a.startswith("--model="):
            cli_model = a.split("=", 1)[1].strip()
        elif a.startswith("--sfw-server="):
            cli_sfw_server = a.split("=", 1)[1].strip()
        elif a.startswith("--sfw-model="):
            cli_sfw_model = a.split("=", 1)[1].strip()
        elif a.startswith("--owners="):
            cli_owners = [o.strip() for o in a.split("=", 1)[1].split(",")
                          if o.strip()]
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    # --- Startup check: every run except --dry-run ends by pushing
    # db_chatgo.json into the gist, so a missing PAT must stop the run
    # here instead of after a long scan/tag pass.
    print("=== GitHub token ===")
    if dry:
        probe_token, probe_src = resolve_token()
        print("dry-run: gist write skipped, token %s"
              % ("found in %s" % probe_src if probe_token
                 else "not needed"))
    else:
        _, token_src = require_gist_token()
        print("gist token: %s" % token_src)

    # --- Startup question 1: what to do with the database ---
    print("=== Database ===")
    if skip_scan:
        print("Scan skipped (--skip-scan).")
    elif do_init:
        print("Init: database will be wiped before rescanning (--init).")
    elif refresh:
        print("Refresh: re-fetching every known gist (--refresh).")
    elif DB_PATH.exists() and not no_prompt:
        n_r = n_s = 0
        try:
            probe = sqlite3.connect(str(DB_PATH))
            n_r = probe.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
            n_s = probe.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
            probe.close()
        except sqlite3.Error:
            pass
        print("db_vivid.sqlite: %d roles, %d stories" % (n_r, n_s))
        idx, _ = pick_from_list("Database:", [
            "Scan: fetch gists, add new + update changed",
            "Init: wipe the database, then full rescan",
            "Skip scan: use the database as-is"], 0)
        if idx == 2:
            skip_scan = True
        elif idx == 1:
            do_init = True
    elif DB_PATH.exists():
        print("db_vivid.sqlite: scanning (non-interactive default)")
    else:
        print("db_vivid.sqlite: fresh database, scanning")

    # --- Startup question 2: LLM server + model ---
    llama_url = model = None
    sfw_url = sfw_model = None
    if not skip_tag or not skip_sfw:
        print("=== LLM setup ===")
        servers, models, default_url, default_model = load_config()
        llama_url = resolve_server(servers, default_url, cli_server,
                                   no_prompt)
        if cli_model or no_prompt:
            # Non-interactive: skip the network probe, use static default.
            model = resolve_model(models, default_model, cli_model,
                                  no_prompt)
        else:
            print("Querying %s for models..." % llama_url)
            live = fetch_server_models(llama_url)
            if live:
                print("  live: %d model(s)" % len(live))
                # Show only what this server actually has. Never merge in
                # models from other servers (e.g. SuperGemma on Ollama).
                merged = list(live)
                # Per-server default wins; else config default if that
                # server actually has it; else first live model (never a
                # model from a different server).
                srv = next((s for s in servers if s["url"] == llama_url),
                           {})
                srv_model = srv.get("model")
                if srv_model and srv_model in merged:
                    pick_default = srv_model
                elif srv_model and srv_model not in live:
                    print("  warn: %s model %r not on this server, "
                          "defaulting to %s." % (CONFIG_PATH.name,
                                                 srv_model, live[0]))
                    pick_default = live[0]
                elif default_model in live:
                    pick_default = default_model
                else:
                    pick_default = live[0]
                model = resolve_model(merged, pick_default, None, False)
            else:
                print("  warn: could not list models from %s, "
                      "using %s list." % (llama_url, CONFIG_PATH.name))
                model = resolve_model(models, default_model, None, False)
        print("Using server=%s model=%s" % (llama_url, model))
        # SFW vision routing: vision needs Ollama (SuperGemma lacks vision).
        # Default to the SAME host as TAG when TAG is already on Ollama, so
        # tags + images evaluate on one box. Explicit --sfw-server= wins.
        if cli_sfw_server:
            sfw_url = resolve_server(servers, llama_url, cli_sfw_server,
                                     no_prompt)
        elif llama_url and ":11434" in llama_url:
            sfw_url = llama_url
        else:
            ollama = next((s for s in servers if ":11434" in s["url"]),
                          None)
            sfw_url = (ollama["url"] if ollama else llama_url)
        if cli_sfw_model:
            sfw_model = cli_sfw_model
        elif "gemma4" in (default_model or "") or "gpt-oss" in (default_model or ""):
            sfw_model = default_model
        else:
            sfw_model = "gemma4:31b-cloud"
        print("Using SFW server=%s model=%s" % (sfw_url, sfw_model))
        # Fail fast: one probe before the 37-image loop instead of 37x
        # 300s connect timeouts against a dead host.
        for label, url in (("TAG", llama_url), ("SFW", sfw_url)):
            if url and not probe_llm_server(url):
                print("  ERROR: %s server unreachable: %s "
                      "(is Ollama running with OLLAMA_HOST=0.0.0.0:11434?)"
                      % (label, url))

    conn = connect(DB_PATH)
    seen = None
    skip_report = None
    if not skip_scan:
        print("=== Phase 1/3: SCAN ===")
        seen, skip_report = run_scan(conn, do_init, refresh, cli_owners)
    if prune_dead:
        print("=== Prune dead gists ===")
        run_prune_dead(conn, dry, seen)
    if not skip_tag:
        print("=== Phase 2/3: TAG ===")
        run_tag(conn, llama_url, model,
                force=force, dry=dry, limit=limit)
    if not skip_sfw and not dry:
        print("=== Phase 3/3: SFW ===")
        run_sfw(conn, sfw_url, sfw_model,
                force=force_sfw, dry=False, limit=sfw_limit)
    elif not skip_sfw and dry:
        run_sfw(conn, sfw_url or llama_url, sfw_model or model,
                force=force_sfw, dry=True, limit=sfw_limit)

    if dry:
        print("[dry-run] no JSON written")
    else:
        export_json(conn)
        print("Wrote %s" % JSON_PATH)
        # Push updated JSON to the configured gist. The token comes from
        # resolve_token() (gist_token.txt / env); only the target gist id
        # still comes from scan_gist.yaml.
        gist_id, _gist_url = load_gist_target()
        if gist_id:
            try:
                push_json_to_gist(JSON_PATH, gist_id, TOKEN)
            except Exception as e:
                print("Failed to push to gist:", e)
        else:
            print("gist_id/gist_url missing in %s, skipping gist update"
                  % CONFIG_PATH.name)
    role_total = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    story_total = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    print("Database totals: %d roles, %d stories (total %d)"
          % (role_total, story_total, role_total + story_total))
    conn.close()
    if skip_report and skip_report["total"]:
        print()
        if skip_report["legacy"]:
            print("!" * 72)
            print("!! %d legacy-prefixed gist(s) were SKIPPED (not imported,"
                  " or left as-is)." % skip_report["legacy"])
            print("!! Check %s for the list, then rename with"
                  % skip_report["path"].name)
            print("!!   python tools/fix_gist_prefix.py --apply")
            print("!! (or fix the files by hand) and rescan to import them.")
            print("!" * 72)
        rest = skip_report["total"] - skip_report["legacy"]
        if rest or not skip_report["legacy"]:
            print("Reminder: %d gist(s) skipped this run -- see %s"
                  % (skip_report["total"], skip_report["path"]))

if __name__ == "__main__":
    main()
