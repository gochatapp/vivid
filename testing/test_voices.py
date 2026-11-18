"""
Tests for the TTS voice catalog (settings/voices.json), `.nar` voice selection
and `.voice [n]` speed control.

Covers catalog seeding/loading, `.nar [n]` number resolution, name lookups,
region grouping, speed mapping/rate conversion, per-bot voice persistence via
status/session dicts, and ffmpeg-free edge-tts synthesis.

Run:  python testing/test_voices.py
"""

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.voices as voices_mod
import system.config as cfg
import system.handlers as handlers_mod
import system.tts as tts_mod
from system.bot_instance import BotInstance, apply_status_dict, get_bot_status_dict

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


def test_shipped_catalog():
    print("\nsettings/voices.json catalog")

    check("catalog file exists", voices_mod.VOICES_FILE.exists(), str(voices_mod.VOICES_FILE))

    voices = voices_mod.load_voices()
    check("20 voices", len(voices) == 20, f"got {len(voices)}")
    check("numbers are 1..N",
          [v["number"] for v in voices] == list(range(1, len(voices) + 1)),
          str([v["number"] for v in voices]))
    check("voice names are unique", len({v["name"] for v in voices}) == len(voices))
    check("all Edge neural voices", all(v["name"].endswith("Neural") for v in voices))
    check("locale prefix matches name",
          all(v["name"].startswith(v["locale"] + "-") for v in voices))
    check("genders are known",
          all(v["gender"] in ("Female", "Male") for v in voices))
    check("first voice is Ana (child)", voices[0]["name"] == "en-US-AnaNeural"
          and "Child" in voices[0]["tags"], str(voices[0]))

    regions = {region: len(items) for region, items in voices_mod.group_voices(voices)}
    check("5 regions", len(regions) == 5, str(regions))
    for region, expected in [
        ("United States", 9),
        ("United Kingdom", 5),
        ("Australia", 2),
        ("Canada", 2),
        ("India", 2),
    ]:
        check(f"{region} has {expected} voices", regions.get(region) == expected,
              f"got {regions.get(region)}")


def test_lookups():
    print("\n.nar number and name lookups")

    check("number 1 -> Ana", voices_mod.resolve_voice("1")["name"] == "en-US-AnaNeural")
    check("number 3 -> Ava", voices_mod.resolve_voice("3")["name"] == "en-US-AvaNeural")
    check("number 20 -> Prabhat",
          voices_mod.resolve_voice("20")["name"] == "en-IN-PrabhatNeural")
    check("full name lookup",
          voices_mod.resolve_voice("en-GB-SoniaNeural")["name"] == "en-GB-SoniaNeural")
    check("short name lookup",
          voices_mod.resolve_voice("RyanNeural")["name"] == "en-GB-RyanNeural")
    check("stem lookup",
          voices_mod.resolve_voice("ry") is None
          and voices_mod.resolve_voice("ryan")["name"] == "en-GB-RyanNeural")

    for bad in ("0", "21", "999", "-1", "abc", ""):
        check(f"rejects '{bad}'", voices_mod.resolve_voice(bad) is None)

    check("get_voice_by_number range",
          voices_mod.get_voice_by_number(9)["name"] == "en-US-RogerNeural"
          and voices_mod.get_voice_by_number(0) is None
          and voices_mod.get_voice_by_number(21) is None)

    check("describe_voice includes tags",
          voices_mod.describe_voice(voices_mod.resolve_voice("2")) == "Female"
          and voices_mod.describe_voice(voices_mod.resolve_voice("11")) == "Female, Child")


