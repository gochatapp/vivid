"""
Tests for the Google Gemini provider (``type: gemini`` in config.yaml).

The provider talks to Google's SDK directly (``google-genai``), not to an
OpenAI-compatible endpoint. ``google-genai`` renamed the async entry point:
``client.aio.models.generate_content_async`` existed in early releases and was
removed later in favour of ``client.aio.models.generate_content``. Using the
stale name produced::

    Error generating response: 'AsyncModels' object has no attribute
    'generate_content_async'

These checks are offline (fake clients) except the optional live section, which
runs only when ``VIVID_GEMINI_API_KEY`` is set in the environment.

Run:  python testing/test_gemini_provider.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.genai import types
from google.genai.models import AsyncModels

from system.providers import GeminiProvider, create_provider
from system.web_server import _EDITOR_TYPES

PASS = 0
FAIL = 0

MODEL = "gemini-2.5-flash"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeAsyncModels:
    """AsyncModels look-alike exposing only ``generate_content``."""

    def __init__(self):
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse("pong")


class _LegacyAsyncModels:
    """AsyncModels look-alike from older google-genai releases."""

    def __init__(self):
        self.calls = []

    async def generate_content_async(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse("pong-legacy")


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, models):
        self.aio = _FakeAio(models)


class _FakeModel:
    def __init__(self, name, actions):
        self.name = name
        self.supported_actions = actions


class _FakePager:
    def __init__(self, models):
        self._models = models

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._models):
            raise StopAsyncIteration
        item = self._models[self._idx]
        self._idx += 1
        return item


class _FakeListModels:
    def __init__(self, models):
        self._models = models

    async def list(self, **kwargs):
        return _FakePager(self._models)


def _provider(async_models):
    provider = GeminiProvider(
        "google-test", {"type": "gemini", "api_key": "test-key", "model": MODEL}
    )
    provider.client = _FakeClient(async_models)
    return provider


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_construction():
    print("\nProvider construction")
    try:
        GeminiProvider("google-no-key", {"type": "gemini"})
    except ValueError as exc:
        check("missing api_key raises ValueError", "api_key" in str(exc), repr(exc))
    except Exception as exc:  # pragma: no cover - unexpected failure path
        check("missing api_key raises ValueError", False, repr(exc))
    else:
        check("missing api_key raises ValueError", False, "no error raised")

    provider = GeminiProvider(
        "google-test", {"type": "gemini", "api_key": "test-key", "model": MODEL}
    )
    check("default model comes from config", provider.model_name == MODEL, provider.model_name)
    check(
        "factory returns GeminiProvider for type=gemini",
        isinstance(create_provider("google", {"type": "gemini", "api_key": "k"}), GeminiProvider),
    )


def check_model_resolution():
    print("\nModel id normalisation")
    provider = GeminiProvider(
        "google-test", {"type": "gemini", "api_key": "test-key", "model": MODEL}
    )
    check("plain id unchanged", provider._resolve_model(MODEL) == MODEL, provider._resolve_model(MODEL))
    check(
        "google/ prefix stripped",
        provider._resolve_model("google/" + MODEL) == MODEL,
        provider._resolve_model("google/" + MODEL),
    )
    check(
        "models/ prefix stripped",
        provider._resolve_model("models/" + MODEL) == MODEL,
        provider._resolve_model("models/" + MODEL),
    )
    check(
        "google/models/ prefix stripped",
        provider._resolve_model("google/models/" + MODEL) == MODEL,
        provider._resolve_model("google/models/" + MODEL),
    )


async def check_chat_uses_installed_method():
    print("\nChat wiring (offline fake client, generate_content only)")
    fake = _FakeAsyncModels()
    provider = _provider(fake)
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
        {"role": "user", "content": "ping again"},
    ]
    text = await provider.chat("google/" + MODEL, messages, {"temperature": 0.5, "top_p": 0.9})
    check("chat returns response text", text == "pong", repr(text))
    check("generate_content called once", len(fake.calls) == 1, len(fake.calls))
    call = fake.calls[0] if fake.calls else {}
    check("model id normalised before the SDK call", call.get("model") == MODEL, call.get("model"))
    contents = call.get("contents") or []
    check(
        "system prompt moved to config, not contents",
        all(c.get("role") != "system" for c in contents),
        contents,
    )
    check(
        "assistant messages map to role=model",
        [c.get("role") for c in contents] == ["user", "model", "user"],
        [c.get("role") for c in contents],
    )
    cfg = call.get("config")
    check(
        "generate config carries system instruction",
        getattr(cfg, "system_instruction", None) == "You are terse.",
        getattr(cfg, "system_instruction", None),
    )
    check(
        "generate config carries temperature",
        getattr(cfg, "temperature", None) == 0.5,
        getattr(cfg, "temperature", None),
    )


async def check_chat_legacy_sdk():
    print("\nChat wiring (older SDK naming)")
    fake = _LegacyAsyncModels()
    provider = _provider(fake)
    text = await provider.chat(MODEL, [{"role": "user", "content": "ping"}], {})
    check("falls back to generate_content_async when present", text == "pong-legacy", repr(text))
    check("legacy method called once", len(fake.calls) == 1, len(fake.calls))


async def check_list_models():
    print("\nModel listing (offline fake client)")
    fake = _FakeListModels([
        _FakeModel("models/" + MODEL, ["generateContent", "countTokens"]),
        _FakeModel("models/gemini-embedding-001", ["embedContent"]),
        _FakeModel("publishers/google/models/gemini-2.5-pro", ["generateContent"]),
    ])
    provider = _provider(fake)
    models = await provider.list_models()
    check("only generateContent models are returned", models == [MODEL, "gemini-2.5-pro"], models)

    class _BrokenListModels:
        async def list(self, **kwargs):
            raise RuntimeError("boom")

    provider = _provider(_BrokenListModels())
    models = await provider.list_models()
    check("listing failure falls back to configured model", models == [MODEL], models)


def check_sdk_surface():
    print("\nInstalled google-genai surface")
    check("AsyncModels exposes generate_content", hasattr(AsyncModels, "generate_content"))
    fields = set(types.Model.model_fields)
    check(
        "Model schema exposes supported_actions (or its legacy name)",
        "supported_actions" in fields or "supported_generation_methods" in fields,
        sorted(fields & {"supported_actions", "supported_generation_methods"}),
    )
    provider = GeminiProvider("google-test", {"type": "gemini", "api_key": "test-key"})
    async_models = provider.client.aio.models
    check(
        "bindable async method exists on a real client",
        callable(getattr(async_models, "generate_content", None)),
        type(async_models).__name__,
    )


def check_editor_mapping():
    print("\nSetup editor mapping")
    check(
        "google preset -> gemini + Generative Language base",
        _EDITOR_TYPES.get("google") == ("gemini", "https://generativelanguage.googleapis.com/"),
        _EDITOR_TYPES.get("google"),
    )


async def check_live_chat():
    api_key = os.environ.get("VIVID_GEMINI_API_KEY", "").strip()
    if not api_key:
        print("\nLive chat skipped (set VIVID_GEMINI_API_KEY to enable)")
        return

    # Model ids are retired over time; allow the live check to follow config.yaml.
    live_model = os.environ.get("VIVID_GEMINI_MODEL", "").strip() or MODEL

    print(f"\nLive chat with {live_model} (network + valid key required)")
    provider = GeminiProvider(
        "google-live", {"type": "gemini", "api_key": api_key, "model": live_model}
    )
    try:
        try:
            text = await provider.chat(
                live_model,
                [{"role": "user", "content": "Reply with the single word: pong"}],
                {"temperature": 0.0},
            )
        except Exception as exc:  # network down / bad key
            check("live chat succeeds", False, f"{type(exc).__name__}: {exc}")
            return
        check("live chat returns text", bool(text.strip()), repr(text))
    finally:
        close = getattr(provider.client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


async def run():
    check_construction()
    check_model_resolution()
    await check_chat_uses_installed_method()
    await check_chat_legacy_sdk()
    await check_list_models()
    check_sdk_surface()
    check_editor_mapping()
    await check_live_chat()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)

