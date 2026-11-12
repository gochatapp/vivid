"""Test scan: probe GitHub gist search with candidate filename terms.

  python scan_probe.py                             # probes the current tags
  python scan_probe.py gochatapp                   # tags + gochatapp
  python scan_probe.py --only gochatapp            # only the listed terms
  python scan_probe.py --mode=user gochatapp       # user:<login> listing
  python scan_probe.py --mode=all gochatapp        # filename: + user: + text
  python scan_probe.py --no-details                # skip the per-gist API calls
  python scan_probe.py --pages=5 --pause=2 --limit=20

Answers one question: does gist search find this term, and whose records are
they? Each term is run through the same query shape scan_gists.py uses
(filename:<term>), pages are walked the same way (10 ids per page, stop when
a page adds nothing, cap 20 pages), and every hit is opened through the API so
the real filenames are listed and ROLE_RE / STORY_RE decide whether
scan_gists.py would accept the gist. Gists already stored are also marked by
looking up db_vivid.sqlite (read-only).

Why terms fail: gist search matches whole filename *tokens*, not substrings.
A file named gochatapp.github.io_role_aiko.txt is indexed as the tokens
gochatapp / github / io / role / aiko, so filename:gochatapp.github.io returns
unrelated *.github.io gists and filename:gochatappgithub returns nothing. The
current tags put the kind INSIDE one separator-free token --
GOCHATAPPROLETAG_aiko.txt / GOCHATAPPSTORYTAG_foo.md -- so filename:
GOCHATAPPROLETAG finds the roles and filename:GOCHATAPPSTORYTAG the stories.

This is a read-only test: nothing is written to the database, scan_skipped.txt
or db_chatgo.json, no gist is edited, and no LLM is called. A GitHub token
(GH_TOKEN / gist_token.txt / gist_local.yaml / scan_gist.yaml, the same
resolution scan_gists.py uses) is optional -- it only raises the API rate
limit used to list filenames.

Flags:
  --mode=filename|user|plain|all  query shapes to run (default: filename)
  --pages=N       result pages per query, 1..20 (default: 10)
  --pause=SEC     pause between requests, shared with the crawler (default: 1)
  --limit=N       cap gists opened through the API per query (default: all)
  --no-details    only scrape the result pages (no API calls, no DB lookups)
  --only          probe just the terms given, not the current tags first
  --help          this text

Exit code: 0 when at least one query returned gists, 1 when nothing was found,
2 on a bad flag.
"""
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

# Works both from chatDB/ and from a subfolder such as chatDB/tools/
# (same import bootstrap as tools/gist_fix_names.py).
_BASE_DIR = Path(__file__).resolve().parent
if not (_BASE_DIR / "scan_gists.py").exists() \
        and (_BASE_DIR.parent / "scan_gists.py").exists():
    sys.path.insert(0, str(_BASE_DIR.parent))

from scan_gists import (DB_PATH, ROLE_RE, ROLE_TAG_PREFIX,  # noqa: E402
                        SEARCH_TERMS, STORY_RE, TAG_PREFIXES,
                        api_get_json, resolve_token)

# The terms under test: the tags carry the kind and have no separator inside,
# so each is one filename token and filename:<tag> finds our records.
DEFAULT_TERMS = TAG_PREFIXES
MODES = ("filename", "user", "plain", "all")
PAGE_PAUSE = 1.0   # keep in step with scan_gists.PAGE_PAUSE
MAX_PAGES = 20     # same safety cap as scan_gists.collect_search_gids

# Search pages are HTML: the API Accept header makes github.com answer 404
# for gist pages (see scan_diagnose.py), so keep it on the API session only.
HTML_HEADERS = {"User-Agent": "VividChat-Gist-Scanner/1.0",
                "Accept": "text/html,application/xhtml+xml"}
API_HEADERS = {"User-Agent": "VividChat-Gist-Scanner/1.0",
               "Accept": "application/vnd.github+json"}

