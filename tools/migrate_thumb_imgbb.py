#!/usr/bin/env python3
"""Migrate gist thumb.md images from GitHub user-attachments to ImgBB.

GitHub stopped allowing hot linking of gist user-attachment images, so
thumb.md files like

    <img width="450" height="450" alt="image"
         src="https://gist.github.com/user-attachments/assets/<uuid>" />

no longer display outside GitHub. This tool walks every gist owned by
the authenticated account, finds thumb.md files whose image URL is a
GitHub user-attachment URL, downloads the image bytes (with the PAT --
unauthenticated fetches return HTML, not image data), uploads them to
ImgBB (https://imgbb.com/), and rewrites thumb.md to point at the
ImgBB direct URL, preserving the original <img>/markdown/bare format.

Usage:
  python tools/migrate_thumb_imgbb.py                 # dry run, plan only
  python tools/migrate_thumb_imgbb.py --apply         # download/upload/patch
  python tools/migrate_thumb_imgbb.py --gist=ID       # only this gist (repeatable)
  python tools/migrate_thumb_imgbb.py --save=plan.txt # write the plan to a file

The GitHub PAT (gist scope) is prompted for interactively (visible, so
you can verify the paste) and is used in memory only when prompting --
it is never saved or printed. Non-interactive runs fall back to
GH_TOKEN / GITHUB_TOKEN, then gist_token.txt next to the repo root --
the same resolution as the other tools.

The ImgBB API key is READ from system_assets/imgbb_api_key.txt next to
the repo root (git-ignored), or the IMGBB_API_KEY environment variable,
or pasted at a visible prompt when neither is available. The key is
never written to a gist, printed, or saved. Gists are only rewritten
with --apply; re-run `python scan_gists.py --refresh` afterwards so the
DB picks up the new image URLs.
"""
import argparse
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

API = "https://api.github.com"
IMGBB_API = "https://api.imgbb.com/1/upload"
TIMEOUT = 60
TOKEN_PATH = BASE_DIR / "gist_token.txt"
IMGBB_KEY_PATH = BASE_DIR / "system_assets" / "imgbb_api_key.txt"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

# Same URL shapes scan_gists.py parses out of thumb.md.
IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", re.I)
BARE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
GITHUB_ASSET_RE = re.compile(
    r"^https://gist\.github\.com/user-attachments/assets/"
    r"([0-9a-fA-F-]{36})/?$")
IMGBB_RE = re.compile(r"^https://i\.ibb\.co/")
CATBOX_RE = re.compile(r"^https://files\.catbox\.moe/")


def load_token() -> str:
    """Prompt user for GitHub PAT interactively (never saved)."""
    if not sys.stdin.isatty():
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = os.environ.get(var)
            if token:
                return token.strip()
        if TOKEN_PATH.exists():
            return TOKEN_PATH.read_text(encoding="utf-8").strip()
        raise RuntimeError(
            "Cannot prompt for a gist token without an interactive "
            "terminal. Set GH_TOKEN or create gist_token.txt.")
    # Plain input() so you can see the PAT as you paste/verify it.
    # The token is used in memory only -- it is never saved or printed.
    token = input(
        "GitHub personal access token with gist access: ").strip()
    if not token:
        raise RuntimeError("No token entered.")
    return token


def load_imgbb_key() -> str:
    """Load the ImgBB API key (never saved, never printed).

    Order: IMGBB_API_KEY env var, system_assets/imgbb_api_key.txt, then
    a visible interactive prompt (so you can verify the paste). The key
    is used in memory only and is never written to a gist.
    """
    key = os.environ.get("IMGBB_API_KEY")
    if key:
        return key.strip()
    if IMGBB_KEY_PATH.exists():
        return IMGBB_KEY_PATH.read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Cannot prompt for an ImgBB API key without an interactive "
            "terminal. Set IMGBB_API_KEY or create "
            "system_assets/imgbb_api_key.txt.")
    key = input("ImgBB API key (visible, so you can verify the paste): "
                ).strip()
    if not key:
        raise RuntimeError("No ImgBB API key entered.")
    return key


def create_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "VividChat-MigrateThumbImgBB/1.0",
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
    })
    return session


