#!/usr/bin/env python3
"""
Create a new gist from the story folder with proper naming conventions.

Scans the story/ folder, validates images, uploads images to ImgBB,
and creates a gist with canonical filenames matching the gochatapp format.

Usage:
  python tools/story_gist_writer.py                    # dry run, report only
  python tools/story_gist_writer.py --apply            # create the gist online
  python tools/story_gist_writer.py --public           # make gist public (default: secret)
  python tools/story_gist_writer.py --description="..." # gist description
  python tools/story_gist_writer.py --no-imgbb         # skip ImgBB, use local filename placeholders
  python tools/story_gist_writer.py --force            # apply even if image checks fail
  python tools/story_gist_writer.py --story-master=FILE # disambiguate if multiple story .txt files

Auth (same as scan_gists.py / migrate_thumb_imgbb.py):
  GitHub PAT: GH_TOKEN / GITHUB_TOKEN env > gist_token.txt > gist_local.yaml
  ImgBB key:  IMGBB_API_KEY env > system_assets/imgbb_api_key.txt > prompt (only on --apply)
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
GIST_LOCAL_PATH = BASE_DIR / "gist_local.yaml"
IMGBB_KEY_PATH = BASE_DIR / "system_assets" / "imgbb_api_key.txt"
STORY_DIR = BASE_DIR / "story"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

# Canonical gist filename tags (from scan_gists.py)
STORY_TAG_PREFIX = "GOCHATAPPSTORYTAG"
ROLE_TAG_PREFIX = "GOCHATAPPROLETAG"

# Image extensions we recognize
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Regex to extract slug from filename
SLUG_RE = re.compile(r"[^a-z0-9]+")
def slugify(name: str) -> str:
    """Convert a name to a slug: lowercase, non-alnum -> _, collapse, strip."""
    s = name.lower()
    s = SLUG_RE.sub("_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def load_github_token() -> str:
    """Load GitHub PAT from env, gist_token.txt, or gist_local.yaml (never saved)."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(var)
        if token:
            return token.strip()
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    if GIST_LOCAL_PATH.exists():
        import yaml
        try:
            data = yaml.safe_load(GIST_LOCAL_PATH.read_text(encoding="utf-8"))
            token = (data.get("github") or {}).get("token")
            if token:
                return token.strip()
        except Exception:
            pass
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Cannot prompt for a GitHub token without an interactive terminal. "
            "Set GH_TOKEN or create gist_token.txt.")
    token = input("GitHub personal access token with gist access: ").strip()
    if not token:
        raise RuntimeError("No token entered.")
    return token


def load_imgbb_key() -> str:
    """Load ImgBB API key from env, file, or prompt (only on --apply)."""
    key = os.environ.get("IMGBB_API_KEY")
    if key:
        return key.strip()
    if IMGBB_KEY_PATH.exists():
        return IMGBB_KEY_PATH.read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Cannot prompt for an ImgBB API key without an interactive terminal. "
            "Set IMGBB_API_KEY or create system_assets/imgbb_api_key.txt.")
    key = input("ImgBB API key (visible, so you can verify the paste): ").strip()
    if not key:
        raise RuntimeError("No ImgBB API key entered.")
    return key


def create_github_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "VividChat-StoryGistWriter/1.0",
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
    })
    return session


def verify_token(session: requests.Session) -> str:
    """Call GET /user to verify the PAT works; return username."""
    r = session.get(f"{API}/user", timeout=TIMEOUT)
    r.raise_for_status()
    user = r.json()
    username = user.get("login")
    if not username:
        raise RuntimeError("GitHub did not identify the authenticated account.")
    return username


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


def upload_to_imgbb(data: bytes, content_type: str, imgbb_key: str) -> str:
    """Upload image bytes to ImgBB; return the direct image URL."""
    filename = "image" + guess_extension(data, content_type)
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
                    f"ImgBB upload failed: HTTP {r.status_code} {r.text[:200]}")
            payload = r.json()
            if not payload.get("success"):
                raise RuntimeError(f"ImgBB upload failed: {payload.get('error', payload)}")
            direct_url = (payload.get("data") or {}).get("url")
            if not direct_url:
                raise RuntimeError(f"ImgBB response had no data.url: {str(payload)[:200]}")
            return direct_url
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2)
    raise RuntimeError(f"ImgBB upload failed: {last_error}")