def test_autoseed():
    print("\nsettings/voices.json auto-seeding and loose formats")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "voices.json"
        seeded = voices_mod.load_voices(path)
        check("missing file is created", path.exists(), str(path))
        check("seeded with defaults", len(seeded) == len(voices_mod.DEFAULT_VOICES))

        payload = json.loads(path.read_text(encoding="utf-8"))
        check("file has default_voice", payload.get("default_voice") == voices_mod.DEFAULT_VOICE_NAME,
              str(payload.get("default_voice")))
        check("file stores numbers", [v["number"] for v in payload["voices"]][:3] == [1, 2, 3])

        path.write_text(json.dumps({"voices": [
            "en-US-AvaNeural",
            {"name": "xx-YY-TestNeural", "gender": "Female"},
        ]}), encoding="utf-8")
        custom = voices_mod.load_voices(path)
        check("string entries are normalised", custom[0]["name"] == "en-US-AvaNeural")
        check("numbers are recomputed", [v["number"] for v in custom] == [1, 2])
        check("locale inferred from name", custom[1]["locale"] == "xx-YY")
        check("unknown locale falls back to itself", custom[1]["region"] == "xx-YY")

        path.write_text(json.dumps({"voices": []}), encoding="utf-8")
        check("empty catalog is reseeded",
              len(voices_mod.load_voices(path)) == len(voices_mod.DEFAULT_VOICES))

        path.write_text("{not valid json", encoding="utf-8")
        check("invalid catalog is reseeded",
              len(voices_mod.load_voices(path)) == len(voices_mod.DEFAULT_VOICES))

        voices_mod.save_voices(voices_mod.DEFAULT_VOICES, path, default_voice="en-GB-RyanNeural")
        voices_mod.save_voices(voices_mod.DEFAULT_VOICES, path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        check("existing default_voice is preserved",
              saved.get("default_voice") == "en-GB-RyanNeural", str(saved.get("default_voice")))


def test_bot_state():
    print("\nper-bot voice selection state")

    original = cfg.CONFIG.get("DEFAULT_VOICE")

    check("default comes from config or seed",
          voices_mod.default_voice_name() == (str(original).strip() if original else voices_mod.DEFAULT_VOICE_NAME))
    check("default voice is selectable",
          voices_mod.resolve_voice(voices_mod.default_voice_name()) is not None,
          voices_mod.default_voice_name())

    first = BotInstance("voices-test-a", "token-a")
    check("new bot gets default voice", first.voice_name == voices_mod.default_voice_name(),
          first.voice_name)
    check("new bot starts at normal speed", first.voice_speed == voices_mod.DEFAULT_SPEED,
          str(first.voice_speed))

    first.voice_name = "en-GB-SoniaNeural"
    first.voice_speed = 1.4
    status = get_bot_status_dict(first)
    check("status exports voice_name", status.get("voice_name") == "en-GB-SoniaNeural",
          str(status.get("voice_name")))
    check("status exports voice_speed", status.get("voice_speed") == 1.4,
          str(status.get("voice_speed")))

    second = BotInstance("voices-test-b", "token-b")
    apply_status_dict(second, status)
    check("status import restores voice_name", second.voice_name == "en-GB-SoniaNeural",
          second.voice_name)
    check("status import restores voice_speed", second.voice_speed == 1.4,
          str(second.voice_speed))

    apply_status_dict(second, {"voice_speed": 9})
    check("imported speed is clamped", second.voice_speed == voices_mod.MAX_SPEED,
          str(second.voice_speed))
    apply_status_dict(second, {"voice_speed": "junk"})
    check("junk speed keeps normal speed", second.voice_speed == voices_mod.DEFAULT_SPEED,
          str(second.voice_speed))

    selected = voices_mod.resolve_voice("15")
    second.voice_name = selected["name"]
    check("nar number selects expected voice", second.voice_name == "en-AU-NatashaNeural",
          second.voice_name)

    try:
        cfg.CONFIG["DEFAULT_VOICE"] = "en-GB-RyanNeural"
        check("config DEFAULT_VOICE overrides seed",
              voices_mod.default_voice_name() == "en-GB-RyanNeural",
              voices_mod.default_voice_name())
    finally:
        if original is None:
            cfg.CONFIG.pop("DEFAULT_VOICE", None)
        else:
            cfg.CONFIG["DEFAULT_VOICE"] = original


def test_handlers():
    print("\n.nar handler behaviour")

    bot = types.SimpleNamespace(
        name="voices-bot",
        voice_name="en-US-AvaNeural",
        voice_mode=False,
        voice_speed=1.0,
        chat_history=[],
        saves=0,
    )
    bot.save_active_context_and_status = lambda: setattr(bot, "saves", bot.saves + 1)

    replies = []
    transients = []

    async def fake_reply_and_log(update, text, is_command=True, bot=None):
        replies.append(text)

    async def fake_reply_transient(update, context, text, delay=5):
        transients.append(text)

    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=42),
        message=types.SimpleNamespace(text=".nar", message_id=1),
    )

    def ctx(args):
        return types.SimpleNamespace(args=args)

    with mock.patch.object(handlers_mod, "get_bot", return_value=bot), \
            mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "reply_and_log", fake_reply_and_log), \
            mock.patch.object(handlers_mod, "reply_transient", fake_reply_transient):
        asyncio.run(handlers_mod.nar_command(update, ctx([])))
        listing = replies[-1] if replies else ""
        check("`.nar` lists voices", "TTS Voices (Bot: voices-bot)" in listing, listing[:60])
        check("listing groups by region", "**United States (en-US)**" in listing)
        check("listing marks current voice", "3. `en-US-AvaNeural` • Female ✅" in listing)
        check("listing fits Telegram limit", 0 < len(listing) < 4096, f"{len(listing)} chars")
        check("listing mentions .nar usage", "`.nar [n]`" in listing)
        check("listing shows current speed", "now `1.0x`" in listing, listing[-160:])

        before = bot.saves
        asyncio.run(handlers_mod.nar_command(update, ctx(["5"])))
        check("`.nar 5` selects Eric", bot.voice_name == "en-US-EricNeural", bot.voice_name)
        check("selection is persisted", bot.saves == before + 1, str(bot.saves))
        check("selection confirms voice", "en-US-EricNeural" in transients[-1], str(transients[-1:]))
        check("off mode hints .voice on", ".voice on" in transients[-1], str(transients[-1:]))

        asyncio.run(handlers_mod.nar_command(update, ctx(["sonia"])))
        check("`.nar sonia` selects Sonia", bot.voice_name == "en-GB-SoniaNeural", bot.voice_name)

        asyncio.run(handlers_mod.nar_command(update, ctx(["99"])))
        check("out-of-range number is rejected", "out of range" in replies[-1], replies[-1])
        check("out-of-range keeps voice", bot.voice_name == "en-GB-SoniaNeural", bot.voice_name)

        asyncio.run(handlers_mod.nar_command(update, ctx(["zzz"])))
        check("unknown voice is rejected", "Unknown voice" in replies[-1], replies[-1])

        bot.voice_mode = True
        transients.clear()
        asyncio.run(handlers_mod.nar_command(update, ctx(["1"])))
        check("on mode has no hint", ".voice on" not in transients[-1], str(transients[-1:]))

        asyncio.run(handlers_mod.narrator_command(update, ctx([])))
        check("`.narrator` lists voices", "TTS Voices (Bot: voices-bot)" in replies[-1])
        check("`.narrator` marks new current voice",
              "1. `en-US-AnaNeural` • Female, Child ✅" in replies[-1])

        # `.voice [n]` speed control
        bot.voice_mode = True
        before = bot.saves
        asyncio.run(handlers_mod.toggle_voice(update, ctx(["2"])))
        check("`.voice 2` sets 1.2x", bot.voice_speed == 1.2, str(bot.voice_speed))
        check("speed change is persisted", bot.saves == before + 1, str(bot.saves))
        check("speed confirmation is shown", "1.2x" in transients[-1], str(transients[-1:]))
        check("no off hint while voice is on", ".voice on" not in transients[-1],
              str(transients[-1:]))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["1"])))
        check("`.voice 1` is normal speed", bot.voice_speed == 1.0, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["9"])))
        check("`.voice 9` is 1.9x", bot.voice_speed == 1.9, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["10"])))
        check("`.voice 10` clamps to 1.9x", bot.voice_speed == 1.9, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["1.5"])))
        check("`.voice 1.5` is literal 1.5x", bot.voice_speed == 1.5, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["0"])))
        check("rejects `.voice 0`", "Unknown voice setting" in replies[-1], replies[-1])
        check("rejected speed keeps 1.5x", bot.voice_speed == 1.5, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["abc"])))
        check("rejects `.voice abc`", "Unknown voice setting" in replies[-1], replies[-1])

        asyncio.run(handlers_mod.toggle_voice(update, ctx(["off"])))
        check("`.voice off` still disables", bot.voice_mode is False)
        transients.clear()
        asyncio.run(handlers_mod.toggle_voice(update, ctx(["3"])))
        check("speed hint when voice is off", ".voice on" in transients[-1], str(transients[-1:]))
        check("`.voice 3` is 1.3x", bot.voice_speed == 1.3, str(bot.voice_speed))

        asyncio.run(handlers_mod.toggle_voice(update, ctx([])))
        check("bare `.voice` toggles back on", bot.voice_mode is True)
        asyncio.run(handlers_mod.toggle_voice(update, ctx(["on"])))
        check("`.voice on` enables", bot.voice_mode is True)


