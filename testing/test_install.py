#!/usr/bin/env python3
"""
Test script for verifying gist installation logic.
Tests the _install_role_from_gist and _install_story_from_gist functions
with real gist IDs from gochatapp.
"""

import sys
import asyncio
import tempfile
import urllib.request
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vivid import (
    _install_role_from_gist,
    _install_story_from_gist,
    CHAT_ROLES_DIR,
    STORIES_DIR,
)

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
        import shutil
        persistent_dir = Path(tmpdir).parent / f"gist_{gist_id}_persistent"
        if persistent_dir.exists():
            shutil.rmtree(persistent_dir)
        shutil.copytree(gist_root, persistent_dir)
        return persistent_dir

async def test_role_install(gist_id="0653c10f46341223b5a3370af74c6528"):
    """Test installing a role from gist."""
    print(f"\n{'='*60}")
    print(f"Testing ROLE install from gist: {gist_id}")
    print(f"{'='*60}")
    
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
        print(f"Role file created: {role_file}")
        print(f"  Content preview: {role_file.read_text(encoding='utf-8')[:200]}...")
    else:
        print(f"Role file NOT created: {role_file}")
        return False
    
    if img_file.exists():
        print(f"Image file created: {img_file} ({img_file.stat().st_size} bytes)")
    else:
        print(f"Image file NOT created (may be expected if no thumb.md)")
    
    return True

async def test_story_install(gist_id="ae5ed1170352b40652205083d75ceda0"):
    """Test installing a story from gist."""
    print(f"\n{'='*60}")
    print(f"Testing STORY install from gist: {gist_id}")
    print(f"{'='*60}")
    
    gist_root = await download_gist_files(gist_id)
    if not gist_root:
        print("FAILED to download gist files")
        return False
    
    # Find the story tag file
    story_tag_file = None
    for f in gist_root.rglob("*"):
        if f.is_file() and "GOCHATAPPSTORYTAG_" in f.name:
            story_tag_file = f
            break
    
    if not story_tag_file:
        print("No GOCHATAPPSTORYTAG_* file found")
        return False
    
    print(f"Found story tag file: {story_tag_file.name}")
    
    status_msg = MockStatusMessage()
    await _install_story_from_gist(gist_id, gist_root, story_tag_file, status_msg)
    
    # Verify installation
    story_name = story_tag_file.stem.replace("GOCHATAPPSTORYTAG_", "")
    full_story_name = f"{story_name}!{gist_id}"
    story_dir = STORIES_DIR / full_story_name
    
    if story_dir.exists():
        print(f"Story directory created: {story_dir}")
        
        # Check subdirectories
        for subdir in ["characters", "storyelements", "story_context", "backups"]:
            sub_path = story_dir / subdir
            if sub_path.exists():
                print(f"  {subdir}/ exists")
            else:
                print(f"  {subdir}/ MISSING")
        
        # Check context.txt
        context_file = story_dir / "story_context" / "context.txt"
        if context_file.exists():
            print(f"context.txt exists: {context_file.read_text(encoding='utf-8')[:100]}...")
        else:
            print(f"context.txt MISSING")
        
        # Check characters
        chars_dir = story_dir / "characters"
        if chars_dir.exists():
            char_files = list(chars_dir.glob("*.txt"))
            print(f"  Characters found: {[f.stem for f in char_files]}")
            for cf in char_files:
                print(f"    - {cf.stem}: {cf.read_text(encoding='utf-8')[:80]}...")
        
        # Check story elements
        elem_dir = story_dir / "storyelements"
        if elem_dir.exists():
            elem_files = list(elem_dir.glob("*.txt"))
            print(f"  Story elements found: {[f.stem for f in elem_files]}")
            for ef in elem_files:
                print(f"    - {ef.stem}: {ef.read_text(encoding='utf-8')[:80]}...")
        
        # Check thumb.jpg
        thumb_file = story_dir / "thumb.jpg"
        if thumb_file.exists():
            print(f"thumb.jpg exists ({thumb_file.stat().st_size} bytes)")
        else:
            print(f"thumb.jpg NOT created")
        
        # Check character images
        for cf in chars_dir.glob("*.jpg"):
            print(f"Character image: {cf.name} ({cf.stat().st_size} bytes)")
        
    else:
        print(f"Story directory NOT created: {story_dir}")
        return False
    
    return True

async def main():
    """Run all tests."""
    print("Testing gist installation logic...")
    print("This will download real gists from GitHub.")
    
    # Test role install
    role_ok = await test_role_install()
    
    # Test story install
    story_ok = await test_story_install()
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Role install:  {'PASS' if role_ok else 'FAIL'}")
    print(f"Story install: {'PASS' if story_ok else 'FAIL'}")
    
    if role_ok and story_ok:
        print("\nAll tests passed!")
        return 0
    else:
        print("\nSome tests failed!")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)