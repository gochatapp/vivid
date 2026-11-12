"""Diagnose why a gist URL can't be read by scan_gists.py. Usage: python scan_diagnose.py <gist-url>"""
import os
import re
import sys
from urllib.parse import quote

import requests

# Keep in sync with scan_gists.py (ROLE_TAG_PREFIX / STORY_TAG_PREFIX /
# LEGACY_PREFIXES / SEARCH_TERMS / DEFAULT_SEARCH_OWNERS): current uploads use
# the GOCHATAPPROLETAG_* / GOCHATAPPSTORYTAG_* tags; gists published earlier
# still use gochatapp.github.io_*, mystupidchatapps_* or vividchatapp_*
# (incl. VividChatFan's) -- those are recognised but SKIPPED by the scanner
# and reported in scan_skipped.txt instead of being imported.
PREFIXES = ("GOCHATAPPROLETAG", "GOCHATAPPSTORYTAG", "gochatapp.github.io",
            "mystupidchatapps", "vividchatapp")
TAG_PREFIXES = PREFIXES[:2]
LEGACY_PREFIXES = PREFIXES[2:]
# Filename search only works for the tags: gist search tokenises on dots and
# underscores, so filename:gochatapp.github.io returns unrelated *.github.io
# gists. Legacy uploads are found with user:<login> queries instead.
SEARCH_TERMS = ("GOCHATAPPROLETAG", "GOCHATAPPSTORYTAG")
DEFAULT_SEARCH_OWNERS = ("gochatapp",)
SEARCH_URLS = tuple("https://gist.github.com/search?q="
                    + quote("filename:" + p) for p in SEARCH_TERMS)
OWNER_SEARCH_URLS = tuple("https://gist.github.com/search?q="
                          + quote("user:" + p) for p in DEFAULT_SEARCH_OWNERS)
HEADERS = {"User-Agent": "VividChat-Gist-Scanner/1.0",
           "Accept": "application/vnd.github+json"}
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
TOKEN_SOURCE = "GH_TOKEN/GITHUB_TOKEN" if TOKEN else None
if not TOKEN:  # same fallback chain as scan_gists.py (yaml config files)
    try:
        from scan_gists import resolve_token
        TOKEN, TOKEN_SOURCE = resolve_token()
    except ImportError:
        pass

_PREFIX_ALT = "|".join(re.escape(p) for p in LEGACY_PREFIXES)
# Current uploads carry the kind inside the tag (GOCHATAPPROLETAG_<name>.txt),
# so each regex accepts only its own tag; legacy uploads keep their
# "<prefix>_role_<name>" shape.
ROLE_RE = re.compile(r"^(?:(?:%s)[\s._-]+(?:roles?[\s._-]+)?"
                     r"|(?:%s)[\s._-]+roles?[\s._-]+)(.+)\.(txt|md)$"
                     % (re.escape(TAG_PREFIXES[0]), _PREFIX_ALT), re.I)
STORY_RE = re.compile(r"^(?:(?:%s)[\s._-]+(?:stor(?:y|ies)[\s._-]+)?"
                      r"|(?:%s)[\s._-]+stor(?:y|ies)[\s._-]+)(.+)\.(txt|md)$"
                      % (re.escape(TAG_PREFIXES[1]), _PREFIX_ALT), re.I)
SCENE_RE = re.compile(r"^scene_(.+)\.(txt|md)$", re.I)
CHAR_RE = re.compile(r"^character_(.+)\.(txt|md)$", re.I)
THUMB_RE = re.compile(r"^thumb\.(txt|md)$", re.I)
# Same test scan_gists.legacy_prefix_of() uses: a legacy-prefixed master is
# discovered and then skipped, so the diagnosis has to say so.
LEGACY_RE = re.compile(r"^(%s)[\s._-]" % _PREFIX_ALT, re.I)


def legacy_prefix_of(filename):
    m = LEGACY_RE.match(filename or "")
    return m.group(1) if m else None


def extract_gist_id(url):
    m = re.search(r"/([0-9a-f]{20,})(?=[/?#]|$)", url, re.I)
    return m.group(1).lower() if m else None


