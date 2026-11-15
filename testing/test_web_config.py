"""
Tests for the config editor web API (GET/POST /api/config, reload, pages).

Runs against the aiohttp app via aiohttp.test_utils, so no real port is bound.
The POST round-trip writes to a temporary copy of config.yaml so the real file
is never modified by the test.

Run:  python testing/test_web_config.py
"""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.config as cfg
from system.web_server import create_web_app
from aiohttp.test_utils import TestClient, TestServer


PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


async def run():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        # --- GET /api/config -------------------------------------------------
        print("\nGET /api/config")
        resp = await client.get("/api/config")
        check("status 200", resp.status == 200, f"got {resp.status}")
        data = await resp.json()
        for key in ("user_id", "debug", "gist_github_token", "bots", "providers"):
            check(f"has '{key}'", key in data)
        check("bots is list", isinstance(data.get("bots"), list))
        check("providers is list", isinstance(data.get("providers"), list))
        check("bot rows have name+token",
              all("name" in b and "token" in b for b in data.get("bots", [])))
        check("provider rows have name+type+base",
              all("name" in p and "type" in p and "base" in p
                  for p in data.get("providers", [])))

        # --- Static pages ----------------------------------------------------
        print("\nGET pages / static assets")
        resp = await client.get("/setup")
        check("/setup 200", resp.status == 200, f"got {resp.status}")
        check("/setup is html", "text/html" in resp.headers.get("Content-Type", ""))
        body = await resp.text()
        check("/setup references /static/setup.js", "/static/setup.js" in body)

        resp = await client.get("/browse")
        check("/browse 200", resp.status == 200, f"got {resp.status}")

        resp = await client.get("/static/styles.css")
        check("/static/styles.css 200", resp.status == 200, f"got {resp.status}")

        resp = await client.get("/static/setup.js")
        check("/static/setup.js 200", resp.status == 200, f"got {resp.status}")

        # --- Validation ------------------------------------------------------
        print("\nPOST /api/test/telegram (validation only)")
        resp = await client.post("/api/test/telegram", json={})
        check("empty token -> 400", resp.status == 400, f"got {resp.status}")

        # --- Reload ----------------------------------------------------------
        print("\nPOST /api/config/reload")
        resp = await client.post("/api/config/reload")
        check("reload 200", resp.status == 200, f"got {resp.status}")
        reload_data = await resp.json()
        check("reload success", reload_data.get("success") is True)
        check("reload returns providers", isinstance(reload_data.get("providers"), list))

        # --- POST /api/config round-trip (temp file, real config untouched) --
        print("\nPOST /api/config (temp copy)")
        original_path = cfg.CONFIG_PATH
        tmp_dir = Path(tempfile.mkdtemp(prefix="vividcfg_"))
        tmp_path = tmp_dir / "config.yaml"
        shutil.copy(original_path, tmp_path)
        orig_provider_names = [p.get("name") for p in cfg.providers_config]
        try:
            cfg.CONFIG_PATH = tmp_path
            payload = await (await client.get("/api/config")).json()
            resp = await client.post("/api/config", json=payload)
            check("POST 200", resp.status == 200, f"got {resp.status} ({await resp.text()})")
            saved = await resp.json()
            check("POST success", saved.get("success") is True)

            written = cfg._read_yaml(tmp_path)
            check("yaml has TELEGRAM_TOKENS", "TELEGRAM_TOKENS" in written)
            check("yaml has PROVIDERS list", isinstance(written.get("PROVIDERS"), list))
            check("legacy OLLAMA_LOCAL removed", "OLLAMA_LOCAL" not in written)
            written_names = [p.get("name") for p in written.get("PROVIDERS", [])]
            check("provider names round-trip", written_names == orig_provider_names,
                  f"expected {orig_provider_names}, got {written_names}")

            # After write+reload the in-memory containers must still be live.
            check("providers dict repopulated", len(cfg.providers) == len(orig_provider_names),
                  f"got {list(cfg.providers)}")
        finally:
            cfg.CONFIG_PATH = original_path
            cfg.reload_config()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
