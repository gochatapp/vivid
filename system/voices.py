"""
TTS voice catalog for Vivid Chat.

The catalog is stored in ``settings/voices.json`` (see ``VOICES_FILE``) so the
voice list can be edited without touching code and survives restarts. Numbering
is derived from the order of the list in that file; ``.nar [n]`` selects the
voice with that number.

Provides:
- load_voices(path=None) -> List[dict]
- save_voices(voices, path=None, default_voice=None) -> List[dict]
- get_voice_by_number(number, path=None) -> Optional[dict]
- find_voice(token, path=None) -> Optional[dict]
- resolve_voice(token, path=None) -> Optional[dict]
- group_voices(voices) -> List[Tuple[str, List[dict]]]
- describe_voice(voice) -> str
- parse_speed(token) -> Optional[float]
- speed_to_rate(speed) -> str
- describe_speed(speed) -> str
- default_voice_name() -> str
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import CONFIG, SETTINGS_DIR

VOICES_FILE = SETTINGS_DIR / "voices.json"

# Fallback used when config.yaml has no DEFAULT_VOICE entry.
DEFAULT_VOICE_NAME = "en-US-AvaNeural"

# Playback speed range (a multiplier, not a percentage). edge-tts allows a much
# wider range, but anything above 1.9 gets unpleasant to listen to.
MIN_SPEED = 1.0
MAX_SPEED = 1.9
DEFAULT_SPEED = 1.0

REGION_NAMES = {
    "en-US": "United States",
    "en-GB": "United Kingdom",
    "en-AU": "Australia",
    "en-CA": "Canada",
    "en-IN": "India",
}


def _voice(name: str, locale: str, gender: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Builds one catalog entry from the short voice facts."""
    return {
        "name": name,
        "locale": locale,
        "region": REGION_NAMES.get(locale, locale),
        "gender": gender,
        "tags": list(tags or []),
    }


# Seed catalog written to settings/voices.json on first run.
DEFAULT_VOICES: List[Dict[str, Any]] = [
    _voice("en-US-AnaNeural", "en-US", "Female", ["Child"]),
    _voice("en-US-AriaNeural", "en-US", "Female"),
    _voice("en-US-AvaNeural", "en-US", "Female"),
    _voice("en-US-ChristopherNeural", "en-US", "Male"),
    _voice("en-US-EricNeural", "en-US", "Male"),
    _voice("en-US-GuyNeural", "en-US", "Male"),
    _voice("en-US-JennyNeural", "en-US", "Female"),
    _voice("en-US-MichelleNeural", "en-US", "Female"),
    _voice("en-US-RogerNeural", "en-US", "Male"),
    _voice("en-GB-LibbyNeural", "en-GB", "Female"),
    _voice("en-GB-MaisieNeural", "en-GB", "Female", ["Child"]),
    _voice("en-GB-RyanNeural", "en-GB", "Male"),
    _voice("en-GB-SoniaNeural", "en-GB", "Female"),
    _voice("en-GB-ThomasNeural", "en-GB", "Male"),
    _voice("en-AU-NatashaNeural", "en-AU", "Female"),
    _voice("en-AU-WilliamNeural", "en-AU", "Male"),
    _voice("en-CA-ClaraNeural", "en-CA", "Female"),
    _voice("en-CA-LiamNeural", "en-CA", "Male"),
    _voice("en-IN-NeerjaNeural", "en-IN", "Female"),
    _voice("en-IN-PrabhatNeural", "en-IN", "Male"),
]


def _locale_from_name(name: str) -> str:
    """Infers a locale like ``en-US`` from a voice name like ``en-US-AvaNeural``."""
    parts = name.split("-")
    if len(parts) >= 3 and 2 <= len(parts[0]) <= 3 and len(parts[1]) == 2:
        return f"{parts[0].lower()}-{parts[1].upper()}"
    return ""


def _short_name(name: str) -> str:
    """Returns the voice part of a name, e.g. ``AvaNeural`` from ``en-US-AvaNeural``."""
    parts = name.split("-")
    return parts[2] if len(parts) >= 3 else name


def _normalize(entries: Iterable[Any]) -> List[Dict[str, Any]]:
    """Accepts loose JSON entries and returns clean, renumbered voice dicts."""
    voices: List[Dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        locale = str(entry.get("locale") or _locale_from_name(name)).strip()
        tags = entry.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]

        voices.append({
            "number": len(voices) + 1,
            "name": name,
            "locale": locale,
            "region": str(entry.get("region") or REGION_NAMES.get(locale, locale or "Other")),
            "gender": str(entry.get("gender") or "Unknown"),
            "tags": [str(tag) for tag in tags if str(tag).strip()],
        })
    return voices


def _existing_default(target: Path) -> Optional[str]:
    """Reads ``default_voice`` from an existing catalog file, if available."""
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            default = str(data.get("default_voice") or "").strip()
            return default or None
    except Exception:
        pass
    return None