def gist_id_from_input(value: str) -> str:
    """Extract gist ID from a gist.github.com URL or return it directly."""
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{20,}", value):
        return value.lower()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") \
            or parsed.hostname != "gist.github.com":
        raise ValueError("Enter a gist.github.com URL or a gist ID.")
    parts = [p for p in parsed.path.split("/") if p]
    # gist.github.com/owner/id  or  gist.github.com/id
    if len(parts) >= 2:
        return parts[1].lower()
    if len(parts) == 1:
        return parts[0].lower()
    raise ValueError("Could not parse gist ID from URL.")


def parse_image_url(text):
    """First image URL in thumb.md text (same order as scan_gists.py)."""
    if not text:
        return None
    for rx, group in ((IMG_SRC_RE, 1), (MD_IMG_RE, 1), (BARE_URL_RE, 0)):
        m = rx.search(text)
        if m:
            return m.group(group).strip()
    return None


def list_own_gists(session: requests.Session) -> list:
    """List all gists owned by the authenticated user."""
    gists = []
    page = 1
    while True:
        print(f"Listing gists (page {page})...", flush=True)
        r = session.get(f"{API}/gists?per_page=100&page={page}",
                        timeout=TIMEOUT)
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected gist listing.")
        gists.extend(batch)
        print(f"  received {len(batch)} gist(s), {len(gists)} total.",
              flush=True)
        if len(batch) < 100:
            return gists
        page += 1


def fetch_gist(session: requests.Session, gist_id: str) -> dict:
    r = session.get(f"{API}/gists/{gist_id}", timeout=TIMEOUT)
    if r.status_code == 404:
        raise RuntimeError(f"Gist {gist_id} not found.")
    r.raise_for_status()
    return r.json()


def thumb_text(session: requests.Session, gist: dict):
    """Return (filename, content) for the gist's thumb file, or None.

    Prefers the `content` field the API returns with GET /gists/{id};
    falls back to fetching `raw_url` (with auth) when truncated.
    """
    files = gist.get("files") or {}
    name = next((fn for fn in files if fn.lower() == "thumb.md"), None)
    if name is None:
        return None
    det = files[name] or {}
    content = det.get("content")
    if content and not det.get("truncated"):
        return name, content
    raw_url = det.get("raw_url")
    if not raw_url:
        raise RuntimeError("thumb file has no content or raw_url.")
    r = session.get(raw_url, timeout=TIMEOUT,
                    headers={"Accept": "*/*", "User-Agent": BROWSER_UA})
    r.raise_for_status()
    return name, r.text


def is_migratable(url) -> bool:
    return bool(url) and bool(GITHUB_ASSET_RE.match(url))


