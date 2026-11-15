"""
Verifies `.mf` / `.modelsfiltered` works on every provider that can list models,
not just Ollama, and degrades gracefully when a provider cannot list models.

Run:  python testing/test_mf_command.py
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.handlers as handlers_mod  # noqa: E402

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


class FakeProvider:
    """Provider stub: lists models, tracks whether model management was consulted."""

    def __init__(self, models, error=None, display="OpenRouter", management=False):
        self.models = models
        self.error = error
        self.display = display
        self.management = management
        self.management_checks = 0
        self.list_calls = 0

    async def list_models(self):
        self.list_calls += 1
        if self.error:
            raise self.error
        return list(self.models)

    def supports_model_management(self):
        self.management_checks += 1
        return self.management

    def get_display_name(self):
        return self.display


class FakeBot:
    def __init__(self, provider_name="openrouter", current_model="b"):
        self.name = "TestBot"
        self.current_provider = provider_name
        self.current_model = current_model
        self.filtered_models = []
        self.saves = 0

    def save_active_context_and_status(self):
        self.saves += 1


def run_mf(bot, provider_map, args):
    """Runs mf_command with mocked IO and returns (logged_texts, transient_texts)."""
    logged = []
    transient = []

    async def fake_reply_and_log(update, text, is_command=True, bot=None):
        logged.append(text)

    async def fake_reply_transient(update, context, text, delay=5):
        transient.append(text)

    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=42),
        message=types.SimpleNamespace(message_id=1),
    )
    context = types.SimpleNamespace(args=list(args))

    with mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "get_bot", return_value=bot), \
            mock.patch.object(handlers_mod, "providers", provider_map), \
            mock.patch.object(handlers_mod, "reply_and_log", fake_reply_and_log), \
            mock.patch.object(handlers_mod, "reply_transient", fake_reply_transient):
        asyncio.run(handlers_mod.mf_command(update, context))

    return logged, transient



def main():
    print("\n.mf on a non-Ollama provider")
    provider = FakeProvider(["openai/gpt-4o", "openai/gpt-4o-mini", "anthropic/claude-3"])
    bot = FakeBot()
    logged, transient = run_mf(bot, {"openrouter": provider}, ["gpt"])
    check("filter listing is sent", len(logged) == 1, repr(logged))
    text = logged[0] if logged else ""
    check("lists both matching models", "openai/gpt-4o" in text and "openai/gpt-4o-mini" in text)
    check("skips non-matching models", "anthropic/claude-3" not in text)
    check("stores the filtered list", bot.filtered_models == ["openai/gpt-4o", "openai/gpt-4o-mini"],
          repr(bot.filtered_models))
    check("does not require model management", provider.management_checks == 0,
          f"checked {provider.management_checks}x")
    check("no transient message for filtering", transient == [])

    print("\n.mf [n] selects from the stored filtered list")
    provider = FakeProvider(["a", "b", "c"])
    bot = FakeBot(current_model="a")
    bot.filtered_models = ["b", "c"]
    logged, transient = run_mf(bot, {"openrouter": provider}, ["2"])
    check("model switched to filtered entry", bot.current_model == "c", bot.current_model)
    check("switch is confirmed", transient and "c" in transient[0], repr(transient))
    check("status saved", bot.saves == 1, str(bot.saves))
    check("filtered list untouched", bot.filtered_models == ["b", "c"], repr(bot.filtered_models))

    print("\n.mf [n] falls back to every listed model when no filter is stored")
    provider = FakeProvider(["c", "a", "b"])
    bot = FakeBot(current_model="a")
    logged, transient = run_mf(bot, {"openrouter": provider}, ["2"])
    check("sorted list is used for the index", bot.current_model == "b", bot.current_model)
    check("no filtered list invented", bot.filtered_models == [], repr(bot.filtered_models))

    print("\n.mf next / .mf prev cycle the pool")
    provider = FakeProvider(["a", "b", "c"])
    bot = FakeBot(current_model="a")
    bot.filtered_models = ["a", "b", "c"]
    run_mf(bot, {"openrouter": provider}, ["next"])
    check("next advances", bot.current_model == "b", bot.current_model)
    run_mf(bot, {"openrouter": provider}, ["prev"])
    check("prev rewinds", bot.current_model == "a", bot.current_model)
    bot.current_model = "c"
    run_mf(bot, {"openrouter": provider}, ["next"])
    check("next wraps around", bot.current_model == "a", bot.current_model)
    bot.current_model = "a"
    run_mf(bot, {"openrouter": provider}, ["prev"])
    check("prev wraps around", bot.current_model == "c", bot.current_model)

    print("\n.mf shows the stored filtered list with no arguments")
    provider = FakeProvider(["a", "b"])
    bot = FakeBot()
    bot.filtered_models = ["x-large", "x-small"]
    logged, transient = run_mf(bot, {"openrouter": provider}, [])
    text = logged[0] if logged else ""
    check("filtered list is shown", "x-large" in text and "x-small" in text, repr(text))
    check("no provider call needed to re-display", provider.list_calls == 0, str(provider.list_calls))

    print("\n.mf reports providers that cannot list models")
    provider = FakeProvider([])
    bot = FakeBot(provider_name="gemini", current_model="keep-me")
    logged, transient = run_mf(bot, {"gemini": provider}, ["flash"])
    text = logged[0] if logged else ""
    check("explains the provider has no model list", "does not expose a model list" in text, repr(text))
    check("model is not changed", bot.current_model == "keep-me", bot.current_model)

    print("\n.mf reports listing errors without raising")
    provider = FakeProvider(["a"], error=RuntimeError("boom"))
    bot = FakeBot(current_model="keep-me")
    logged, transient = run_mf(bot, {"openrouter": provider}, ["next"])
    text = logged[0] if logged else ""
    check("error is reported", "Failed to list models" in text and "boom" in text, repr(text))
    check("model is not changed", bot.current_model == "keep-me", bot.current_model)

    print("\n.mf rejection paths")
    provider = FakeProvider(["a", "b"])
    bot = FakeBot()
    logged, transient = run_mf(bot, {"openrouter": provider}, ["9"])
    check("invalid index is rejected", logged and "Invalid model number" in logged[0], repr(logged))
    logged, transient = run_mf(bot, {"openrouter": provider}, ["zzz"])
    check("unmatched filter is reported",
          logged and "No models found matching" in logged[0], repr(logged))

    print("\n.mf still works on Ollama providers")
    provider = FakeProvider(["llama3.2:3b", "qwen2.5:7b"], display="Ollama", management=True)
    bot = FakeBot(provider_name="ollama", current_model="llama3.2:3b")
    logged, transient = run_mf(bot, {"ollama": provider}, ["qwen"])
    check("Ollama filtering works", bot.filtered_models == ["qwen2.5:7b"], repr(bot.filtered_models))

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
