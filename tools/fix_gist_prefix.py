"""Rename gist files that were uploaded with an old filename prefix.

  python fix_gist_prefix.py                    # dry run, your own gists
  python fix_gist_prefix.py --apply            # perform the renames
  python fix_gist_prefix.py --gist=<id>        # only this gist (repeatable)
  python fix_gist_prefix.py --from=X           # other source prefix
  python fix_gist_prefix.py --to=Y             # force one target prefix

Default: the dotted gochatapp.github.io_* names move onto the searchable
tags, chosen per file from its kind (the kind lives inside the tag, because
gist search breaks tokens on "." and "_"):
  gochatapp.github.io_role_<name>.txt   ->  GOCHATAPPROLETAG_<name>.txt
  gochatapp.github.io_story_<name>.md   ->  GOCHATAPPSTORYTAG_<name>.md
--to=<prefix> forces one target for every renamed file instead. Files that
are not role/story masters (thumb.md, scene_*.txt, character_*.txt, images)
keep their names.

Gists are found through the API (GET /gists), not the filename search:
search indexes only some of the typo'd files, while /gists lists the
authenticated account's gists with their exact filenames in one request.
Gists owned by another account (e.g. VividChatFan) need --gist=<id> and
a token that can edit them.

Rename case only -- run "python scan_gists.py --refresh" afterwards so the
DB picks up the new raw URLs. Use tools/gist_fix_names.py when the name part
also needs normalising.

Token: GH_TOKEN / GITHUB_TOKEN, else gist_token.txt, else
gist_local.yaml (github.token), else scan_gist.yaml (gisttoken) -- the
same resolution scan_gists.py uses.
"""
import re
import sys
from pathlib import Path

import requests

# Works both from the repo root and from a subfolder such as tools/.
_BASE_DIR = Path(__file__).resolve().parent
if not (_BASE_DIR / "scan_gists.py").exists() \
        and (_BASE_DIR.parent / "scan_gists.py").exists():
    sys.path.insert(0, str(_BASE_DIR.parent))

from scan_gists import (ROLE_TAG_PREFIX, STORY_TAG_PREFIX,  # noqa: E402
                        TOKEN, TOKEN_SOURCE)

API = "https://api.github.com"
TIMEOUT = 30
# The kind word that follows the old prefix, e.g. gochatapp.github.io_role_x.
KIND_AFTER_RE = re.compile(r"^(roles?|stor(?:y|ies))[\s._-]+", re.I)
TAG_FOR_KIND = {"role": ROLE_TAG_PREFIX, "story": STORY_TAG_PREFIX}


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": "VividChat-Gist-Scanner/1.0",
                      "Accept": "application/vnd.github+json",
                      "Authorization": "Bearer %s" % TOKEN})
    return s


def list_own_gists(s):
    """Yield every gist of the authenticated account (files included)."""
    page = 1
    while True:
        r = s.get(f"{API}/gists?per_page=100&page={page}", timeout=TIMEOUT)
        if r.status_code != 200:
            print("  cannot list gists: HTTP %d %s"
                  % (r.status_code, (r.text or "")[:200]))
            return
        batch = r.json() or []
        for g in batch:
            yield g
        if len(batch) < 100:
            return
        page += 1
        if page > 20:
            return


def plan_for(gist, old, new=None):
    """Return [(old_filename, new_filename)] for one gist.

    old is the prefix to move away from, new an optional fixed target. With
    new=None the kind word after the old prefix picks the tag, so one source
    prefix can map onto two targets:
      <old>_role_<name>.txt  -> GOCHATAPPROLETAG_<name>.txt
      <old>_story_<name>.md  -> GOCHATAPPSTORYTAG_<name>.md
    """
    out = []
    for fn in (gist.get("files") or {}):
        if not fn.lower().startswith(old.lower() + "_"):
            continue
        rest = fn[len(old) + 1:]
        if new:
            out.append((fn, new + "_" + rest))
            continue
        kind = KIND_AFTER_RE.match(rest)
        if not kind:
            continue
        tag = TAG_FOR_KIND["story" if kind.group(1).lower().startswith("stor")
                           else "role"]
        out.append((fn, tag + "_" + rest[kind.end():]))
    return out


def main():
    apply = "--apply" in sys.argv
    old = "gochatapp.github.io"
    new = ""
    wanted = []
    for a in sys.argv[1:]:
        if a.startswith("--from="):
            old = a.split("=", 1)[1].strip()
        elif a.startswith("--to="):
            new = a.split("=", 1)[1].strip()
        elif a.startswith("--gist="):
            wanted.append(a.split("=", 1)[1].strip().lower())
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return
    if not old or old == new:
        print("bad prefixes: --from=%r --to=%r" % (old, new))
        return
    if not TOKEN:
        print("No gist token. Set GH_TOKEN, or put a PAT in "
              "gist_token.txt (preferred), gist_local.yaml or "
              "scan_gist.yaml.")
        return

    if new:
        print("Rename %s_* -> %s_*  (token from %s)"
              % (old, new, TOKEN_SOURCE))
    else:
        print("Rename %s_* -> %s_* / %s_*  (token from %s)"
              % (old, ROLE_TAG_PREFIX, STORY_TAG_PREFIX, TOKEN_SOURCE))
    s = session()

    targets = []
    if wanted:
        for gid in wanted:
            r = s.get(f"{API}/gists/{gid}", timeout=TIMEOUT)
            if r.status_code != 200:
                owner = "?"
                print("  SKIP %s: HTTP %d -- needs a token that can edit it"
                      % (gid, r.status_code))
                continue
            targets.append(r.json())
    else:
        targets = list(list_own_gists(s))
    print("Checked %d gist(s)" % len(targets))

    changes = []
    for g in targets:
        pairs = plan_for(g, old, new or None)
        if pairs:
            changes.append((g, pairs))

    if not changes:
        print("Nothing to rename -- no %s_* role/story files left." % old)
        return

    for g, pairs in changes:
        owner = (g.get("owner") or {}).get("login", "?")
        print("\n%s  (%s)" % (g.get("html_url"), owner))
        for a, b in pairs:
            print("  %s -> %s" % (a, b))

    if not apply:
        print("\nDry run: %d gist(s) would change. Re-run with --apply."
              % len(changes))
        return

    failed = 0
    for g, pairs in changes:
        gid = g.get("id")
        patch = {"files": {a: {"filename": b} for a, b in pairs}}
        r = s.patch(f"{API}/gists/{gid}", json=patch, timeout=TIMEOUT)
        if r.status_code not in (200, 201):
            print("  FAILED %s: HTTP %d %s"
                  % (gid, r.status_code, (r.text or "")[:200]))
            failed += 1
            continue
        after = set((r.json().get("files") or {}).keys())
        missing = [b for _, b in pairs if b not in after]
        if missing:
            print("  PARTIAL %s: missing after rename: %s" % (gid, missing))
            failed += 1
        else:
            print("  renamed %d file(s) in %s" % (len(pairs), gid))
    print("\nDone: %d gist(s) updated, %d failed" % (len(changes) - failed,
                                                     failed))
    if failed:
        print("Re-run the scan afterwards so the DB picks up the new raw URLs.")


if __name__ == "__main__":
    main()