def scan_story_folder() -> dict:
    if not STORY_DIR.exists():
        raise RuntimeError(f'Story folder not found: {STORY_DIR}')
    all_files = list(STORY_DIR.iterdir())
    txt_files = [f for f in all_files if f.suffix.lower() == '.txt']
    img_files = [f for f in all_files if f.suffix.lower() in IMAGE_EXTS]
    characters = []
    story_elements = []
    story_master_candidates = []
    used_images = set()
    for txt in txt_files:
        name = txt.stem
        lower = name.lower()
        if lower.startswith('character_'):
            slug = slugify(name[len('character_'):])
            img_path = None
            for img in img_files:
                img_stem = img.stem
                img_lower = img_stem.lower()
                if img_lower.startswith('character_'):
                    img_stem = img_stem[len('character_'):]
                if slugify(img_stem) == slug:
                    img_path = img
                    used_images.add(img)
                    break
            characters.append((txt, img_path, slug))
        elif lower.startswith('storyelement_'):
            slug = slugify(name[len('storyelement_'):])
            story_elements.append((txt, slug))
        elif lower.startswith('story_'):
            slug = slugify(name[len('story_'):])
            story_master_candidates.append((txt, slug))
        else:
            # Legacy: any other txt file could be story master
            slug = slugify(name)
            story_master_candidates.append((txt, slug))

    # Find orphan images
    orphan_images = [img for img in img_files if img not in used_images]

    # Match story master to thumb.* image
    story_master = None
    if story_master_candidates:
        for txt, slug in story_master_candidates:
            img_path = None
            for img in orphan_images:
                # Story image is thumb.* (any extension)
                if img.stem.lower() == 'thumb':
                    img_path = img
                    used_images.add(img)
                    break
            story_master = (txt, img_path, slug)
            break

    # Recompute orphans after matching story master
    orphan_images = [img for img in img_files if img not in used_images]
    return {
        'characters': characters,
        'story_master': story_master,
        'story_elements': story_elements,
        'orphan_images': orphan_images,
        'story_master_candidates': story_master_candidates,
    }


