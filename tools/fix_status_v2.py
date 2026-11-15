import re

with open('system/handlers.py', 'r', encoding='utf8') as f:
    content = f.read()

# We need to replace the status_text tuple with mode-aware code
# The current status_text is built as a tuple of f-strings from lines 1595-1621

# Strategy: replace the entire status_text assignment block

# New code that will replace the old status_text block
new_status_code = '''# Build mode-aware status display
    # Only chats can have roles and personas; only stories can have characters and storyelements (scenes)
    is_story_mode = bot.current_mode == "story"
    is_chat_mode = bot.current_mode == "chat"

    status_parts = [f"📊 **Status (Bot: {bot.name})**\\n---\\n"]

    # Provider and Model are always shown
    status_parts.append(f"🌐 Provider: `{display}`\\n")
    status_parts.append(f"🧠 Model: `{bot.current_model}`\\n")

    # Mode-specific: Role and Persona only for chat mode
    if is_chat_mode:
        status_parts.append(f"🎭 Role: `{display_name}`\\n")
        status_parts.append(f"👤 Persona: `{persona_display}`\\n")

    # Mode line always shown
    status_parts.append(f"📖 Mode: `{bot.current_mode}`\\n")

    # Always show Trace and Lazy Mode
    status_parts.append(f"📝 Trace Mode: `{'ON' if bot.trace_mode else 'OFF'}`\\n")
    status_parts.append(f"😴 Lazy Mode: `{'ON' if bot.lazy_mode else 'OFF'}`\\n")

    # Mode-specific: Characters and storyelements only for story mode
    if is_story_mode:
        status_parts.append(f"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\\n")
        # Scenes in story mode are referred to as "storyelements"
        scene_label = "📗 Story Elements" if bot.active_scenes else "📗 Story Elements: None"
        status_parts.append(f"{scene_label}: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\\n")
    elif is_chat_mode:
        # In chat mode, show active chars/scenes if any
        if bot.active_characters or bot.active_scenes:
            status_parts.append(f"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\\n")
            status_parts.append(f"🎬 Active Scenes: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\\n")

    # Context and generation info always shown
    status_parts.append(f"💾 Context: `{len(bot.chat_history)}` msgs `{size_kb}KB` max `{bot.context_limit}`\\n")
    status_parts.append(f"📏 Length: `{f'~{bot.response_length} words' if bot.response_length else 'None'}`\\n")
    status_parts.append(f"🧠 LLM Ctx: `{bot.generation_options['num_ctx']}`")

    status_text = "".join(status_parts)
'''

# Find the old status_text block and replace it
# The old block starts with 'status_text = (' and ends with ')'
# Let's find and replace it

# First, let's find the exact start and end
start_marker = 'status_text = ('
end_marker = ')'  # This is on line 1621 in the original

start_idx = content.find(start_marker)
if start_idx >= 0:
    # Find the closing parenthesis
    # We need to find the matching ')' - it's the one at the end of the tuple
    # The tuple spans from after 'status_text = (' to the last '}'
    # Let's find it by looking for the pattern
    remaining = content[start_idx + len(start_marker):]
    # Find the last ')' that closes the tuple
    # The pattern ends with ')' followed by newline and then the await line
    import re
    match = re.search(r'\)\s*$', remaining, re.MULTILINE)
    if match:
        end_rel = match.start() + 1  # Include the closing paren
        old_block = content[start_idx:start_idx + end_rel]
        print(f"Found old block from {start_idx} to {start_idx + end_rel}")
        print(f"Old block length: {len(old_block)}")
        
        # Replace with new code
        new_content = content[:start_idx] + new_status_code + content[start_idx + end_rel:]
        
        with open('system/handlers.py', 'w', encoding='utf8') as f:
            f.write(new_content)
        print("Replacement done!")
    else:
        print("Could not find closing parenthesis")
else:
    print("Could not find start marker")