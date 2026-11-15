"""
Tests for the NVIDIA NIM provider (build.nvidia.com).

NVIDIA's hosted API is OpenAI-compatible, so Vivid reaches it through the
``openai_compatible`` provider at https://integrate.api.nvidia.com/v1. These
checks cover the setup-editor preset mapping and, when the network allows it,
the live model listing. A real chat call needs an nvapi-... key, so it is not
exercised here.

Run:  python testing/test_nvidia_provider.py
"""

import asyncio
import sys
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.providers import OpenAICompatibleProvider
from system.web_server import (
    _EDITOR_TYPES,
    _editor_to_provider,
    _ephemeral_config,
    _provider_to_editor,
    create_web_app,
)
from aiohttp.test_utils import TestClient, TestServer

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"

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
        "nvidia preset -> openai_compatible + NVIDIA base",
        _EDITOR_TYPES.get("nvidia") == ("openai_compatible", NVIDIA_BASE),
        _EDITOR_TYPES.get("nvidia"),
    )

    row = {
        "name": "nvidia",
        "type": "nvidia",
        "api_key": "nvapi-test",
        "model": NVIDIA_MODEL,
        "api_base": "",
    }
    provider_cfg = _editor_to_provider(row, {})
    check("row saves as openai_compatible", provider_cfg.get("type") == "openai_compatible", provider_cfg)
    check("row saves NVIDIA base url", provider_cfg.get("url") == NVIDIA_BASE, provider_cfg)
    check("row saves api key", provider_cfg.get("api_key") == "nvapi-test", provider_cfg)
    check("row saves model id", provider_cfg.get("model") == NVIDIA_MODEL, provider_cfg)

    editor_row = _provider_to_editor(provider_cfg)
    check("nvidia provider reopens as NVIDIA in editor", editor_row.get("type") == "nvidia", editor_row)
    check("editor row keeps model", editor_row.get("model") == NVIDIA_MODEL, editor_row)

    ephemeral = _ephemeral_config({"name": "nvidia-test", "type": "nvidia"})
    check("ephemeral config uses NVIDIA base", ephemeral.get("url") == NVIDIA_BASE, ephemeral)


async def check_live_models():
    print("\nLive /v1/models (network required)")
    provider = OpenAICompatibleProvider(
        "nvidia-test", {"url": NVIDIA_BASE, "api_key": "not-needed"}
    )
    try:
        try:
            models = await provider.list_models()
        except Exception as exc:  # network down / blocked
            check("model list reachable", False, repr(exc))
            return
        check("model list is non-empty", bool(models), f"got {len(models)} models")
        check("model ids are strings", all(isinstance(m, str) and m for m in models))
        check(
            "catalog exposes nvidia/* ids",
            any(str(m).startswith("nvidia/") for m in models),
            f"{len(models)} models",
        )
    finally:
        await _close(provider)


async def check_live_chat_wiring():
    """A bogus key must be rejected by NVIDIA auth, not by a wrong URL/route."""
    print("\nLive /v1/chat/completions wiring (invalid key, network required)")
    provider = OpenAICompatibleProvider(
        "nvidia-test", {"url": NVIDIA_BASE, "api_key": "nvapi-invalid"}
    )
    try:
        try:
            await provider.chat(NVIDIA_MODEL, [{"role": "user", "content": "ping"}], {})
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            check(
                "chat request reaches NVIDIA auth (401/403)",
                status in (401, 403),
                f"{type(exc).__name__}: {status}",
            )
        else:
            check(
                "chat request reaches NVIDIA auth (401/403)",
                False,
                "unexpected success with an invalid key",
            )
    finally:
        await _close(provider)


async def check_web_api():
    """The setup page's "Models" button hits /api/models with the row values."""
    print("\nGET /api/models (setup editor path, network required)")
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(
            "/api/models",
            params={"type": "nvidia", "base": "", "key": "not-needed", "model": ""},
        )
        check("/api/models 200", resp.status == 200, f"got {resp.status}")
        data = await resp.json()
        models = data.get("models") or []
        check(
            "/api/models lists NVIDIA models",
            any(str(m).startswith("nvidia/") for m in models),
            f"{len(models)} models / {data.get('error', '')}",
        )


async def _close(provider):
    close = getattr(provider.client, "close", None)
    if close is not None:
        await close()


async def run():
    check_editor_mapping()
    await check_live_models()
    await check_live_chat_wiring()
    await check_web_api()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
