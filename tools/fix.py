#!/usr/bin/env python3
import sys

# Step 1: Add voice_mode to bot_instance.py __init__
print("Step 1: voice_mode in __init__")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'self.lazy_mode = False' in line:
        lines.insert(i+1, '        self.voice_mode = False\n')
        break
with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(lines)
print("  Done")

# Step 2: Add voice_mode to get_bot_status_dict
print("Step 2: voice_mode in get_bot_status_dict")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
old = '        \"total_messages_sent\": bot.total_messages_sent,'
new = '        \"total_messages_sent\": bot.total_messages_sent,\n        \"voice_mode\": bot.voice_mode,'
if old in content:
    content = content.replace(old, new, 1)
    with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(content)
    print("  Done")
else:
    print("  Pattern not found")

# Step 3: Add voice_mode in apply_status_dict
print("Step 3: voice_mode in apply_status_dict")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
old = '    if \"trace_mode\" in data:\\n        bot.trace_mode = bool(data[\"trace_mode\"])\\n'
new = '    if \"trace_mode\" in data:\\n        bot.trace_mode = bool(data[\"trace_mode\"])\\n    if \"voice_mode\" in data:\\n        bot.voice_mode = bool(data[\"voice_mode\"])\\n'
if old in content:
    content = content.replace(old, new, 1)
    with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(content)
    print("  Done")
else:
    print("  Pattern not found")

# Step 4: Add voice_mode to save_session metadata
print("Step 4: voice_mode in save_session")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
old = '                \"trace_mode\": self.trace_mode\\n            },'
new = '                \"trace_mode\": self.trace_mode,\\n                \"voice_mode\": self.voice_mode\\n            },'
if old in content:
    content = content.replace(old, new, 1)
    with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(content)
    print("  Done")
else:
    print("  Pattern not found")

# Step 5: Add voice_mode restoration in load_session
print("Step 4.5: voice_mode restoration in load_session")
with open('system/bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
old = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n'
new = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n                        self.voice_mode = meta.get(\"voice_mode\", False)\\n'
if old in content:
    content = content.replace(old, new, 1)
    with open('system/bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(content)
    print("  Done")
else:
    print("  Pattern not found")

print("\\nStep 5: toggle_voice function and handler in handlers.py")
# Read handlers.py
with open('system/handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Add toggle_voice function before setup_handlers
func_marker = 'def setup_handlers(app: Application):'
idx = content.find(func_marker)
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
    print("  Could not find setup_handlers")

# Add voice handler registration after trace handler
old_reg = '    app.add_handler(PrefixHandler(\".\",\"trace\", toggle_trace))\\n'
new_reg = '    app.add_handler(PrefixHandler(\".\",\"trace\", toggle_trace))\\n\\n    app.add_handler(PrefixHandler(\".\", \"voice\", toggle_voice))\\n'
if old_reg in content:
    content = content.replace(old_reg, new_reg)
    print("  Added voice handler registration")
else:
    print("  Could not find trace handler registration")

# Add voice to LAZY_COMMAND_MAP
old_lazy = '    \"mcontext\": llmcontext_command'
new_lazy = '    \"mcontext\": llmcontext_command,\\n    \"voice\": toggle_voice,'
if old_lazy in content:
    content = content.replace(old_lazy, new_lazy, 1)
    print("  Added voice to LAZY_COMMAND_MAP")
else:
    print("  Could not add voice to LAZY_COMMAND_MAP")

# Write handlers.py
with open('system/handlers.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print("\\nAll steps complete!")
"