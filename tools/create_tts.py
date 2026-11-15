#!/usr/bin/env python3
import os

tts_content = '''\"\"\"
Telegram TTS module for Vivid Chat.

Provides:
- strip_markdown(text) — strips markdown formatting so TTS reads clean text
- async synthesize_speech(text, voice="en-US-AvaNeural") -> bytes — returns MP3 audio bytes
\"\"\"

import io
import re
from typing import Optional

from edge_tts import Communicate


def strip_markdown(text: str) -> str:
    \"\"\"Strip common Markdown formatting so TTS engines read clean text.

    Port of the Go stripMarkdown from test_tts to avoid reading asterisks,
    underscores, backticks, links, etc. as spoken characters.
    \"\"\"
    if not text:
        return text

    # Remove bold/italic markers: **text**, *text*, __text__, _text_
    text = re.sub(r'(\*\*|__)(.*?)\\1', r'\\2', text)

    # Remove inline code: \`text\`
    text = re.sub(r'\\\`([^\\\`]*)\\\`, r'\\1', text)

    # Remove images: ![alt](url) -> alt (or just remove)
    text = re.sub(r'!\\\\[([^\\\\]*)\\\\]\\\\([^)]*\\\\), r'\\\\1', text)

    # Remove links: [text](url) -> text
    text = re.sub(r'\\\\[([^\\\\]*)\\\\]\\\\([^)]*\\\\), r'\\\\1', text)

    # Remove headers: # text, ## text, etc.
    text = re.sub(r'(?m)^\\s{0,3}#{1,6}\\s+', '', text)

    # Remove blockquotes: > text
    text = re.sub(r'(?m)^\\s{0,3}>\\s?', '', text)

    # Remove horizontal rules: ---, ***, ___
    text = re.sub(r'(?m)^\\s{0,3}((-{3,})|(\\*{3,})|(_{3,}))\\s*$', '', text)

    # Remove list markers: - item, * item, + item, 1. item
    text = re.sub(r'(?m)^\\s{0,3}([-*+]|\\\\d+\\\\.)\\s+', '', text)

    # Remove strikethrough: ~~text~~
    text = re.sub(r'~~(.*?)~~', r'\\1', text)

    # Clean up any leftover markdown symbols and collapse multiple spaces
    text = text.replace('\\\\*\\\\*', '').replace('\\\\__', '').replace('\\\\*', '').replace('\\\\_', '')
    text = text.replace('\\\\`', '').replace('\\\\~\\\, ' '')

    # Collapse multiple spaces and trim
    text = re.sub(r'\\\\s+', ' ', text).strip()

    return text


async def synthesize_speech(
    text: str,
    voice: str = \"en-US-AvaNeural\",
) -> Optional[bytes]:
    \"\"\"Convert text to MP3 bytes using edge-tts.

    Returns MP3 audio bytes, or None if synthesis fails.
    The voice parameter is an Edge TTS voice name, e.g.:
      - en-US-AvaNeural (English, US, female)
      - en-GB-SoniaNeural (English, UK, female)
    \"\"\"
    try:
        # Clean text of markdown first
        clean_text = strip_markdown(text)

        comm = Communicate(clean_text, voice)

        # Collect audio chunks into a buffer
        buf = io.BytesIO()

        async for chunk in comm.stream():
            if chunk.type == \"audio\":
                buf.write(chunk.data)

        mp3_bytes = buf.getvalue()
        return mp3_bytes if mp3_bytes else None

    except Exception as e:
        print(f\"\\u26a0\\ufe0f TTS synthesis error: {e}\")
        return None
'''

with open('system\\tts.py', 'w', encoding='utf-8') as f:
    f.write(tts_content)

print('Created tts.py')