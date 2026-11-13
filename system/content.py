"""
Content loading helpers for roles, personas, characters, story elements, and scenes.
Extracted from vivid.py content functions.
"""

import re
from pathlib import Path
from typing import Optional

from .config import (
    ROLES_DIR,
    CHAT_PERSONAS_DIR,
    STORIES_DIR,
    BASE_DIR,
)
from .bot_instance import get_current_default_bot


def get_role_content(role_name: str) -> str:
    """
    Reads the persona/role definition from a .txt file.
    Returns a fallback string if the role file is missing.
    Supports both local files and Gist-backed files (name!gist_id.txt).
    """
    role_file = ROLES_DIR / f"{role_name}.txt"
    if role_file.exists():
        content = role_file.read_text(encoding='utf-8').strip()
        if not content:
            return "You are a helpful AI assistant."
        return content
    return "You are a helpful AI assistant."


def get_persona_content(persona_name: str) -> str:
    """
    Reads the persona definition from a .txt file in the personas directory.
    Returns empty string if the persona file is missing.
    """
    if not persona_name:
        return ""
    persona_file = CHAT_PERSONAS_DIR / f"{persona_name}.txt"
    if persona_file.exists():
        content = persona_file.read_text(encoding='utf-8').strip()
        return content
    return ""


def get_story_characters_dir(active_story: str = "") -> Optional[Path]:
    """Returns the characters directory for the active story."""
    story = active_story or getattr(get_current_default_bot(), 'active_story', "")
    if story:
        return STORIES_DIR / story / "characters"
    return None


def get_story_elements_dir(active_story: str = "") -> Optional[Path]:
    """Returns the story elements directory for the active story."""
    story = active_story or getattr(get_current_default_bot(), 'active_story', "")
    if story:
        return STORIES_DIR / story / "storyelements"
    return None


def get_character_content(char_name: str, active_story: str = "") -> str:
    """
    Reads the character description from a .txt file in the active story's characters directory.
    Only works in story mode with an active story selected.
    """
    chars_dir = get_story_characters_dir(active_story)
    if not chars_dir or not chars_dir.exists():
        return ""
    char_file = chars_dir / f"{char_name}.txt"
    if char_file.exists():
        return char_file.read_text(encoding='utf-8').strip()
    return ""


def get_element_content(element_name: str, active_story: str = "") -> str:
    """
    Reads the story element (location, item, etc.) description from a .txt file
    in the active story's storyelements directory.
    Only works in story mode with an active story selected.
    """
    elements_dir = get_story_elements_dir(active_story)
    if not elements_dir or not elements_dir.exists():
        return ""
    element_file = elements_dir / f"{element_name}.txt"
    if element_file.exists():
        return element_file.read_text(encoding='utf-8').strip()
    return ""


def get_scene_content(scene_name: str) -> str:
    """Reads legacy scene description from scenes directory."""
    scene_file = BASE_DIR / "scenes" / f"{scene_name}.txt"
    if scene_file.exists():
        return scene_file.read_text(encoding='utf-8').strip()
    return ""


# Backward compatibility aliases (these take no args, use default bot's active_story)
def get_character_content_legacy(char_name: str) -> str:
    """
    Reads the character description from a .txt file in the active story's characters directory.
    Only works in story mode with an active story selected.
    """
    chars_dir = get_story_characters_dir()
    if not chars_dir or not chars_dir.exists():
        return ""
    char_file = chars_dir / f"{char_name}.txt"
    if char_file.exists():
        return char_file.read_text(encoding='utf-8').strip()
    return ""


def get_element_content_legacy(element_name: str) -> str:
    """
    Reads the story element (location, item, etc.) description from a .txt file
    in the active story's storyelements directory.
    Only works in story mode with an active story selected.
    """
    elements_dir = get_story_elements_dir()
    if not elements_dir or not elements_dir.exists():
        return ""
    element_file = elements_dir / f"{element_name}.txt"
    if element_file.exists():
        return element_file.read_text(encoding='utf-8').strip()
    return ""