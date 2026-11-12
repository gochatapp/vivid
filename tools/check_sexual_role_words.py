"""Find common sexual terms in role gists without changing any gists.

Usage:
  python tools/check_sexual_role_words.py
  python tools/check_sexual_role_words.py --owners=gochatapp,another-owner
  python tools/check_sexual_role_words.py --db=path\\to\\db_vivid.sqlite
  python tools/check_sexual_role_words.py --output=tools\\sexual_words.txt

Discovery follows scan_gists.py: role/story filename tags plus all gists from
the default and database-known owners. Only files recognized as role files
are scanned; story files are ignored.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scan_gists import (  # noqa: E402
    DB_PATH,
    DEFAULT_SEARCH_OWNERS,
    PAGE_PAUSE,
    ROLE_RE,
    api_get_json,
    collect_search_gids,
    resolve_token,
    search_owners,
)


SEXUAL_TERMS = (
    "sex", "sexual", "sexually", "sexy", "erotic", "porn", "pornography",
    "pornographic", "nude", "nudes", "nudity", "naked", "orgasm",
    "orgasms", "intercourse", "masturbate", "masturbates", "masturbated",
    "masturbating", "masturbation", "genital", "genitals", "penis", "penises",
    "vagina", "vaginal", "vulva", "clitoris", "clitoral", "ejaculate",
    "ejaculation", "semen", "fellatio", "cunnilingus", "blowjob",
    "handjob", "fuck", "fucks", "fucked", "fucking", "pussy", "pussies",
    "cunt", "cunts", "cum", "cums", "cummed", "cumming", "stud", "cock",
    "cocks", "dick", "dicks", "tits", "titties", "boobs", "breasts",
    "horny", "aroused", "foreplay", "penetration", "penetrate", "penetrated",
)
TERM_RE = re.compile(
    r"(?<!\w)(?:%s)(?!\w)" % "|".join(
        re.escape(term) for term in sorted(SEXUAL_TERMS, key=len, reverse=True)
    ),
    re.IGNORECASE,
)
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "VividChat-Sexual-Role-Word-Checker/1.0",
}


def matching_lines(text):
    """Return {matched term: [1-based line numbers]} for one text."""
    found = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in TERM_RE.finditer(line):
            term = match.group(0).lower()
            found.setdefault(term, set()).add(line_number)
    return {term: sorted(lines) for term, lines in found.items()}


def read_role_file(session, file_data, auth_headers):
    """Return a gist file's content, falling back to its raw URL if needed."""
    if file_data.get("content") and not file_data.get("truncated"):
        return file_data["content"]

    raw_url = file_data.get("raw_url")
    if not raw_url:
        raise RuntimeError("role file has no content or raw URL")
    response = session.get(raw_url, timeout=30, headers=auth_headers)
    response.raise_for_status()
    return response.text or ""


