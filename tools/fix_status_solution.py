#!/usr/bin/env python3
"""
Fix the .status display to be mode-aware.
Only chats can have roles and personas; only stories can have characters and storyelements (scenes).
"""
import re

with open('system/handlers.py', 'r', encoding='utf8') as f:
    content = f.read()

# The old status_text block - lines 1595-1621 in the original file
old_block = '''    status_text = (\n\n        f\"📊 **Status (Bot: {bot.name})**\\n---\\n\"\n\n        f\"🌐 Provider: `{display}`\\n\"\n\n        f\"🧠 Model: `{bot.current_model}`\\n\"\n\n        f\"🎭 Role: `{display_name}`\\n\"\n\n        f\"👤 Persona: `{persona_display}`\\n\"\n\n        f\"📖 Mode: `{bot.current_mode}`\\n\"\n\n        f\"📝 Trace Mode: `{'ON' if bot.trace_mode else 'OFF'}`\\n\"\n\n        f\"😴 Lazy Mode: `{'ON' if bot.lazy_mode else 'OFF'}`\\n\"\n\n        f\"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\\n\"\n\n        f\"🎬 Active Scenes: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\\n\"\n\n        f\"💾 Context: `{len(bot.chat_history)}` msgs `{size_kb}KB` max `{bot.context_limit}`\\n\"\n\n        f\"📏 Length: `{f'~{bot.response_length} words' if bot.response_length else 'None'}`\\n\"\n\n        f\"🧠 LLM Ctx: `{bot.generation_options['num_ctx']}`\"\n\n    )'''

# New mode-aware status text builder
new_block = '''# Mode-aware status display
    # Only chats can have roles and personas; only stories can have characters and storyelements (scenes)
    is_story = bot.current_mode == "story"
    is_chat = bot.current_mode == "chat"

    # Build status parts based on mode
    status_parts = [f"📊 **Status (Bot: {bot.name})**\\n---\\n"]

    # Provider and Model are always shown
    status_parts.append(f"🌐 Provider: `{display}`\\n")
    status_parts.append(f"🧠 Model: `{bot.current_model}`\\n")

    # Mode-specific: Role and Persona only for chat mode
    if is_chat:
        status_parts.append(f"🎭 Role: `{display_name}`\\n")
        status_parts.append(f"👤 Persona: `{persona_display}`\\n")

    # Mode line always shown
    status_parts.append(f"📖 Mode: `{bot.current_mode}`\\n")

    # Always show Trace and Lazy Mode
    status_parts.append(f"📝 Trace Mode: `{'ON' if bot.trace_mode else 'OFF'}`\\n")
    status_parts.append(f"😴 Lazy Mode: `{'ON' if bot.lazy_mode else 'OFF'}`\\n")

    # Mode-specific: Characters and storyelements only for story mode
    if is_story:
        status_parts.append(f"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\\n")
        # Scenes in story mode are referred to as "storyelements"
        scene_label = "📗 Story Elements" if bot.active_scenes else "📗 Story Elements: None"
        status_parts.append(f"{scene_label}: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\\n")
    elif is_chat:
        # In chat mode, show active chars/scenes if any
        if bot.active_characters or bot.active_scenes:
            status_parts.append(f"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\\n")
            status_parts.append(f"🎬 Active Scenes: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\\n")

    # Context and generation info always shown
    status_parts.append(f"💾 Context: `{len(bot.chat_history)}` msgs `{size_kb}KB` max `{bot.context_limit}`\\n")
    status_parts.append(f"📏 Length: `{f'~{bot.response_length} words' if bot.response_length else 'None'}`\\n")
    status_parts.append(f"🧠 LLM Ctx: `{bot.generation_options['num_ctx']}`")

    status_text = "".join(status_parts)'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('system/handlers.py', 'w', encoding='utf8') as f:
        f.write(content)
    print("Success: Status display made mode-aware!")
else:
    print("Could not find the old status_text block in the file.")
    # Debug: show what we have around the status_text area
    idx = content.find('status_text')
    if idx >= 0:
        print(f"Found status_text at index {idx}")
        print(content[idx:idx+800])