def guess_extension(data: bytes, content_type: str) -> str:
    """File extension from magic bytes, else the Content-Type."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    ext = mimetypes.guess_extension((content_type or "").split(";")[0])
    return ext or ".png"


def download_image(session: requests.Session, url: str) -> tuple:
    """Download gist user-attachment image bytes using the PAT."""
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True,
                    headers={"Accept": "*/*", "User-Agent": BROWSER_UA})
    r.raise_for_status()
    content_type = r.headers.get("Content-Type", "")
    data = r.content
    if not content_type.lower().startswith("image/"):
        snippet = data[:120].decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"expected image bytes, got Content-Type "
            f"{content_type or '(none)'}: {snippet!r} "
            f"(unauthenticated GitHub user-attachment URLs return HTML)")
    if not data:
        raise RuntimeError("image download returned an empty body.")
    return data, content_type


def upload_to_imgbb(data: bytes, content_type: str,
                    imgbb_key: str) -> str:
    """Upload image bytes to ImgBB; return the direct image URL.

    ImgBB: POST https://api.imgbb.com/1/upload?key=<key> with form field
    `image` = raw bytes. Response JSON `data.url` is the direct image
    URL (data.display_url is a viewer page -- not used here).
    """
    filename = "thumb" + guess_extension(data, content_type)
    mime = (content_type or "application/octet-stream").split(";")[0].strip()
    last_error = None
    for attempt in (1, 2):
        try:
            r = requests.post(
                IMGBB_API,
                params={"key": imgbb_key},
                files={"image": (filename, data, mime)},
                timeout=TIMEOUT,
                headers={"User-Agent": BROWSER_UA})
            if r.status_code != 200:
                raise RuntimeError(
                    f"ImgBB upload failed: HTTP {r.status_code} "
                    f"{r.text[:200]}")
            payload = r.json()
            if not payload.get("success"):
                raise RuntimeError(
                    f"ImgBB upload failed: {payload.get('error', payload)}")
            direct_url = (payload.get("data") or {}).get("url")
            if not direct_url:
                raise RuntimeError(
                    f"ImgBB response had no data.url: {str(payload)[:200]}")
            return direct_url
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2)
    raise RuntimeError(f"ImgBB upload failed: {last_error}")


def replace_image_url(text: str, old_url: str, new_url: str) -> str:
    """Swap the image URL in thumb.md, preserving its original format."""
    if "<img" in text.lower() and old_url in text:
        return text.replace(old_url, new_url, 1)
    if "![" in text and old_url in text:
        return text.replace(old_url, new_url, 1)
    if old_url in text:
        return text.replace(old_url, new_url, 1)
    # Fallback: replace only the first URL found (same order as parsing).
    for rx, group in ((IMG_SRC_RE, 1), (MD_IMG_RE, 1), (BARE_URL_RE, 0)):
        m = rx.search(text)
        if m:
            start, end = m.span(group)
            return text[:start] + new_url + text[end:]
    raise RuntimeError("could not locate image URL in thumb.md to replace.")


def patch_thumb(session: requests.Session, gist_id: str,
                filename: str, new_content: str) -> bool:
    """Patch thumb.md in a gist; verify the ImgBB URL took effect."""
    patch = {"files": {filename: {"content": new_content}}}
    r = session.patch(f"{API}/gists/{gist_id}", json=patch, timeout=TIMEOUT)
    if r.status_code not in (200, 201):
        print(f"    FAILED: HTTP {r.status_code} {r.text[:200]}")
        return False
    after = (r.json().get("files") or {}).get(filename) or {}
    if "i.ibb.co" not in (after.get("content") or ""):
        print("    FAILED: patched thumb.md does not contain the ImgBB URL")
        return False
    return True


def process_gist(session, gist) -> tuple:
    """Inspect one gist's thumb.md.

    Returns ("skipped"|"failed", reason) for gists that need no work, or
    (gist_id, gist_url, filename, content, old_url) for a migratable one.
    """
    gist_id = gist.get("id")
    gist_url = gist.get("html_url") or gist_id
    found = thumb_text(session, gist)
    if found is None:
        return "skipped", "no thumb.md"
    filename, content = found
    old_url = parse_image_url(content)
    if not old_url:
        return "skipped", "thumb.md has no image URL"
    if IMGBB_RE.match(old_url):
        return "skipped", "already on ImgBB"
    if CATBOX_RE.match(old_url):
        return "skipped", "already on Catbox"
    if not is_migratable(old_url):
        return "skipped", f"non-GitHub image URL: {old_url[:80]}"
    return gist_id, gist_url, filename, content, old_url


def collect_targets(session, requested, username):
    """Resolve the gists to process (owned only, like the other tools)."""
    if requested:
        gists = []
        for gid in requested:
            try:
                gist = fetch_gist(session, gid)
            except RuntimeError as exc:
                print(f"  SKIP {gid}: {exc}")
                continue
            owner = (gist.get("owner") or {}).get("login", "")
            if owner != username:
                print(f"  SKIP {gid}: owned by {owner}, not {username}")
                continue
            gists.append(gist)
        print(f"Found {len(gists)} owned gist(s) to process.", flush=True)
        return gists
    return list_own_gists(session)


def migrate_one(session, gist_id, filename, content, old_url,
                imgbb_key):
    """Download -> upload to ImgBB -> patch thumb.md. Returns new URL."""
    data, content_type = download_image(session, old_url)
    print(f"    downloaded {len(data)} bytes ({content_type}); "
          f"uploading to ImgBB...", flush=True)
    imgbb_url = upload_to_imgbb(data, content_type, imgbb_key)
    if not IMGBB_RE.match(imgbb_url):
        raise RuntimeError(
            f"unexpected ImgBB response: {imgbb_url[:120]!r}")
    new_content = replace_image_url(content, old_url, imgbb_url)
    print(f"    imgbb: {imgbb_url}")
    print("    patching thumb.md...", flush=True)
    if not patch_thumb(session, gist_id, filename, new_content):
        raise RuntimeError("thumb.md patch did not verify.")
    return imgbb_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate gist thumb.md images from GitHub "
                    "user-attachments to ImgBB",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually download/upload images and patch thumb.md files")
    parser.add_argument(
        "--gist", action="append", default=[],
        help="Process only this gist ID or gist.github.com URL (repeatable)")
    parser.add_argument(
        "--save", metavar="FILE",
        help="Write the migration plan to a file")
    args = parser.parse_args()

    try:
        token = load_token()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    session = create_session(token)

    try:
        user_response = session.get(f"{API}/user", timeout=TIMEOUT)
        user_response.raise_for_status()
        username = user_response.json().get("login")
        if not username:
            raise RuntimeError(
                "GitHub did not identify the authenticated account.")
        print(f"Gist account: {username}", flush=True)
    except requests.RequestException as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    requested = []
    for value in args.gist:
        try:
            requested.append(gist_id_from_input(value))
        except ValueError as exc:
            print(f"  SKIP {value!r}: {exc}")
    requested = sorted(set(requested))

    gists = collect_targets(session, requested, username)

    # Load the ImgBB key only when actually uploading -- a dry run
    # never needs it, so you are never prompted for it on a plan run.
    imgbb_key = None
    if args.apply:
        try:
            imgbb_key = load_imgbb_key()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("ImgBB key loaded.", flush=True)

    counts = {"migrated": 0, "planned": 0, "skipped": 0, "failed": 0}
    plan_lines = []
    for gist in gists:
        gist_url = gist.get("html_url") or gist.get("id") or "(unknown gist)"
        try:
            result = process_gist(session, gist)
        except Exception as exc:  # per-gist isolation: never abort the run
            counts["failed"] += 1
            plan_lines.append(
                f"FAIL  {gist_url}  ({type(exc).__name__}: {exc})")
            print(f"  FAIL  {gist_url}  ({type(exc).__name__}: {exc})")
            continue
        if len(result) == 2:
            status, reason = result
            counts[status] += 1
            plan_lines.append(f"SKIP  {gist_url}  ({reason})")
            print(f"  SKIP  {gist_url}  ({reason})")
            continue

        gist_id, gist_url, filename, content, old_url = result
        plan_lines.append(f"{gist_id}  {gist_url}  {old_url}")
        print(f"\n  {gist_id}  {gist_url}\n    {old_url}")

        if not args.apply:
            counts["planned"] += 1
            print("    would download -> upload to ImgBB -> patch thumb.md")
            continue

        print("    downloading image (authenticated)...", flush=True)
        try:
            migrate_one(session, gist_id, filename, content, old_url,
                        imgbb_key)
        except Exception as exc:
            counts["failed"] += 1
            plan_lines.append(
                f"FAIL  {gist_url}  ({type(exc).__name__}: {exc})")
            print(f"    FAILED: {type(exc).__name__}: {exc}")
            continue
        counts["migrated"] += 1
        print(f"    migrated {gist_url}")

    if args.save:
        Path(args.save).write_text("\n".join(plan_lines) + "\n",
                                    encoding="utf-8")
        print(f"\nPlan written to {args.save} ({len(plan_lines)} line(s)).")

    if args.apply:
        print(f"\nDone: {counts['migrated']} migrated, "
              f"{counts['skipped']} skipped, {counts['failed']} failed.")
        if counts["migrated"]:
            print("Re-run `python scan_gists.py --refresh` so the DB "
                  "picks up the new image URLs.")
    else:
        print(f"\nDry run: {counts['planned']} would migrate, "
              f"{counts['skipped']} skipped, {counts['failed']} failed. "
              f"Re-run with --apply to write the changes.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())


