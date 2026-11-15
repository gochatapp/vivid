"""
Tests for the community store web API used by /browse.

Covers GET /api/store (fetch + cache), GET /api/installed, and the
install/uninstall endpoints. GitHub access is stubbed out, and all file
operations run against temporary directories, so no network or real data is
touched.

Run:  python testing/test_web_store.py
"""

import asyncio
import sys
import tempfile
import shutil
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.web_server as web_server
from aiohttp.test_utils import TestClient, TestServer


PASS = 0
FAIL = 0
GIST_ID = "0123456789abcdef0123456789abcdef"
STORY_GIST_ID = "fedcba9876543210fedcba9876543210"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def make_fake_gist():
    """Gist metadata for a role without images (no thumb.md src URLs)."""
    return {
        "files": {
            "GOCHATAPPROLETAG_store_test.txt": {
                "content": "You are Store Test, a role installed by a test.",
                "truncated": False,
                "raw_url": "",
            },
            "thumb.md": {
                "content": "<p>No image URLs here.</p>",
                "truncated": False,
                "raw_url": "",
            },
        }
    }


async def run():
    tmp_dir = Path(tempfile.mkdtemp(prefix="vividstore_"))
    roles_dir = tmp_dir / "roles"
    context_dir = tmp_dir / "context"
    config_dir = tmp_dir / "config"
    stories_dir = tmp_dir / "stories"
    for folder in (roles_dir, context_dir, config_dir, stories_dir):
        folder.mkdir()

    originals = {
        name: getattr(web_server, name)
        for name in (
            "CHAT_ROLES_DIR",
            "CHAT_CONTEXT_DIR",
            "CHAT_CONFIG_DIR",
            "STORIES_DIR",
            "_fetch_store_db",
            "_fetch_gist",
            "_read_gist_file",
        )
    }
    original_cache = dict(web_server._store_cache)

    web_server.CHAT_ROLES_DIR = roles_dir
    web_server.CHAT_CONTEXT_DIR = context_dir
    web_server.CHAT_CONFIG_DIR = config_dir
    web_server.STORIES_DIR = stories_dir
    web_server._store_cache.update(fetched_at=0.0, data=None)

    async def fake_fetch_store_db():
        return {
            "roles": [{"gist_id": GIST_ID, "rolename": "store_test"}],
            "stories": [],
            "tag_rank": {"SFW": 0},
        }

    async def fake_fetch_gist(session, gist_id):
        if gist_id != GIST_ID:
            raise RuntimeError(f"Gist not found: {gist_id}")
        return make_fake_gist()

    async def fake_read_gist_file(session, file_info):
        return file_info.get("content", "")

    web_server._fetch_store_db = fake_fetch_store_db
    web_server._fetch_gist = fake_fetch_gist
    web_server._read_gist_file = fake_read_gist_file

    try:
        app = web_server.create_web_app()
        async with TestClient(TestServer(app)) as client:
            # --- GET /api/store -------------------------------------------
            print("\nGET /api/store")
            calls = {"count": 0}

            async def counting_store_db():
                calls["count"] += 1
                return await fake_fetch_store_db()

            web_server._fetch_store_db = counting_store_db
            resp = await client.get("/api/store")
            check("status 200", resp.status == 200, f"got {resp.status}")
            data = await resp.json()
            check("roles returned", len(data.get("roles", [])) == 1)
            check("tag_rank returned", data.get("tag_rank") == {"SFW": 0})

            resp = await client.get("/api/store")
            check("second request 200", resp.status == 200)
            check("response cached", calls["count"] == 1, f"got {calls['count']}")

            resp = await client.get("/api/store?refresh=1")
            check("refresh bypasses cache", calls["count"] == 2,
                  f"got {calls['count']}")

            async def failing_store_db():
                raise RuntimeError("upstream down")

            web_server._fetch_store_db = failing_store_db
            web_server._store_cache.update(fetched_at=0.0, data=None)
            resp = await client.get("/api/store")
            check("upstream failure -> 502", resp.status == 502,
                  f"got {resp.status}")
            check("error payload", "error" in await resp.json())

            # Restore the working stub for the remaining sections.
            web_server._fetch_store_db = fake_fetch_store_db
            web_server._store_cache.update(fetched_at=0.0, data=None)

            # --- GET /api/installed ---------------------------------------
            print("\nGET /api/installed")
            role_file = roles_dir / f"Store_Test!{GIST_ID}.txt"
            role_file.write_text("role text", encoding="utf-8")
            (roles_dir / f"Store_Test!{GIST_ID}.jpg").write_bytes(b"jpg")
            (stories_dir / f"My_Story!{STORY_GIST_ID}").mkdir()

            resp = await client.get("/api/installed")
            check("status 200", resp.status == 200, f"got {resp.status}")
            installed = (await resp.json()).get("installed", [])
            role_ids = [i["gist_id"] for i in installed if i["kind"] == "role"]
            story_ids = [i["gist_id"] for i in installed if i["kind"] == "story"]
            check("role gist listed", GIST_ID in role_ids, str(installed))
            check("story gist listed", STORY_GIST_ID in story_ids, str(installed))

            # --- POST /api/store/install ----------------------------------
            print("\nPOST /api/store/install")
            resp = await client.post(
                "/api/store/install", json={"kind": "role", "gist_id": GIST_ID}
            )
            check("status 200", resp.status == 200,
                  f"got {resp.status} ({await resp.text()})")
            result = await resp.json()
            check("ok true", result.get("ok") is True)
            check("role file written",
                  (roles_dir / f"Store_Test!{GIST_ID}.txt").exists())

            resp = await client.post(
                "/api/store/install", json={"kind": "role", "gist_id": GIST_ID}
            )
            check("reinstall is idempotent", resp.status == 200,
                  f"got {resp.status}")

            resp = await client.post("/api/store/install", json={"kind": "role"})
            check("missing gist_id -> 400", resp.status == 400,
                  f"got {resp.status}")

            resp = await client.post(
                "/api/store/install",
                json={"kind": "story", "gist_id": STORY_GIST_ID},
            )
            check("unsupported kind -> 400", resp.status == 400,
                  f"got {resp.status}")

            resp = await client.post(
                "/api/store/install",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            check("invalid JSON -> 400", resp.status == 400,
                  f"got {resp.status}")

            # --- POST /api/store/uninstall --------------------------------
            print("\nPOST /api/store/uninstall")
            (context_dir / f"Store_Test!{GIST_ID}.txt").write_text(
                "history", encoding="utf-8")
            (config_dir / f"Store_Test!{GIST_ID}.status").write_text(
                "{}", encoding="utf-8")

            resp = await client.post(
                "/api/store/uninstall", json={"kind": "role", "gist_id": GIST_ID}
            )
            check("status 200", resp.status == 200,
                  f"got {resp.status} ({await resp.text()})")
            removed = (await resp.json()).get("removed", [])
            check("role file removed", len(removed) >= 1, str(removed))
            check("role txt gone", not role_file.exists())
            check("role image gone",
                  not (roles_dir / f"Store_Test!{GIST_ID}.jpg").exists())
            check("context gone",
                  not (context_dir / f"Store_Test!{GIST_ID}.txt").exists())
            check("status gone",
                  not (config_dir / f"Store_Test!{GIST_ID}.status").exists())

            resp = await client.post(
                "/api/store/uninstall", json={"kind": "role", "gist_id": GIST_ID}
            )
            check("not installed -> 404", resp.status == 404,
                  f"got {resp.status}")


    finally:
        for name, value in originals.items():
            setattr(web_server, name, value)
        web_server._store_cache.clear()
        web_server._store_cache.update(original_cache)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