def test_speed_mapping():
    print("\n.voice speed mapping")

    expected = {
        "1": 1.0,
        "2": 1.2,
        "3": 1.3,
        "5": 1.5,
        "8": 1.8,
        "9": 1.9,
        "10": 1.9,
        "11": 1.9,
    }
    for token, speed in expected.items():
        actual = voices_mod.parse_speed(token)
        check(f"`.voice {token}` -> {speed}", actual == speed, str(actual))

    for token in ("0", "-2", "abc", "", "0.9", "2.5"):
        actual = voices_mod.parse_speed(token)
        check(f"rejects speed '{token}'", actual is None, str(actual))

    check("decimal speed is literal", voices_mod.parse_speed("1.5") == 1.5,
          str(voices_mod.parse_speed("1.5")))
    check("rate for 1.0 is +0%", voices_mod.speed_to_rate(1.0) == "+0%",
          voices_mod.speed_to_rate(1.0))
    check("rate for 1.2 is +20%", voices_mod.speed_to_rate(1.2) == "+20%",
          voices_mod.speed_to_rate(1.2))
    check("rate for 1.9 is +90%", voices_mod.speed_to_rate(1.9) == "+90%",
          voices_mod.speed_to_rate(1.9))
    check("rate clamps above max", voices_mod.speed_to_rate(3.0) == "+90%",
          voices_mod.speed_to_rate(3.0))
    check("describe_speed formats multiplier", voices_mod.describe_speed(1.2) == "1.2x",
          voices_mod.describe_speed(1.2))