def save_voices(
    voices: Iterable[Any],
    path: Optional[Path] = None,
    default_voice: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Writes the catalog to disk and returns the normalized entries."""
    target = Path(path) if path else VOICES_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    normalized = _normalize(voices)
    if default_voice is None:
        default_voice = _existing_default(target) or DEFAULT_VOICE_NAME

    payload = {
        "default_voice": default_voice,
        "voices": normalized,
    }

    temp_path = target.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    temp_path.replace(target)
    return normalized


def ensure_voices_file(path: Optional[Path] = None) -> Path:
    """Creates the catalog file with seed voices when missing, empty, or unreadable."""
    target = Path(path) if path else VOICES_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        save_voices(DEFAULT_VOICES, target)
        return target

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read voice catalog {target}: {e}. Rewriting defaults.")
        save_voices(DEFAULT_VOICES, target)
        return target

    entries = data.get("voices") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not _normalize(entries):
        print(f"⚠️ No voices found in {target}. Rewriting defaults.")
        save_voices(DEFAULT_VOICES, target)
    return target


def load_voices(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads the voice catalog, seeding it with defaults when needed."""
    target = ensure_voices_file(path)
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    entries = data.get("voices") if isinstance(data, dict) else data
    voices = _normalize(entries if isinstance(entries, list) else [])
    return voices or _normalize(DEFAULT_VOICES)


def get_voice_by_number(number: int, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Returns the voice at a 1-based position, or None when out of range."""
    voices = load_voices(path)
    if 1 <= number <= len(voices):
        return voices[number - 1]
    return None


def _lookup_keys(name: str) -> set:
    """Keys a voice can be addressed by: full name, short name, short stem."""
    short = _short_name(name)
    keys = {name.lower(), short.lower()}
    if short.lower().endswith("neural"):
        keys.add(short[:-len("Neural")].lower())
    return keys


def find_voice(token: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Finds a voice by full name, short name, or short stem (case-insensitive)."""
    needle = str(token or "").strip().lower()
    if not needle:
        return None
    for voice in load_voices(path):
        if needle in _lookup_keys(voice["name"]):
            return voice
    return None


def resolve_voice(token: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Resolves a ``.nar`` argument: a number, a voice name, or a short name."""
    token = str(token or "").strip()
    if not token:
        return None
    if token.lstrip("+").isdigit():
        return get_voice_by_number(int(token), path)
    return find_voice(token, path)


def group_voices(voices: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Groups voices by region, keeping the order they first appear in."""
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    positions: Dict[str, int] = {}
    for voice in voices:
        region = voice.get("region") or voice.get("locale") or "Other"
        if region not in positions:
            positions[region] = len(groups)
            groups.append((region, []))
        groups[positions[region]][1].append(voice)
    return groups


def describe_voice(voice: Dict[str, Any]) -> str:
    """One-line description such as ``Female, Child``."""
    details = [voice.get("gender") or "Unknown"]
    details.extend(voice.get("tags") or [])
    return ", ".join(details)


def clamp_speed(speed: Any) -> float:
    """Coerces any value to a valid speed multiplier inside MIN_SPEED..MAX_SPEED."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    return max(MIN_SPEED, min(MAX_SPEED, round(value, 1)))


def parse_speed(token: Any) -> Optional[float]:
    """Maps a ``.voice [n]`` argument to a speed multiplier (1.0 - 1.9).

    Digit input is a step on the speed ladder: ``1`` is normal speed,
    ``2`` is 1.2x, ``3`` is 1.3x ... ``9`` is 1.9x, and anything above 9 is
    clamped to 1.9x. A decimal token such as ``1.5`` is the multiplier itself.

    Returns None for values that cannot be a speed (``0``, ``-2``, ``abc``).
    """
    text = str(token or "").strip().lstrip("+")
    if not text:
        return None

    if "." in text:
        try:
            speed = float(text)
        except ValueError:
            return None
        if MIN_SPEED <= speed <= MAX_SPEED:
            return round(speed, 1)
        return None

    try:
        step = int(text)
    except ValueError:
        return None

    if step < 1:
        return None
    if step == 1:
        return MIN_SPEED
    return round(min(MAX_SPEED, MIN_SPEED + step * 0.1), 1)


def speed_to_rate(speed: Optional[float] = None) -> str:
    """Converts a speed multiplier to an edge-tts rate string, e.g. 1.2 -> '+20%'."""
    value = clamp_speed(speed if speed is not None else DEFAULT_SPEED)
    percent = int(round((value - MIN_SPEED) * 100))
    return f"{percent:+d}%"


def describe_speed(speed: Optional[float] = None) -> str:
    """Human-readable speed, e.g. ``1.2x``."""
    return f"{clamp_speed(speed if speed is not None else DEFAULT_SPEED):.1f}x"


def default_voice_name() -> str:
    """Voice used when a bot has no selection: config DEFAULT_VOICE, else the seed default."""
    configured = CONFIG.get("DEFAULT_VOICE") if isinstance(CONFIG, dict) else None
    configured = str(configured).strip() if configured else ""
    return configured or DEFAULT_VOICE_NAME

