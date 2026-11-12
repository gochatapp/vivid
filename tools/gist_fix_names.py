"""Normalize gist record filenames to the canonical chatgo naming.

  python gist_fix_names.py                  # list every gist, dry run
  python gist_fix_names.py --apply          # perform the renames
  python gist_fix_names.py --gist=<id>      # only these gists (repeatable)
  python gist_fix_names.py --others         # also list gists owned by other
                                            #   accounts (from db_vivid.sqlite;
                                            #   never patched)
  python gist_fix_names.py --keep-ext       # keep the uploaded .txt/.md
  python gist_fix_names.py --keep-legacy    # leave valid mystupidchatapps_*
                                            #   and vividchatapp_* names alone
  python gist_fix_names.py --keep-case      # keep Xiao_Chen, don't lowercase
  python gist_fix_names.py --save=names.txt # write the plan to a file

Canonical names (what role_maker.html / story_maker.html / store_api.go
upload -- the record kind lives INSIDE the tag, so the tag is a single
gist-search token and "_" separates it from the name):
  GOCHATAPPROLETAG_<slug>.txt
  GOCHATAPPSTORYTAG_<slug>.md
Records still named gochatapp.github.io_role_<slug>.txt,
mystupidchatapps_role_<slug>.txt or vividchatapp_role_<slug>.txt are renamed
to the canonical form as well.
<slug> is lower-case a-z0-9 words joined by single underscores -- the same
rule as publishFilename() in role_maker.html (so Assistant_NSFW becomes
assistant_nsfw, "Role - Aiko" becomes aiko, ...).

Careful: scan_gists.py takes the role name from the filename
(rolename=raw_name in run_scan), so lowering the case of a filename also
lowers the name shown in the app. Use --keep-case when a gist file already
carries a pretty name such as vividchatapp_role_Xiao_Chen.txt.

Any gist file whose name carries the tag, or a role/story word (with or
without a prefix, in any order, any case), is a record file and gets renamed.
Everything else (thumb.md, scene_*.txt, character_*.txt, db_chatgo.json,
images) is listed but never touched.

Only the filename is PATCHed -- content is never re-sent, so a large file
cannot be truncated. Every rename is re-read from the API afterwards to
prove it happened. Gists owned by another account (VividChatFan) are
reported as FOREIGN: they need a token that can edit them.

The script securely prompts for a GitHub personal access token with gist
access each run. The token is not saved.

After --apply run:  python scan_gists.py --refresh
to refresh the local database and root JSON export. No file is copied elsewhere.
"""
import re
import sqlite3
import sys
from pathlib import Path

import requests

# Works both from vividDB/ and from a subfolder such as vividDB/tools/.
_BASE_DIR = Path(__file__).resolve().parent
if not (_BASE_DIR / "scan_gists.py").exists() \
        and (_BASE_DIR.parent / "scan_gists.py").exists():
    sys.path.insert(0, str(_BASE_DIR.parent))

from scan_gists import (DB_PATH, LEGACY_PREFIXES,  # noqa: E402
                        ROLE_TAG_PREFIX, STORY_TAG_PREFIX)

API = "https://api.github.com"
TIMEOUT = 30
RECORD_EXT = {"role": "txt", "story": "md"}
SLUG_MAX = 40
# The tag carries the kind, so one tag per kind and back again.
TAG_FOR_KIND = {"role": ROLE_TAG_PREFIX, "story": STORY_TAG_PREFIX}
KIND_FOR_TAG = {ROLE_TAG_PREFIX.lower(): "role",
                STORY_TAG_PREFIX.lower(): "story"}