GID_RE = re.compile(r'href="/[^/"?#]+/([0-9a-f]{20,})(?=[/?#"]|$)', re.I)
TOTAL_RE = re.compile(r"([\d,]+)\s+gists?\b", re.I)


def search_url(term, page=1):
    """URL for filename:<term> -- the query scan_gists.py discovers with."""
    base = "https://gist.github.com/search?q=" + quote("filename:" + term)
    return base + "&p=%d" % page if page > 1 else base


def owner_url(login, page=1):
    """URL for user:<login> -- every gist of one account (prefix agnostic)."""
    base = "https://gist.github.com/search?q=" + quote("user:" + login)
    return base + "&p=%d" % page if page > 1 else base


def plain_url(term, page=1):
    """URL for a bare term (searches descriptions/content, not filenames)."""
    base = "https://gist.github.com/search?q=" + quote(term)
    return base + "&p=%d" % page if page > 1 else base


def queries_for(term, mode):
    """[(label, url_for)] for one term under --mode."""
    out = []
    if mode in ("filename", "all"):
        out.append(("filename:" + term, lambda p, t=term: search_url(t, p)))
    if mode in ("user", "all"):
        out.append(("user:" + term, lambda p, t=term: owner_url(t, p)))
    if mode in ("plain", "all"):
        out.append(("text:" + term, lambda p, t=term: plain_url(t, p)))
    return out


def get_html(s, url):
    """GET a gist search page; returns (html, error). Never raises.

    The HTML search endpoint is rate limited per IP (HTTP 429 -- it is the
    same budget a browser tab uses), so a 429 is reported instead of
    raised: scan_gists.collect_search_gids calls raise_for_status() there
    and would abort the whole scan. Waits honour X-RateLimit-Reset, like
    scan_gists.api_get_json.
    """
    for attempt in range(3):
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException as e:
            return None, "fetch error: %s" % e
        if r.status_code == 200:
            return r.text, None
        body = (r.text or "")[:200]
        limited = r.headers.get("X-RateLimit-Remaining") == "0" \
            or "rate limit" in body.lower() or "whoa there" in body.lower()
        if r.status_code in (403, 429) and limited:
            wait = 60
            reset = r.headers.get("X-RateLimit-Reset")
            if reset and reset.isdigit():
                wait = max(1, int(reset) - int(time.time()) + 5)
            print("  rate limited (HTTP %d), waiting %ds (attempt %d/3)..."
                  % (r.status_code, wait, attempt + 1), flush=True)
            time.sleep(wait)
            continue
        return None, "HTTP %d: %s" % (r.status_code, body)
    return None, "rate limited: retries exhausted"


def reported_total(html):
    """Best-effort 'N gists' result count from a search page (None if absent)."""
    nums = []
    for raw in TOTAL_RE.findall(html):
        try:
            nums.append(int(raw.replace(",", "")))
        except ValueError:
            pass
    return max(nums) if nums else None


def collect_ids(s, label, url_for, pages, pause):
    """Scrape gist IDs from one query's result pages.

    Returns (ids, total, aborted): ids is de-duplicated in page order,
    total is the count the page reports, aborted is True when a request
    failed so an empty result can be explained instead of blamed on the
    term.
    """
    ids, seen = [], set()
    total = None
    aborted = False
    for page in range(1, pages + 1):
        html, err = get_html(s, url_for(page))
        if err:
            print("  page %d (%s): %s" % (page, label, err))
            aborted = True
            break
        found = []
        for gid in GID_RE.findall(html):
            gid = gid.lower()
            if gid not in seen:
                seen.add(gid)
                found.append(gid)
        if total is None:
            total = reported_total(html)
        print("  page %d (%s): %d gist(s)" % (page, label, len(found)))
        if not found:
            break
        ids.extend(found)
        if page < pages:
            time.sleep(pause)
    return ids, total, aborted