def read_file(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def build_gist_files(scan_result: dict, imgbb_key: str = None, no_imgbb: bool = False) -> tuple:
    files = {}
    upload_plan = []
    checks = {
        'total_files': 0,
        'total_bytes': 0,
        'missing_character_images': [],
        'missing_story_image': False,
        'all_checks_pass': True,
    }

    for txt_path, img_path, slug in scan_result['characters']:
        content = read_file(txt_path)
        gist_filename = f'character_{slug}.txt'
        files[gist_filename] = content
        checks['total_files'] += 1
        checks['total_bytes'] += len(content.encode('utf-8'))

        if img_path:
            if no_imgbb:
                direct_url = f'LOCAL:{img_path.name}'
                upload_plan.append((img_path, direct_url, False))
            else:
                data = img_path.read_bytes()
                content_type = mimetypes.guess_type(str(img_path))[0] or 'application/octet-stream'
                if imgbb_key:
                    direct_url = upload_to_imgbb(data, content_type, imgbb_key)
                else:
                    direct_url = f'UPLOAD_TO_IMGBB:{img_path.name}'
                upload_plan.append((img_path, direct_url, False))
        else:
            checks['missing_character_images'].append(slug)
            checks['all_checks_pass'] = False

        if img_path:
            thumb_filename = f'{slug}_thumb.md'
            if no_imgbb:
                src = img_path.name
            else:
                src = next((url for p, url, _ in upload_plan if p == img_path), img_path.name)
            thumb_content = f'<img width="450" height="450" alt="image" src="{src}" />'
            files[thumb_filename] = thumb_content
            checks['total_files'] += 1
            checks['total_bytes'] += len(thumb_content.encode('utf-8'))

    if scan_result['story_master']:
        txt_path, img_path, slug = scan_result['story_master']
        content = read_file(txt_path)
        gist_filename = f'{STORY_TAG_PREFIX}_{slug}.md'
        files[gist_filename] = content
        checks['total_files'] += 1
        checks['total_bytes'] += len(content.encode('utf-8'))

        if img_path:
            if no_imgbb:
                direct_url = f'LOCAL:{img_path.name}'
                upload_plan.append((img_path, direct_url, True))
            else:
                data = img_path.read_bytes()
                content_type = mimetypes.guess_type(str(img_path))[0] or 'application/octet-stream'
                if imgbb_key:
                    direct_url = upload_to_imgbb(data, content_type, imgbb_key)
                else:
                    direct_url = f'UPLOAD_TO_IMGBB:{img_path.name}'
                upload_plan.append((img_path, direct_url, True))

            # Create story thumb.md (like character thumbs but named thumb.md)
            thumb_filename = "thumb.md"
            if no_imgbb:
                src = img_path.name
            else:
                src = next((url for p, url, _ in upload_plan if p == img_path), img_path.name)
            thumb_content = f'<img width="450" height="450" alt="image" src="{src}" />'
            files[thumb_filename] = thumb_content
            checks['total_files'] += 1
            checks['total_bytes'] += len(thumb_content.encode('utf-8'))
        else:
            checks['missing_story_image'] = True
            checks['all_checks_pass'] = False

    for txt_path, slug in scan_result['story_elements']:
        content = read_file(txt_path)
        gist_filename = f'storyelement_{slug}.txt'
        files[gist_filename] = content
        checks['total_files'] += 1
        checks['total_bytes'] += len(content.encode('utf-8'))

    for img_path in scan_result['orphan_images']:
        if no_imgbb:
            direct_url = f'LOCAL:{img_path.name}'
            upload_plan.append((img_path, direct_url, False))
        else:
            data = img_path.read_bytes()
            content_type = mimetypes.guess_type(str(img_path))[0] or 'application/octet-stream'
            if imgbb_key:
                direct_url = upload_to_imgbb(data, content_type, imgbb_key)
            else:
                direct_url = f'UPLOAD_TO_IMGBB:{img_path.name}'
            upload_plan.append((img_path, direct_url, False))

    return files, upload_plan, checks


def print_report(scan_result: dict, files: dict, upload_plan: list, checks: dict,
                 token_source: str, username: str, apply: bool, public: bool,
                 description: str, no_imgbb: bool):
    print("=" * 70)
    print("STORY GIST WRITER - REPORT")
    print("=" * 70)
    print(f"GitHub account: {username}  (token from {token_source})")
    print(f"Gist visibility: {'public' if public else 'secret'}")
    print(f"Description: {description or '(none)'}")
    print(f"ImgBB upload: {'disabled (--no-imgbb)' if no_imgbb else 'enabled'}")
    print(f"Mode: {'APPLY (will create gist)' if apply else 'DRY RUN (no writes)'}")
    print()

    if scan_result['story_master']:
        txt_path, img_path, slug = scan_result['story_master']
        gist_fn = f"{STORY_TAG_PREFIX}_{slug}.md"
        print(f"Story master:")
        print(f"  Local:     {txt_path.name} -> Gist: {gist_fn} ({txt_path.stat().st_size} bytes)")
        if img_path:
            print(f"  Image:     {img_path.name} -> ImgBB ({img_path.stat().st_size} bytes)")
        else:
            print(f"  Image:     MISSING!")
    else:
        print("Story master: NONE FOUND")
    print()

    print(f"Characters ({len(scan_result['characters'])}):")
    for txt_path, img_path, slug in scan_result['characters']:
        gist_fn = f"character_{slug}.txt"
        thumb_fn = f"{slug}_thumb.md"
        print(f"  {slug}:")
        print(f"    Record:  {txt_path.name} -> {gist_fn} ({txt_path.stat().st_size} bytes)")
        if img_path:
            print(f"    Image:   {img_path.name} -> {thumb_fn} ({img_path.stat().st_size} bytes)")
        else:
            print(f"    Image:   MISSING!")
            print(f"    Thumb:   {thumb_fn} (placeholder)")
    print()

    print(f"Story elements ({len(scan_result['story_elements'])}):")
    for txt_path, slug in scan_result['story_elements']:
        gist_fn = f"storyelement_{slug}.txt"
        print(f"  {slug}: {txt_path.name} -> {gist_fn} ({txt_path.stat().st_size} bytes)")
    print()

    if scan_result['orphan_images']:
        print("Orphan images (uploaded but not referenced in gist):")
        for img_path in scan_result['orphan_images']:
            print(f"  {img_path.name} ({img_path.stat().st_size} bytes)")
        print()

    print("ImgBB upload plan:")
    if upload_plan:
        for local_path, direct_url, is_story in upload_plan:
            tag = " [story image]" if is_story else ""
            if direct_url.startswith("UPLOAD_TO_IMGBB:") or direct_url.startswith("LOCAL:"):
                print(f"  {local_path.name} -> {direct_url}{tag} (dry run)")
            else:
                print(f"  {local_path.name} -> {direct_url}{tag}")
    else:
        print("  (no images to upload)")
    print()

    print("CHECKS:")
    print(f"  Total gist files: {checks['total_files']}")
    print(f"  Total content bytes: {checks['total_bytes']}")
    if checks['missing_character_images']:
        print(f"  MISSING character images for: {', '.join(checks['missing_character_images'])}")
    else:
        print("  All character images present: YES")
    if checks['missing_story_image']:
        print("  Story image: MISSING")
    else:
        print("  Story image present: YES")
    print(f"  All checks pass: {'YES' if checks['all_checks_pass'] else 'NO'}")
    print()

    if not apply:
        print("=" * 70)
        print("DRY RUN COMPLETE - No gist created.")
        print("Re-run with --apply to create the gist online.")
        print("=" * 70)
    elif not checks['all_checks_pass']:
        print("=" * 70)
        print("CHECKS FAILED - Gist NOT created (use --force to override).")
        print("=" * 70)


def create_gist(session, files, public, description):
    payload = {
        'description': description or 'Story gist created by story_gist_writer.py',
        'public': public,
        'files': {name: {'content': content} for name, content in files.items()}
    }
    r = session.post(f'{API}/gists', json=payload, timeout=TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'Gist creation failed: HTTP {r.status_code} {r.text[:500]}')
    return r.json()


def verify_gist(session, gist_id, expected_files):
    r = session.get(f'{API}/gists/{gist_id}', timeout=TIMEOUT)
    if r.status_code != 200:
        print(f'  Verification failed: HTTP {r.status_code}')
        return False
    gist = r.json()
    actual_files = set(gist.get('files', {}).keys())
    missing = expected_files - actual_files
    extra = actual_files - expected_files
    if missing:
        print(f'  Verification FAILED: missing files: {sorted(missing)}')
        return False
    if extra:
        print(f'  Note: extra files in gist: {sorted(extra)}')
    return True


def main():
    parser = argparse.ArgumentParser(description='Create a story gist from the story/ folder')
    parser.add_argument('--apply', action='store_true', help='Create the gist online (default: dry run)')
    parser.add_argument('--public', action='store_true', help='Make gist public (default)')
    parser.add_argument('--private', action='store_true', help='Make gist private/secret')
    parser.add_argument('--description', metavar='TEXT', help='Gist description')
    parser.add_argument('--no-imgbb', action='store_true', help='Skip ImgBB upload, use local filename placeholders')
    parser.add_argument('--force', action='store_true', help='Apply even if image checks fail')
    parser.add_argument('--story-master', metavar='FILE', help='Story master filename if multiple candidates')
    args = parser.parse_args()
    public = not args.private
    try:
        github_token = load_github_token()
    except RuntimeError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    github_session = create_github_session(github_token)
    try:
        username = verify_token(github_session)
        token_source = 'GH_TOKEN env' if os.environ.get('GH_TOKEN') else 'GITHUB_TOKEN env' if os.environ.get('GITHUB_TOKEN') else 'gist_token.txt' if TOKEN_PATH.exists() else 'gist_local.yaml'
    except Exception as exc:
        print(f'Authentication failed: {exc}', file=sys.stderr)
        return 1
    try:
        scan_result = scan_story_folder()
    except RuntimeError as exc:
        print(f'Error scanning story folder: {exc}', file=sys.stderr)
        return 1
    if len(scan_result['story_master_candidates']) > 1 and not args.story_master:
        print('Multiple story master candidates found:')
        for txt, slug in scan_result['story_master_candidates']:
            print(f'  {txt.name} (slug: {slug})')
        print('Use --story-master=FILE to select one.')
        return 1
    elif args.story_master:
        for txt, slug in scan_result['story_master_candidates']:
            if txt.name == args.story_master:
                img_path = None
                for img in scan_result['orphan_images']:
                    if slugify(img.stem) == slug:
                        img_path = img
                        break
                scan_result['story_master'] = (txt, img_path, slug)
                break
        else:
            print(f'Story master file not found: {args.story_master}', file=sys.stderr)
            return 1
    imgbb_key = None
    if args.apply and not args.no_imgbb:
        try:
            imgbb_key = load_imgbb_key()
            print('ImgBB key loaded.')
        except RuntimeError as exc:
            print(f'Error loading ImgBB key: {exc}', file=sys.stderr)
            return 1
    files, upload_plan, checks = build_gist_files(scan_result, imgbb_key=imgbb_key, no_imgbb=args.no_imgbb)
    # Determine description for report (same logic as used for gist creation)
    report_description = args.description
    if not report_description and scan_result['story_master']:
        txt_path, _, _ = scan_result['story_master']
        report_description = read_file(txt_path).strip()
    print_report(scan_result, files, upload_plan, checks, token_source, username, args.apply, public, report_description, args.no_imgbb)
    if not args.apply:
        return 0
    if not checks['all_checks_pass'] and not args.force:
        print('Aborting: checks failed. Use --force to create gist anyway.')
        return 1
    if not args.no_imgbb and imgbb_key:
        final_upload_plan = []
        for local_path, direct_url, is_story in upload_plan:
            if direct_url.startswith('UPLOAD_TO_IMGBB:') or direct_url.startswith('LOCAL:'):
                print(f'Uploading {local_path.name} to ImgBB...', flush=True)
                data = local_path.read_bytes()
                content_type = mimetypes.guess_type(str(local_path))[0] or 'application/octet-stream'
                try:
                    new_url = upload_to_imgbb(data, content_type, imgbb_key)
                    final_upload_plan.append((local_path, new_url, is_story))
                    print(f'  -> {new_url}')
                except RuntimeError as exc:
                    print(f'  FAILED: {exc}')
                    return 1
            else:
                final_upload_plan.append((local_path, direct_url, is_story))
        upload_plan = final_upload_plan
        for txt_path, img_path, slug in scan_result['characters']:
            if img_path:
                thumb_filename = f'{slug}_thumb.md'
                direct_url = next((url for p, url, _ in upload_plan if p == img_path), img_path.name)
                files[thumb_filename] = f'<img width="450" height="450" alt="image" src="{direct_url}" />'
        # Rebuild story thumb.md
        if scan_result['story_master']:
            txt_path, img_path, slug = scan_result['story_master']
            if img_path:
                thumb_filename = "thumb.md"
                direct_url = next((url for p, url, _ in upload_plan if p == img_path), img_path.name)
                files[thumb_filename] = f'<img width="450" height="450" alt="image" src="{direct_url}" />'
    print('\nCreating gist...', flush=True)
    # Use story content as description if no explicit description provided
    description = args.description
    if not description and scan_result['story_master']:
        txt_path, _, _ = scan_result['story_master']
        description = read_file(txt_path).strip()
    try:
        gist = create_gist(github_session, files, public, description)
    except RuntimeError as exc:
        print(f'FAILED: {exc}')
        return 1
    gist_id = gist.get('id')
    gist_url = gist.get('html_url')
    print(f'Gist created: {gist_url}')
    print('Verifying gist...', flush=True)
    expected_files = set(files.keys())
    if verify_gist(github_session, gist_id, expected_files):
        print('Verification PASSED - all files present.')
    else:
        print('Verification FAILED - some files missing.')
        return 1
    print('\\nRaw file URLs:')
    for fn in sorted(files.keys()):
        raw_url = f'https://gist.githubusercontent.com/{username}/{gist_id}/raw/{fn}'
        print(f'  {fn}: {raw_url}')
    return 0


if __name__ == '__main__':
    sys.exit(main())