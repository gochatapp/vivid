"""
Tests for the DeepSeek provider preset in the setup editor.

DeepSeek's hosted API is OpenAI-compatible, so Vivid reaches it through the
``openai_compatible`` provider at https://api.deepseek.com/v1. These checks
cover the editor preset mapping and the /setup page markup; no network calls
are made.

Run:  python testing/test_deepseek_provider.py
"""

import asyncio
import sys
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.web_server import (
    _EDITOR_TYPES,
    _editor_to_provider,
    _ephemeral_config,
    _provider_to_editor,
    create_web_app,
)
from aiohttp.test_utils import TestClient, TestServer

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

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


def check_editor_mapping():
    print("\nSetup editor mapping")
    check(
        "deepseek preset -> openai_compatible + DeepSeek base",
        _EDITOR_TYPES.get("deepseek") == ("openai_compatible", DEEPSEEK_BASE),
        _EDITOR_TYPES.get("deepseek"),
    )

    row = {
        "name": "deepseek",
        "type": "deepseek",
        "api_key": "sk-test",
        "model": DEEPSEEK_MODEL,
        "api_base": "",
    }
    provider_cfg = _editor_to_provider(row, {})
    check("row saves as openai_compatible", provider_cfg.get("type") == "openai_compatible", provider_cfg)
    check("row saves DeepSeek base url", provider_cfg.get("url") == DEEPSEEK_BASE, provider_cfg)
    check("row saves api key", provider_cfg.get("api_key") == "sk-test", provider_cfg)
    check("row saves model id", provider_cfg.get("model") == DEEPSEEK_MODEL, provider_cfg)

    editor_row = _provider_to_editor(provider_cfg)
    check("deepseek provider reopens as DeepSeek in editor", editor_row.get("type") == "deepseek", editor_row)
    check("editor row keeps model", editor_row.get("model") == DEEPSEEK_MODEL, editor_row)

    ephemeral = _ephemeral_config({"name": "deepseek-test", "type": "deepseek"})
    check("ephemeral config uses DeepSeek base", ephemeral.get("url") == DEEPSEEK_BASE, ephemeral)


async def check_setup_page():
    print("\nSetup page markup")
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/setup")
        check("/setup 200", resp.status == 200, f"got {resp.status}")
        body = await resp.text()
        check(
            "per-type provider buttons removed",
            "addProv('ollama')" not in body and "addProv('nvidia')" not in body,
        )
        check("generic add provider button kept", "addProv()" in body)

        resp = await client.get("/static/setup.js")
        check("/static/setup.js 200", resp.status == 200, f"got {resp.status}")
        js = await resp.text()
        check("type dropdown offers DeepSeek", "value=deepseek>DeepSeek" in js)
        check("DeepSeek preset base in setup.js", DEEPSEEK_BASE in js)
        check("DeepSeek default model in setup.js", DEEPSEEK_MODEL in js)


async def run():
    check_editor_mapping()
    await check_setup_page()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
