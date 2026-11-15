with open('system/handlers.py', 'r', encoding='utf8') as f:
    content = f.read()

# Find the status_text assignment and the lines that build it
# The old code has status_text = ( ... ) tuple

# New mode-aware status text builder
new_code = ''''''
# Build mode-aware status display
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

# Find the old status_text block
# It starts with 'status_text = (' and goes to the closing ')'
# Let's find and replace from line 1594 onwards

# Actually, let's find the exact old block by searching for a unique pattern
# The old code has 'f"🎭 Role:' on line 1601 (now shifted due to our edits)

# Let's search for the status_text assignment
import re
# Find 'status_text = (' pattern
match = re.search(r'status_text\s*=\s*\(.*?\)\s*$', content, re.DOTALL | re.MULTILINE)
if match:
    old_block = match.group(0)
    start_idx = match.start()
    print(f"Found old status_text block at {start_idx}, length {len(old_block)}")
    
    # Replace with new code
    new_content = content[:start_idx] + new_code + content[start_idx + len(old_block):]
    
    with open('system/handlers.py', 'w', encoding='utf8') as f:
        f.write(new_content)
    print("Replacement done!")
else:
    print("Could not find status_text block with regex")
    # Debug: show what we have around the status_text area
    idx = content.find('status_text')
    if idx >= 0:
        print(f"Found 'status_text' at {idx}")
        print(content[idx:idx+200])