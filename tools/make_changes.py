#!/usr/bin/env python3
import sys
import os

# Step 1: Add voice_mode to bot_instance.py __init__
print("Step 1: Adding voice_mode to bot_instance.py __init__")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'self.lazy_mode = False' in line:
        lines.insert(i+1, '        self.voice_mode = False\n')
        break

with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(lines)

# Step 2: Add voice_mode persistence in get_bot_status_dict
print("Step 2: Adding voice_mode to get_bot_status_dict")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Add after "total_messages_sent" in the dict
old = '        \"total_messages_sent\": bot.total_messages_sent,'
new = '        \"total_messages_sent\": bot.total_messages_sent,\n        \"voice_mode\": bot.voice_mode,'
if old in content:
    content = content.replace(old, new, 1)
    print("  Added voice_mode to get_bot_status_dict")
else:
    print("  Could not find pattern for get_bot_status_dict")

# Add voice_mode in apply_status_dict
old2 = '    if \"trace_mode\" in data:\\n        bot.trace_mode = bool(data[\"trace_mode\"])\\n'
new2 = '    if \"trace_mode\" in data:\\n        bot.trace_mode = bool(data[\"trace_mode\"])\\n    if \"voice_mode\" in data:\\n        bot.voice_mode = bool(data[\"voice_mode\"])\\n'
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("  Added voice_mode to apply_status_dict")
else:
    print("  Could not find pattern for apply_status_dict")

with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

# Step 3: Add voice_mode to save_session metadata
print("Step 3: Adding voice_mode to save_session metadata")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

old3 = '                \"trace_mode\": self.trace_mode\\n            },'
new3 = '                \"trace_mode\": self.trace_mode,\\n                \"voice_mode\": self.voice_mode\\n            },'
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("  Added voice_mode to save_session metadata")
else:
    print("  Could not find pattern for save_session metadata")

with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

# Step 4: Add voice_mode restoration in load_session
print("Step 4: Adding voice_mode restoration in load_session")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

old4 = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n'
new4 = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n                        self.voice_mode = meta.get(\"voice_mode\", False)\\n'
if old4 in content:
    content = content.replace(old4, new4, 1)
    print("  Added voice_mode restoration in load_session")
else:
    print("  Could not find pattern for load_session restoration")

with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print("\\nStep 5: Adding toggle_voice function and handler to handlers.py")

# Step 5: Add toggle_voice function and handler to handlers.py
# Read the handlers file
with open('system/handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Add the toggle_voice function before setup_handlers
func_start = 'def setup_handlers(app: Application):'
# Find it
idx = content.find(func_start)
if idx >= 0:
    toggle_func = '''\nasync def toggle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    \"\"\"Toggles voice output for bot messages.\"\"\"
    if update.effective_user.id != get_telegram_user_id():
        return

    bot = get_bot(context, update)

    bot.voice_mode = not bot.voice_mode
    bot.save_active_context_and_status()
    status = \"ON \\\\uD83D\\\\uDCA8\" if bot.voice_mode else \"OFF \\\\uD83D\\\\uD447\"
    await reply_transient(update, context, f\"Voice Mode: {status}\")

'''
    # Insert before func_start
    new_content = content[:idx] + toggle_func + content[idx:]
    content = new_content
    print("  Added toggle_voice function")
else:
    print("  Could not find setup_handlers function")

# Add voice handler registration after trace handler
# Find "app.add_handler(PrefixHandler(\".\",\"trace\", toggle_trace))
old_registration = '    app.add_handler(PrefixHandler(\".\",\"trace\", toggle_trace))\\n'
new_registration = '    app.add_handler(PrefixHandler(\".\",\"trace\", toggle_trace))\\n\\n    app.add_handler(PrefixHandler(\".\", \"voice\", toggle_voice))\\n'
if old_registration in content:
    content = content.replace(old_registration, new_registration)
    print("  Added voice handler registration")
else:
    print("  Could not find trace handler registration pattern")

# Add \"voice\": toggle_voice to LAZY_COMMAND_MAP
old_lazy = '    \"mcontext\": llmcontext_command'
new_lazy = '    \"mcontext\": llmcontext_command,\\n    \"voice\": toggle_voice,'
if old_lazy in content:
    content = content.replace(old_lazy, new_lazy, 1)
    print("  Added voice to LAZY_COMMAND_MAP")
else:
    print("  Could not find LAZY_COMMAND_MAP pattern for voice")

# Write the handlers file
with open('system/handlers.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print("\\nAll steps complete!")
"