# Prefixes left alone by --keep-legacy: the two pre-dotted upload names.
# gochatapp.github.io is deliberately NOT here -- it is what this tool moves
# onto the tags.
LEGACY_NAME_PREFIXES = ("mystupidchatapps", "vividchatapp")
# Prefixes seen on filenames that still carry a role/story word: the current
# tags, the dotted gochatapp.github.io prefix, the older mystupidchatapps_* /
# vividchatapp_* uploads, plus the typo'd variants below.
KNOWN_PREFIXES = (ROLE_TAG_PREFIX, STORY_TAG_PREFIX) + LEGACY_PREFIXES + (
    "gochatapp", "mydumbchatapps", "mybstuipdchatapps", "vividchatapps",
    "chatgo")
NOT_RECORD_EXT = (".json", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip",
                  ".pdf", ".mp4")
TAG_ALT = "|".join(re.escape(t) for t in (ROLE_TAG_PREFIX, STORY_TAG_PREFIX))
PREFIX_ALT = "|".join(re.escape(p) for p in LEGACY_PREFIXES)
TAG_RE = re.compile(r"^(?P<tag>%s)[\s._-]+(?P<name>.+)\.(?P<ext>[A-Za-z0-9]+)$"
                    % TAG_ALT, re.I)
CANON_RE = re.compile(r"^(?:(?:%s)[\s._-]+)?(role|story)[\s._-]+(.+)"
                      r"\.([A-Za-z0-9]+)$" % PREFIX_ALT, re.I)
LEGACY_RE = re.compile(r"^(?:%s)[\s._-]+(?:role|story)[\s._-]+.+\.(?:txt|md)$"
                       % "|".join(re.escape(p)
                                  for p in LEGACY_NAME_PREFIXES), re.I)
KIND_RE = re.compile(r"(?<![A-Za-z0-9])(role|story)s?(?![A-Za-z0-9])", re.I)
# Only a kind word at the very start of the captured name, e.g. the leftover
# of an already-prefixed name ("role_aiko" -> "aiko").
LEAD_KIND_RE = re.compile(r"^(?:role|story)s?[\s._-]+", re.I)
PREFIX_RE = re.compile(r"^(?:%s)[\s._-]+" % "|".join(
    re.escape(p) for p in sorted(KNOWN_PREFIXES, key=len, reverse=True)), re.I)


def session(token):
    s = requests.Session()
    s.headers.update({"User-Agent": "VividChat-Gist-Scanner/1.0",
                      "Accept": "application/vnd.github+json"})
    s.headers["Authorization"] = "Bearer %s" % token
    return s


def slugify(text):
    """Same rule as publishFilename() in role_maker.html."""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower())
    return slug.strip("_")[:SLUG_MAX].strip("_")


def record_name(fn):
    """(kind, raw_name_text) for a record filename, or None if not one."""
    lower = fn.lower()
    if lower.endswith(NOT_RECORD_EXT) or lower.startswith("thumb."):
        return None
    stem = fn.rsplit(".", 1)[0] if "." in fn else fn
    tagged = TAG_RE.match(fn)
    if tagged:
        kind = KIND_FOR_TAG.get(tagged.group("tag").lower())
        if kind:
            return kind, tagged.group("name")
    m = CANON_RE.match(fn)
    if m:
        return m.group(1).lower(), m.group(2)
    body = PREFIX_RE.sub("", stem)
    k = KIND_RE.search(body)
    if not k:
        return None
    return k.group(1).lower(), body[:k.start()] + " " + body[k.end():]


def make_name(raw, keep_case):
    """Filename-safe name part; lower-case slug unless --keep-case.

    Stale prefixes/kind words are stripped first: a name that was already
    published as mystupidchatapps_role_aiko must not become
    GOCHATAPPROLETAG_mystupidchatapps_aiko.
    """
    text = raw or ""
    while True:
        stripped = LEAD_KIND_RE.sub("", PREFIX_RE.sub("", text))
        if stripped == text:
            break
        text = stripped
    if keep_case:
        name = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
        return name[:SLUG_MAX].strip("_")
    return slugify(text)