def db_index():
    """gist id -> {'role', 'story'} for records already stored (read-only)."""
    out = {}
    uri = "file:%s?mode=ro" % Path(DB_PATH).as_posix()
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        print("  (database not readable: %s)" % e)
        return out
    try:
        for table, kind in (("roles", "role"), ("stories", "story")):
            try:
                rows = conn.execute("SELECT gist_id FROM %s" % table).fetchall()
            except sqlite3.Error:
                continue
            for (gid,) in rows:
                if gid:
                    out.setdefault(gid.lower(), set()).add(kind)
    finally:
        conn.close()
    return out


def gist_details(s, gid, auth):
    """API view of one gist (None when it cannot be fetched)."""
    data, err = api_get_json(s, "https://api.github.com/gists/" + gid, auth)
    if data is None:
        print("    %s: %s" % (gid, err))
    return data


def record_names(filenames):
    """[(kind, name)] for the role/story master files scan_gists.py accepts."""
    out = []
    for fn in filenames:
        match = ROLE_RE.match(fn)
        if match:
            out.append(("role", match.group(1)))
            continue
        match = STORY_RE.match(fn)
        if match:
            out.append(("story", match.group(1)))
    return out


def tokenisation_hint(term):
    """Explain a zero-result term (gist search indexes whole tokens)."""
    others = [t for t in TAG_PREFIXES if t.lower() != term.lower()]
    also = (" (other tags: %s)" % ", ".join(others)) if others else ""
    return ("gist search matches whole filename tokens, not substrings: "
            "'%s_<name>.txt' is indexed as %s / <name>, so a term only "
            "matches as a whole token, and only once a gist carries it%s. "
            "Older prefixed uploads (gochatapp.github.io_*, "
            "mystupidchatapps_*, vividchatapp_*) are not searchable by "
            "filename at all -- crawl them with user:<login>."
            % (ROLE_TAG_PREFIX, ROLE_TAG_PREFIX, also))


def _int_arg(arg):
    """Parse '--flag=N'; None when N is not an integer."""
    try:
        return int(arg.split("=", 1)[1])
    except (IndexError, ValueError):
        return None


def parse_args(argv):
    """Flag parsing in the same hand-rolled style as the other tools/*."""
    opts = {"mode": "filename", "pages": 10, "pause": PAGE_PAUSE,
            "limit": 0, "details": True, "only": False, "terms": []}
    for arg in argv:
        if arg in ("--help", "-h"):
            return None
        if arg == "--no-details":
            opts["details"] = False
        elif arg == "--only":
            opts["only"] = True
        elif arg.startswith("--mode="):
            opts["mode"] = arg.split("=", 1)[1].strip().lower()
        elif arg.startswith("--pages="):
            value = _int_arg(arg)
            if value is None:
                return "bad value: %s" % arg
            opts["pages"] = value
        elif arg.startswith("--pause="):
            try:
                opts["pause"] = max(0.0, float(arg.split("=", 1)[1]))
            except (IndexError, ValueError):
                return "bad value: %s" % arg
        elif arg.startswith("--limit="):
            value = _int_arg(arg)
            if value is None:
                return "bad value: %s" % arg
            opts["limit"] = max(0, value)
        elif arg.startswith("-"):
            return "unknown flag: %s" % arg
        else:
            opts["terms"].append(arg)
    if opts["mode"] not in MODES:
        return "bad --mode=%s (use %s)" % (opts["mode"], "|".join(MODES))
    if not 1 <= opts["pages"] <= MAX_PAGES:
        return "bad --pages=%d (1..%d)" % (opts["pages"], MAX_PAGES)
    return opts