def check(url):
    print(f"URL: {url}")
    gid = extract_gist_id(url)
    if not gid:
        print("FAIL: no 20+ hex gist id found in URL.")
        print("  Expected like: https://gist.github.com/<user>/<32-hex-id>")
        return
    print(f"Gist id: {gid}")

    s = requests.Session()
    s.headers.update(HEADERS)
    ah = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    print(f"Auth: {'token set (%s)' % TOKEN_SOURCE if TOKEN else 'NO token -- private/secret gists will 404'}")

    # 1. API fetch (same call scan_gists.py makes)
    print("\n[1] API fetch: GET https://api.github.com/gists/<id>")
    try:
        r = s.get(f"https://api.github.com/gists/{gid}", timeout=30, headers=ah)
    except requests.RequestException as e:
        print(f"FAIL: network error: {e}")
        return
    print(f"  status: {r.status_code}")
    if r.status_code == 404:
        print("FAIL: API returns 404. Causes in order of likelihood:")
        print("  a) Gist was DELETED (owner deleted it).")
        print("  b) Gist id typo'd / URL truncated.")
        print("  c) Gist is SECRET and you are not authenticated as someone")
        print("     with access (secret gists are invisible without a token")
        print("     that can see them; unauthenticated API always 404s them).")
        print("  d) Gist is PRIVATE to another account/org.")
        print("  Fix: open the URL in a browser logged in as the owner.")
        print("  If the browser also 404s -> deleted. If browser works but API")
        print("  404s -> secret/private: put the PAT in gist_token.txt (or")
        print("  set GH_TOKEN) and re-run.")
        # still check HTML page for extra signal
    elif r.status_code == 403 and "rate limit" in r.text.lower():
        print("FAIL: rate limited (403). Add a token (gist_token.txt / "
              "GH_TOKEN) or wait. Body:")
        print("  " + r.text[:300])
        return
    elif r.status_code != 200:
        print(f"FAIL: unexpected status. Body: {r.text[:500]}")
        return
    else:
        g = r.json()
        print(f"  OK: owner={(g.get('owner') or {}).get('login')}, "
              f"public={g.get('public')}, desc={g.get('description')!r}")
        files = g.get("files", {}) or {}
        print(f"  files ({len(files)}): {sorted(files)}")
        role = [f for f in files if ROLE_RE.match(f)]
        story = [f for f in files if STORY_RE.match(f)]
        thumb = [f for f in files if THUMB_RE.match(f)]
        print(f"  role master files: {role or 'NONE'}")
        print(f"  story master files: {story or 'NONE'}")
        print(f"  thumb files: {thumb or 'NONE'}")
        legacy = [f for f in role + story if legacy_prefix_of(f)]
        if legacy:
            print("SKIP: scan_gists.py would SKIP this gist -- its master file "
                  "uses a\n  legacy prefix (%s), so it is logged to "
                  "scan_skipped.txt and NOT\n  imported. Fix: rename the file "
                  "to %s_<name>.txt / %s_<name>.md\n  (python "
                  "tools/fix_gist_prefix.py --apply) or by hand, then rescan."
                  % (", ".join(sorted({legacy_prefix_of(f) for f in legacy})),
                     PREFIXES[0], PREFIXES[1]))
            print("  files: %s" % ", ".join(sorted(legacy)))
        elif not role and not story:
            print("FAIL: scan_gists.py would SKIP with 'no role/story master "
                  "file'.\n  Fix: add a file named %s_<name>.txt\n  or "
                  "%s_<name>.md (case-insensitive).\n  Legacy "
                  "gochatapp.github.io_*/mystupidchatapps_*/vividchatapp_* "
                  "names are NOT imported any more, so the file must use the "
                  "tag.\n  Note: the tag is also what makes the "
                  "gist discoverable by filename search."
                  % (PREFIXES[0], PREFIXES[1]))
        else:
            print("OK: scan_gists.py would ACCEPT this gist (role/story found).")
            if gid not in hint_search(s, (g.get("owner") or {}).get("login")):
                print("NOTE: gist not found by the discovery searches\n"
                      "  (filename:<tag> + user:<owner>), so scan_gists.py\n"
                      "  would never see it (search-index lag, or it belongs "
                      "to an\n  account that is not crawled: add it with "
                      "--owners=<login>).")

    # 2. HTML page fetch (browser view)
    print("\n[2] HTML page fetch (same URL, browser headers)")
    try:
        # The session sends Accept: application/vnd.github+json (needed for
        # the API), which makes github.com answer 404 for gist HTML pages,
        # so override it here to keep this check honest.
        h = s.get(url, timeout=30,
                  headers={"User-Agent": "Mozilla/5.0",
                           "Accept": "text/html,application/xhtml+xml"})
        print(f"  status: {h.status_code}")
        if h.status_code == 404:
            print("  page also 404s -> gist deleted or never existed / secret.")
        elif h.status_code == 200:
            print("  page loads -> gist EXISTS; API 404 means auth issue (secret).")
    except requests.RequestException as e:
        print(f"  error: {e}")


def hint_search(s, owner=None):
    gids = set()
    gid_re = r'href="/[^/"?#]+/([0-9a-f]{20,})(?=[/?#"]|$)'
    urls = list(SEARCH_URLS) + list(OWNER_SEARCH_URLS)
    if owner:
        urls.append("https://gist.github.com/search?q=" + quote("user:" + owner))
    for url in urls:
        try:
            r = s.get(url, timeout=30)
            gids |= set(re.findall(gid_re, r.text, re.I))
        except requests.RequestException:
            pass
    return gids


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scan_diagnose.py <gist-url>")
        sys.exit(1)
    check(sys.argv[1].strip())