def canonical(kind, raw, ext, keep_ext, keep_case):
    """GOCHATAPPROLETAG_<slug>.txt / GOCHATAPPSTORYTAG_<slug>.md."""
    name = make_name(raw, keep_case) or kind
    tag = TAG_FOR_KIND[kind]
    if keep_ext and ext in ("txt", "md"):
        return "%s_%s.%s" % (tag, name, ext)
    return "%s_%s.%s" % (tag, name, RECORD_EXT[kind])


def verdict(fn, keep_ext, keep_legacy, keep_case=False):
    """(state, target, kind); state: ok | rename | legacy | other."""
    rec = record_name(fn)
    if not rec:
        return "other", None, None
    kind, raw = rec
    ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
    target = canonical(kind, raw, ext, keep_ext, keep_case)
    if fn == target:
        return "ok", target, kind
    if keep_legacy and LEGACY_RE.match(fn):
        return "legacy", target, kind
    return "rename", target, kind


def plan(gist, keep_ext, keep_legacy, keep_case=False):
    """[[filename, state, target, note], ...] for one gist."""
    files = sorted((gist.get("files") or {}).keys())
    entries = [[fn] + list(verdict(fn, keep_ext, keep_legacy,
                                   keep_case))[0:2] + [""]
               for fn in files]
    existing = set(files)
    claimed = {}
    for e in entries:
        if e[1] != "rename":
            continue
        target = e[2]
        if target in existing:
            e[1], e[3] = "conflict", "target filename already exists"
        elif target in claimed:
            e[1], e[3] = "conflict", "same target as " + claimed[target]
        else:
            claimed[target] = e[0]
    return entries


def list_own_gists(s):
    """All gists of the authenticated account (files included)."""
    out = []
    page = 1
    while page <= 20:
        r = s.get("%s/gists?per_page=100&page=%d" % (API, page), timeout=TIMEOUT)
        if r.status_code != 200:
            print("cannot list gists: HTTP %d %s"
                  % (r.status_code, (r.text or "")[:200]))
            break
        batch = r.json() or []
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def foreign_gist_ids(own_ids):
    """(gist_id, owner) rows in the DB that we do not own (read-only)."""
    out = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        for tbl in ("roles", "stories"):
            for row in conn.execute("SELECT gist_id, owner FROM %s" % tbl):
                gid, owner = row["gist_id"], row["owner"] or "?"
                if gid and gid not in own_ids and (gid, owner) not in out:
                    out.append((gid, owner))
        conn.close()
    except sqlite3.Error as exc:
        print("warn: cannot read %s (%s)" % (DB_PATH.name, exc))
    return out