def parse_tags(value):
    """Normalize DB/JSON tags to a list of strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value] if value.strip() else []
    if isinstance(value, list):
        return [tag for tag in value if isinstance(tag, str)]
    return []


def load_rating_indexes(db_path, json_path):
    """Load role tags from SQLite and the JSON export, keyed by gist ID."""
    indexes = {"SQLite": {}, "JSON": {}}
    errors = []

    if db_path.is_file():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                rows = conn.execute(
                    "SELECT gist_id, tags FROM roles"
                ).fetchall()
            indexes["SQLite"] = {
                gist_id: parse_tags(tags)
                for gist_id, tags in rows if gist_id
            }
        except sqlite3.Error as exc:
            errors.append("Could not read SQLite ratings from %s: %s"
                          % (db_path, exc))
    else:
        errors.append("SQLite ratings unavailable; file not found: %s" % db_path)

    if json_path.is_file():
        try:
            with json_path.open(encoding="utf-8") as source:
                data = json.load(source)
            for role in data.get("roles", []):
                if isinstance(role, dict) and role.get("gist_id"):
                    indexes["JSON"][role["gist_id"]] = parse_tags(
                        role.get("tags")
                    )
        except (OSError, ValueError, TypeError) as exc:
            errors.append("Could not read JSON ratings from %s: %s"
                          % (json_path, exc))
    else:
        errors.append("JSON ratings unavailable; file not found: %s" % json_path)
    return indexes, errors


def rating_status(gist_id, indexes):
    """Return NSFW label and per-source rating labels for a gist."""
    labels = {}
    for source, index in indexes.items():
        if gist_id not in index:
            labels[source] = "not found"
            continue
        tags = index[gist_id]
        rating = next(
            (tag for tag in tags
             if tag.casefold() in ("nsfw", "sfw", "mature", "18+")),
            None,
        )
        labels[source] = rating or "no rating tag"

    ratings = [value.casefold() for value in labels.values()]
    if "nsfw" in ratings and any(
        rating not in ("nsfw", "not found", "no rating tag")
        for rating in ratings
    ):
        overall = "MIXED"
    elif "nsfw" in ratings:
        overall = "YES"
    elif any(rating not in ("not found", "no rating tag") for rating in ratings):
        overall = "NO"
    else:
        overall = "UNKNOWN"
    return overall, labels


def main():
    parser = argparse.ArgumentParser(
        description="Read role gists and report common sexual terms in their text."
    )
    parser.add_argument(
        "--owners",
        help="comma-separated additional GitHub gist owners to scan",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="SQLite database used for owner discovery and NSFW tag comparison "
             "(default: db_vivid.sqlite)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=BASE_DIR / "db_chatgo.json",
        help="JSON export used for NSFW tag comparison (default: db_chatgo.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "tools" / "sexual_role_word_report.txt",
        help="text report path (default: tools/sexual_role_word_report.txt)",
    )
    args = parser.parse_args()

    extra_owners = (
        [owner.strip() for owner in args.owners.split(",") if owner.strip()]
        if args.owners else []
    )
    owners = list(DEFAULT_SEARCH_OWNERS)
    if args.db.is_file():
        with sqlite3.connect(str(args.db)) as conn:
            owners = search_owners(conn, extra_owners)
    else:
        owners.extend(extra_owners)

    token, _source = resolve_token()
    request_headers = dict(API_HEADERS)
    if token:
        request_headers["Authorization"] = "token %s" % token
    if not token:
        print("Note: no GitHub token found; only publicly accessible gists can be read.")

    rating_indexes, rating_errors = load_rating_indexes(args.db, args.json)
    report = [
        "Sexual-word scan of role gists",
        "Generated: %s" % datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "NSFW tags compared from SQLite: %s" % args.db,
        "NSFW tags compared from JSON: %s" % args.json,
        "",
    ]
    report.extend("WARNING: " + error for error in rating_errors)
    if rating_errors:
        report.append("")

    with requests.Session() as session:
        print("Discovering role gists for: %s" % ", ".join(owners))
        gist_ids, _updated = collect_search_gids(session, owners)
        report.append("Owners scanned: %s" % ", ".join(owners))
        report.append("Discovered gists: %d" % len(gist_ids))
        report.append("")
        print("Checking role files in %d discovered gist(s)." % len(gist_ids))

        checked = 0
        flagged = 0
        errors = 0
        matches_found = 0
        for index, gist_id in enumerate(gist_ids):
            if index:
                time.sleep(PAGE_PAUSE)
            data, error = api_get_json(
                session,
                "https://api.github.com/gists/" + gist_id,
                request_headers,
            )
            if error:
                errors += 1
                print("ERROR: could not read gist %s: %s" % (gist_id, error))
                report.append("ERROR: https://gist.github.com/%s: %s"
                              % (gist_id, error))
                continue

            role_files = [
                (name, details)
                for name, details in (data.get("files") or {}).items()
                if ROLE_RE.match(name)
            ]
            if not role_files:
                continue

            checked += 1
            owner = (data.get("owner") or {}).get("login", "unknown")
            gist_url = data.get("html_url") or (
                "https://gist.github.com/" + gist_id
            )
            print("%s (%s)" % (gist_url, owner))

            gist_report = []
            gist_matches = 0
            description_matches = matching_lines(
                data.get("description") or ""
            )
            if description_matches:
                gist_matches += 1
                details = ", ".join(
                    "%s (line%s %s)" % (
                        term,
                        "" if len(lines) == 1 else "s",
                        ", ".join(str(line) for line in lines),
                    )
                    for term, lines in sorted(description_matches.items())
                )
                print("  gist description: %s" % details)
                gist_report.append("  gist description: " + details)
            for filename, file_data in role_files:
                try:
                    contents = read_role_file(
                        session, file_data, request_headers
                    )
                except requests.RequestException as exc:
                    errors += 1
                    print("  ERROR: could not read %s: %s" % (filename, exc))
                    gist_report.append(
                        "  ERROR: could not read %s: %s" % (filename, exc)
                    )
                    continue
                except RuntimeError as exc:
                    errors += 1
                    print("  ERROR: %s: %s" % (filename, exc))
                    gist_report.append("  ERROR: %s: %s" % (filename, exc))
                    continue
                file_matches = matching_lines(contents)
                if file_matches:
                    gist_matches += 1
                    details = ", ".join(
                        "%s (line%s %s)" % (
                            term,
                            "" if len(lines) == 1 else "s",
                            ", ".join(str(line) for line in lines),
                        )
                        for term, lines in sorted(file_matches.items())
                    )
                    print("  %s: %s" % (filename, details))
                    gist_report.append("  %s: %s" % (filename, details))

            if gist_matches:
                flagged += gist_matches
                matches_found += 1
                nsfw, ratings = rating_status(gist_id, rating_indexes)
                report.extend([
                    "Gist: %s" % gist_url,
                    "Owner: %s" % owner,
                    "NSFW tag: %s (SQLite: %s; JSON: %s)" % (
                        nsfw, ratings["SQLite"], ratings["JSON"]
                    ),
                ])
                report.extend(gist_report)
                report.append("")

    report.extend([
        "Summary: checked %d role gist(s); %d gist(s) had matching terms; "
        "%d matching file/description(s); %d read error(s)." %
        (checked, matches_found, flagged, errors),
        "",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(report), encoding="utf-8")
    print(
        "Done: checked %d role gist(s); %d gist(s) had matching terms; "
        "%d matching file/description(s); %d read error(s)." %
        (checked, matches_found, flagged, errors)
    )
    print("Report written to %s" % args.output)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
