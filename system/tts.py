"""
Telegram TTS module for Vivid Chat.

Provides:
- strip_markdown(text) — strips markdown formatting so TTS reads clean text
- async synthesize_speech(text, voice=None, speed=None) -> Optional[bytes] — returns MP3 audio bytes
"""

import io
import re
from typing import Optional

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    edge_tts = None
    HAS_EDGE_TTS = False

from .voices import DEFAULT_SPEED, default_voice_name, speed_to_rate

DEFAULT_VOICE = default_voice_name()


def strip_markdown(text: str) -> str:
    """Strip common Markdown formatting so TTS engines read clean text.

    Avoids reading asterisks, underscores, backticks, links, etc. as spoken characters.
    """
    if not text:
        return ""

    # Remove bold/italic markers: **text**, *text*, __text__, _text_
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)

    # Remove inline code: `text`
    text = re.sub(r'`([^`]*)`', r'\1', text)

    # Remove images: ![alt](url) -> alt
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)

    # Remove links: [text](url) -> text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)

    # Remove headers: # text, ## text, etc.
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text)

    # Remove blockquotes: > text
    text = re.sub(r'(?m)^\s{0,3}>\s?', '', text)

    # Remove horizontal rules: ---, ***, ___
    text = re.sub(r'(?m)^\s{0,3}((-{3,})|(\*{3,})|(_{3,}))\s*$', '', text)

    # Remove list markers: - item, * item, + item, 1. item
    text = re.sub(r'(?m)^\s{0,3}([-*+]|\d+\.)\s+', '', text)

    # Remove strikethrough: ~~text~~
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # Clean up any leftover markdown characters
    for ch in ['**', '__', '*', '_', '`', '~~']:
        text = text.replace(ch, '')

    # Collapse multiple spaces and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def synthesize_speech(
    text: str,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
) -> Optional[bytes]:
    """Convert text to MP3 bytes using edge-tts.

    ``voice`` defaults to the catalog default and ``speed`` (a multiplier such
    as 1.2) defaults to normal 1.0x playback.
    Returns MP3 audio bytes, or None if synthesis fails or edge-tts is unavailable.
    """
    if not HAS_EDGE_TTS or edge_tts is None:
        print("⚠️ TTS warning: edge-tts is not installed or available.")
        return None

    clean_text = strip_markdown(text)
    if not clean_text:
        return None

    selected_voice = voice or default_voice_name()
    rate = speed_to_rate(speed if speed is not None else DEFAULT_SPEED)
    try:
        comm = edge_tts.Communicate(clean_text, selected_voice, rate=rate)
        buf = io.BytesIO()
        async for chunk in comm.stream():
            # Handle both object and dict representations of chunks
            if isinstance(chunk, dict):
                if chunk.get('type') == "audio":
                    buf.write(chunk.get('data', b''))
            else:
                if getattr(chunk, 'type', None) == "audio":
                    buf.write(getattr(chunk, 'data', b''))

        mp3_bytes = buf.getvalue()
        return mp3_bytes if mp3_bytes else None
    except Exception as e:
        print(f"⚠️ TTS synthesis error: {e}")
        return None
