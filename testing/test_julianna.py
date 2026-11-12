#!/usr/bin/env python3
"""Test installing the Julianna Vance gist."""

import sys
import asyncio
from pathlib import Path
import tempfile
import urllib.request
import json
import shutil

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vivid import _install_role_from_gist, CHAT_ROLES_DIR


class MockStatusMessage:
    """Mock Telegram message for testing."""
    def __init__(self):
        self.text = ""

    async def edit_text(self, text):
        self.text = text
        print(f"[STATUS] {text}")


async def download_gist_files(gist_id):
    """Download all files from a gist using GitHub API."""
    api_url = f"https://api.github.com/gists/{gist_id}"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Vivid-Chat-Bot"})
    with urllib.request.urlopen(req, timeout=30) as response:
        gist_data = json.load(response)

    files = gist_data.get("files", {})
    if not files:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        gist_root = Path(tmpdir) / gist_id
        gist_root.mkdir()

        for filename, file_info in files.items():
            raw_url = file_info.get("raw_url")
            if raw_url:
                file_path = gist_root / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    urllib.request.urlretrieve(raw_url, file_path)
                except Exception as e:
                    print(f"Failed to download {filename}: {e}")

        # Copy to a persistent location for inspection
        persistent_dir = Path(tmpdir).parent / f"gist_{gist_id}_persistent"
        if persistent_dir.exists():
            shutil.rmtree(persistent_dir)
        shutil.copytree(gist_root, persistent_dir)
        return persistent_dir


async def test_julianna_install():
    """Test installing Julianna Vance role from gist."""
    gist_id = "30dd3afbbcbf2c42eb504f9361b41860"
    print(f"Testing ROLE install from gist: {gist_id}")

    gist_root = await download_gist_files(gist_id)
    if not gist_root:
        print("FAILED to download gist files")
        return False

    # Find the role tag file
    role_tag_file = None
    for f in gist_root.rglob("*"):
        if f.is_file() and "GOCHATAPPROLETAG_" in f.name:
            role_tag_file = f
            break

    if not role_tag_file:
        print("No GOCHATAPPROLETAG_* file found")
        return False

    print(f"Found role tag file: {role_tag_file.name}")

    status_msg = MockStatusMessage()
    await _install_role_from_gist(gist_id, gist_root, role_tag_file, status_msg)

    # Verify installation
    role_name = role_tag_file.stem.replace("GOCHATAPPROLETAG_", "")
    full_name = f"{role_name}!{gist_id}"
    role_file = CHAT_ROLES_DIR / f"{full_name}.txt"
    img_file = CHAT_ROLES_DIR / f"{full_name}.jpg"

    if role_file.exists():
        print(f"SUCCESS: Role file created: {role_file}")
        print(f"  Content preview: {role_file.read_text(encoding='utf-8')[:300]}...")
    else:
        print(f"FAILED: Role file NOT created: {role_file}")
        return False

    if img_file.exists():
        print(f"  Image file created: {img_file} ({img_file.stat().st_size} bytes)")
    else:
        print(f"  Image file NOT created (thumb.md may not have image URLs)")

    return True


async def main():
    print("Testing Julianna Vance gist installation...")
    ok = await test_julianna_install()
    print(f"\nResult: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)