def test_tts_rate():
    print("\nsynthesize_speech rate wiring")

    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            captured["text"] = text
            captured["voice"] = voice
            captured["rate"] = kwargs.get("rate")

        async def stream(self):
            yield {"type": "audio", "data": b"mp3-bytes"}

    fake_edge = types.SimpleNamespace(Communicate=FakeCommunicate)
    with mock.patch.object(tts_mod, "edge_tts", fake_edge), \
            mock.patch.object(tts_mod, "HAS_EDGE_TTS", True):
        audio = asyncio.run(
            tts_mod.synthesize_speech("**hi** there", voice="en-GB-SoniaNeural", speed=1.2)
        )
        check("audio bytes returned", audio == b"mp3-bytes", repr(audio))
        check("voice passed through", captured.get("voice") == "en-GB-SoniaNeural",
              str(captured.get("voice")))
        check("rate reflects speed", captured.get("rate") == "+20%", str(captured.get("rate")))
        check("markdown stripped before speech", captured.get("text") == "hi there",
              str(captured.get("text")))

        asyncio.run(tts_mod.synthesize_speech("hello", speed=1.9))
        check("max speed rate", captured.get("rate") == "+90%", str(captured.get("rate")))

        asyncio.run(tts_mod.synthesize_speech("hello"))
        check("default speed rate", captured.get("rate") == "+0%", str(captured.get("rate")))
        check("default voice used", captured.get("voice") == voices_mod.default_voice_name(),
              str(captured.get("voice")))


def test_voice_reply():
    print("\nvoice note payload")

    class FakeMessage:
        def __init__(self):
            self.kwargs = None

        async def reply_voice(self, **kwargs):
            self.kwargs = kwargs
            return types.SimpleNamespace(message_id=99)

    update = types.SimpleNamespace(message=FakeMessage())
    asyncio.run(handlers_mod.send_voice_reply(update, b"ID3fake-audio"))

    kwargs = update.message.kwargs or {}
    check("voice note is sent", "voice" in kwargs, str(kwargs.keys()))
    check("voice note has no caption", "caption" not in kwargs, str(kwargs.keys()))

    payload = kwargs.get("voice")
    check("voice payload is an InputFile",
          payload is not None and getattr(payload, "filename", None) == "voice.mp3",
          str(getattr(payload, "filename", payload)))
    content = getattr(payload, "input_file_content", None)
    data = content.read() if hasattr(content, "read") else content
    check("voice payload carries audio bytes", data == b"ID3fake-audio", repr(data))


def test_tts_without_ffmpeg():
    print("\nTTS runs without ffmpeg")

    source = Path(tts_mod.__file__).read_text(encoding="utf-8").lower()
    check("no ffmpeg PATH lookup in tts module",
          "shutil.which" not in source and "ffmpeg not found" not in source)
    check("no ffmpeg transcode call in tts module",
          "'ffmpeg'" not in source and '"ffmpeg"' not in source)
    check("no local SAPI fallbacks (pyttsx3/win32com)",
          "pyttsx3" not in source and "win32com" not in source)

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            self.text = text
            self.voice = voice
            self.kwargs = kwargs

        async def stream(self):
            yield {"type": "audio", "data": b"ID3-edge-only"}

    with mock.patch.object(tts_mod, "edge_tts",
                           types.SimpleNamespace(Communicate=FakeCommunicate)), \
            mock.patch.object(tts_mod, "HAS_EDGE_TTS", True), \
            mock.patch("shutil.which", return_value=None):
        audio = asyncio.run(tts_mod.synthesize_speech("**hi** there", speed=1.2))
        check("edge-tts produced audio with no external binaries",
              audio == b"ID3-edge-only", repr(audio))

    with mock.patch.object(tts_mod, "edge_tts", None), \
            mock.patch.object(tts_mod, "HAS_EDGE_TTS", False):
        audio = asyncio.run(tts_mod.synthesize_speech("hello"))
        check("missing edge-tts returns None instead of crashing",
              audio is None, repr(audio))


def main():
    test_shipped_catalog()
    test_lookups()
    test_autoseed()
    test_bot_state()
    test_handlers()
    test_speed_mapping()
    test_tts_rate()
    test_tts_without_ffmpeg()
    test_voice_reply()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