def report_gist(g, entries, owner, foreign=False):
    print("\n%s  (%s)%s" % (g.get("html_url"), owner,
                            "   [not yours - read-only]" if foreign else ""))
    desc = (g.get("description") or "").replace("\n", " ").strip()
    if desc:
        print("  %s%s" % (desc[:110], "..." if len(desc) > 110 else ""))
    pre = "  foreign " if foreign else "  "
    for fn, state, target, note in entries:
        if state == "rename":
            print("%sRENAME  %s\n%s      -> %s" % (pre, fn, pre, target))
        elif state == "conflict":
            print("%sSKIP    %s  (%s)" % (pre, fn, note))
        elif state == "ok":
            print("%sok      %s" % (pre, fn))
        elif state == "legacy":
            print("%slegacy  %s  (left alone: --keep-legacy)" % (pre, fn))
        else:
            print("%sother   %s" % (pre, fn))


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    apply_ = "--apply" in argv
    keep_ext = "--keep-ext" in argv
    keep_legacy = "--keep-legacy" in argv
    keep_case = "--keep-case" in argv
    want_others = "--others" in argv
    save_to = None
    wanted = []
    for a in argv:
        if a.startswith("--gist="):
            wanted.append(a.split("=", 1)[1].strip().lower())
        elif a.startswith("--save="):
            save_to = a.split("=", 1)[1].strip()

    if not sys.stdin.isatty():
        print("Cannot prompt for a gist token without an interactive terminal.")
        return 1
    token = input("GitHub personal access token with gist access: ").strip()
    if not token:
        print("No token entered.")
        return 1

    s = session(token)
    me = "?"
    r = s.get("%s/user" % API, timeout=TIMEOUT)
    if r.status_code == 200:
        me = (r.json() or {}).get("login") or "?"
    print("Gist account: %s   (token entered interactively)" % me)
    print("Canonical:    %s_<slug>.txt  /  %s_<slug>.md"
          % (ROLE_TAG_PREFIX, STORY_TAG_PREFIX))

    if wanted:
        targets = []
        for gid in wanted:
            r = s.get("%s/gists/%s" % (API, gid), timeout=TIMEOUT)
            if r.status_code != 200:
                print("  SKIP %s: HTTP %d" % (gid, r.status_code))
                continue
            targets.append(r.json())
    else:
        targets = list_own_gists(s)
    own_ids = {g.get("id") for g in targets}
    print("Listed %d gist(s) you own." % len(targets))

    planned = []
    ok_files = other_files = 0
    for g in targets:
        entries = plan(g, keep_ext, keep_legacy, keep_case)
        owner = (g.get("owner") or {}).get("login", me)
        report_gist(g, entries, owner)
        for fn, state, target, _note in entries:
            if state == "ok":
                ok_files += 1
            elif state == "other":
                other_files += 1
            elif state == "rename":
                planned.append((g, fn, target))

    foreign = []
    if want_others:
        for gid, owner in foreign_gist_ids(own_ids):
            r = s.get("%s/gists/%s" % (API, gid), timeout=TIMEOUT)
            if r.status_code != 200:
                print("\n%s  (%s): HTTP %d -- deleted or not visible"
                      % (gid, owner, r.status_code))
                continue
            g = r.json()
            entries = plan(g, keep_ext, keep_legacy, keep_case)
            who = (g.get("owner") or {}).get("login", owner)
            report_gist(g, entries, who, foreign=True)
            pending = [e for e in entries if e[1] == "rename"]
            if pending:
                foreign.append((g, len(pending)))

    if foreign:
        print("\n%d gist(s) belong to another account and need a token that "
              "can edit them:" % len(foreign))
        for g, n in foreign:
            print("  %s  (%d file(s) not canonical)" % (g.get("html_url"), n))

    print("\nSummary: %d canonical file(s), %d ignored file(s), %d rename(s)."
          % (ok_files, other_files, len(planned)))
    lines = ["%s -> %s" % (fn, target) for _g, fn, target in planned]
    if save_to:
        with open(save_to, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("Plan written to %s (%d line(s))." % (save_to, len(lines)))
    if not planned:
        print("Nothing to do -- every role/story filename is canonical.")
        return 0
    if not apply_:
        print("Dry run. Re-run with --apply to rename the file(s) above.")
        return 0

    done = failures = 0
    for g, fn, target in planned:
        gid = g.get("id")
        patch = {"files": {fn: {"filename": target}}}
        r = s.patch("%s/gists/%s" % (API, gid), json=patch, timeout=TIMEOUT)
        if r.status_code not in (200, 201):
            print("  FAILED %s: HTTP %d %s"
                  % (fn, r.status_code, (r.text or "")[:200]))
            failures += 1
            continue
        after = s.get("%s/gists/%s" % (API, gid), timeout=TIMEOUT)
        names = set((after.json().get("files") or {}).keys()) \
            if after.status_code == 200 else set()
        if target in names and fn not in names:
            print("  renamed %s\n       -> %s" % (fn, target))
            done += 1
        else:
            print("  FAILED %s: rename did not take effect (files: %s)"
                  % (fn, sorted(names)))
            failures += 1

    print("\nDone: %d renamed, %d failed." % (done, failures))
    if done:
        print("Now run:  python scan_gists.py --refresh"
              "   (refreshes the local database and JSON export; no copy)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