def probe_terms(extra, only):
    """Terms to probe: DEFAULT_TERMS first unless --only, then the CLI ones."""
    raw = list(extra) if only else list(DEFAULT_TERMS) + list(extra)
    terms, seen = [], set()
    for term in raw:
        term = term.strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def describe_gist(api, gid, auth, known, pause):
    """Open one gist through the API and print what it holds."""
    data = gist_details(api, gid, auth)
    time.sleep(pause)
    if data is None:
        return None
    owner = (data.get("owner") or {}).get("login", "?")
    files = sorted((data.get("files") or {}).keys())
    records = record_names(files)
    stored = sorted(known.get(gid, ()))
    print("  %s  (%s)" % (data.get("html_url")
                          or "https://gist.github.com/" + gid, owner))
    print("    record files: %s" % (", ".join("%s %s" % r for r in records)
                                    or "NONE -- scan_gists.py would SKIP it"))
    print("    files: %s" % (", ".join(files) or "(none)"))
    print("    database: %s" % ("stored as " + "/".join(stored) if stored
                                else "not in the database yet (new)"))
    return {"gid": gid, "owner": owner, "records": records,
            "files": files, "stored": stored}


def main():
    opts = parse_args(sys.argv[1:])
    if opts is None:
        print(__doc__)
        return 0
    if isinstance(opts, str):
        print(opts)
        print("Run with --help for usage.")
        return 2
    terms = probe_terms(opts["terms"], opts["only"])
    if not terms:
        print("No terms to probe. Run with --help for usage.")
        return 2

    token, source = resolve_token()
    auth = {"Authorization": "Bearer %s" % token} if token else {}
    print("Test scan: gist search probe (read-only -- writes nothing)")
    print("Terms: %s" % ", ".join(terms))
    print("Crawler filename terms (scan_gists.SEARCH_TERMS): %s"
          % (", ".join(SEARCH_TERMS) or "(none)"))
    print("Crawler tags (scan_gists.TAG_PREFIXES): %s"
          % ", ".join(TAG_PREFIXES))
    print("Mode: %s   pages: %d   details: %s"
          % (opts["mode"], opts["pages"],
             "on" if opts["details"] else "off"))
    print("Token: %s" % (source or
                         "none (API details go unauthenticated, lower limit)"))
    print("")

    search = requests.Session()
    search.headers.update(HTML_HEADERS)
    api = requests.Session()
    api.headers.update(API_HEADERS)

    known = db_index() if opts["details"] else {}
    results = []
    for term in terms:
        for label, url_for in queries_for(term, opts["mode"]):
            print("== %s ==" % label)
            print("URL: %s" % url_for(1))
            ids, total, aborted = collect_ids(search, label, url_for,
                                              opts["pages"], opts["pause"])
            if total is not None:
                print("  page reports %d gist(s)" % total)
            if not ids:
                verdict = ("SEARCH FAILED (see the error above)"
                           if aborted else "NOT FOUND (no gist matches)")
                print("  %s" % verdict)
                if not aborted:
                    print("  %s" % tokenisation_hint(term))
                print("")
                results.append({"label": label, "ids": [], "details": [],
                                "note": verdict})
                continue

            details = []
            if opts["details"]:
                limit = opts["limit"] or len(ids)
                for gid in ids[:limit]:
                    entry = describe_gist(api, gid, auth, known,
                                          opts["pause"])
                    if entry:
                        details.append(entry)
                if limit < len(ids):
                    print("  (%d more gist(s) not opened -- raise --limit)"
                          % (len(ids) - limit))
            records = [d for d in details if d["records"]]
            new = [d for d in records if not d["stored"]]
            if opts["details"]:
                note = "%d record file(s), %d not in DB" % (len(records),
                                                            len(new))
            else:
                note = "details off"
            print("  %d gist(s) found -- %s" % (len(ids), note))
            print("")
            results.append({"label": label, "ids": ids, "details": details,
                            "note": note})

    print("Summary")
    for res in results:
        print("  %-30s %4d gist(s)  %s"
              % (res["label"], len(res["ids"]), res["note"]))
    found = sum(1 for res in results if res["ids"])
    print("")
    print("Done: %d of %d quer(y/ies) returned gists."
          % (found, len(results)))
    if not found:
        print(tokenisation_hint(terms[0]))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
