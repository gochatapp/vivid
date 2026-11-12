#!/usr/bin/env python3
"""Remove image.md files from GitHub gists.

Some gists have an unwanted image.md file that was created by a bug in the
saving code. This tool finds and removes those files.

Usage:
  python tools/remove_image_md.py                 # list gists with image.md (dry run)
  python tools/remove_image_md.py --apply         # actually remove image.md files
  python tools/remove_image_md.py --gist=ID       # only process specific gist(s)
  python tools/remove_image_md.py --save=plan.txt # write the plan to a file

The GitHub PAT is read from gist_token.txt or GH_TOKEN environment variable.
"""
import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

API = "https://api.github.com"
TIMEOUT = 30
TARGET_FILENAME = "image.md"
TOKEN_PATH = BASE_DIR / "gist_token.txt"


def load_token() -> str:
    """Prompt user for GitHub PAT interactively."""
    if not sys.stdin.isatty():
        # Fall back to env var or token file for non-interactive use
        token = os.environ.get("GH_TOKEN")
        if token:
            return token.strip()
        if TOKEN_PATH.exists():
            return TOKEN_PATH.read_text(encoding="utf-8").strip()
        raise RuntimeError(
            "Cannot prompt for a gist token without an interactive terminal. "
            "Set GH_TOKEN or create gist_token.txt."
        )
    token = input("GitHub personal access token with gist access: ").strip()
    if not token:
        raise RuntimeError("No token entered.")
    return token


def create_session(token: str) -> requests.Session:
    """Create authenticated requests session."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "VividChat-RemoveImageMD/1.0",
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
    })
    return session


def list_own_gists(session: requests.Session) -> list:
    """List all gists owned by the authenticated user."""
    gists = []
    page = 1
    while True:
        print(f"Listing gists (page {page})...", flush=True)
        response = session.get(
            f"{API}/gists?per_page=100&page={page}", timeout=TIMEOUT
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected gist listing.")
        gists.extend(batch)
        print(f"  received {len(batch)} gist(s), {len(gists)} total.", flush=True)
        if len(batch) < 100:
            return gists
        page += 1


def fetch_gist(session: requests.Session, gist_id: str) -> dict:
    """Fetch a single gist by ID."""
    response = session.get(f"{API}/gists/{gist_id}", timeout=TIMEOUT)
    if response.status_code == 404:
        raise RuntimeError(f"Gist {gist_id} not found.")
    response.raise_for_status()
    return response.json()


def has_image_md(gist: dict) -> bool:
    """Check if gist has an image.md file."""
    files = gist.get("files", {})
    return TARGET_FILENAME in files


def remove_image_md(session: requests.Session, gist_id: str) -> bool:
    """Remove image.md from a gist using PATCH.
    Returns True on success, False on failure."""
    # To delete a file in a gist, set its value to null in the files object
    patch = {"files": {TARGET_FILENAME: None}}
    response = session.patch(f"{API}/gists/{gist_id}", json=patch, timeout=TIMEOUT)
    if response.status_code not in (200, 201):
        print(f"  FAILED: HTTP {response.status_code} {response.text[:200]}")
        return False
    # Verify the file was removed
    verify = session.get(f"{API}/gists/{gist_id}", timeout=TIMEOUT)
    if verify.status_code == 200:
        files = verify.json().get("files", {})
        if TARGET_FILENAME not in files:
            return True
    print(f"  FAILED: removal did not take effect")
    return False


def gist_id_from_input(value: str) -> str:
    """Extract gist ID from a URL or return the ID directly."""
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{20,}", value):
        return value.lower()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.hostname != "gist.github.com":
        raise ValueError("Enter a gist.github.com URL or leave it blank.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] != "":
        # gist.github.com/owner/id
        return parts[1].lower()
    if len(parts) == 1:
        # gist.github.com/id
        return parts[0].lower()
    raise ValueError("Could not parse gist ID from URL.")




def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove image.md files from GitHub gists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually remove image.md files"
    )
    parser.add_argument(
        "--gist",
        action="append",
        default=[],
        help="Process only this gist ID or URL (repeatable)",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Write the plan to a file",
    )
    args = parser.parse_args()

    try:
        token = load_token()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    session = create_session(token)

    # Verify authentication
    try:
        user_response = session.get(f"{API}/user", timeout=TIMEOUT)
        user_response.raise_for_status()
        username = user_response.json().get("login")
        if not username:
            raise RuntimeError("GitHub did not identify the authenticated account.")
        print(f"Gist account: {username}", flush=True)
    except requests.RequestException as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        return 1

    # Get target gists
    if args.gist:
        gists = []
        requested = {gist_id_from_input(g).lower() for g in args.gist}
        for gid in requested:
            try:
                gist = fetch_gist(session, gid)
                # Verify ownership
                owner = (gist.get("owner") or {}).get("login", "")
                if owner != username:
                    print(f"  SKIP {gid}: owned by {owner}, not {username}")
                    continue
                gists.append(gist)
            except RuntimeError as e:
                print(f"  SKIP {gid}: {e}")
        print(f"Found {len(gists)} owned gist(s) to process.", flush=True)
    else:
        all_gists = list_own_gists(session)
        gists = [g for g in all_gists if has_image_md(g)]
        print(f"Found {len(gists)} gist(s) with {TARGET_FILENAME} out of {len(all_gists)} total.", flush=True)

    if not gists:
        print(f"No gists with {TARGET_FILENAME} found.")
        return 0

    # Plan
    plan_lines = []
    for gist in gists:
        gid = gist.get("id")
        url = gist.get("html_url") or gid
        plan_lines.append(f"{gid}  {url}")
        print(f"  {gid}  {url}")

    if args.save:
        Path(args.save).write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
        print(f"Plan written to {args.save} ({len(plan_lines)} line(s)).")

    if not args.apply:
        print("\nDry run. Re-run with --apply to remove the image.md files.")
        return 0

    # Apply
    print("\nRemoving image.md files...")
    done = failures = 0
    for gist in gists:
        gid = gist.get("id")
        url = gist.get("html_url") or gid
        print(f"  Processing {gid}...", flush=True)
        if remove_image_md(session, gid):
            print(f"    removed {TARGET_FILENAME} from {url}")
            done += 1
        else:
            failures += 1

    print(f"\nDone: {done} removed, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
