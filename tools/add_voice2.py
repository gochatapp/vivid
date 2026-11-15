#!/usr/bin/env python3
with open('system\\bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Replace the exact trace_mode block in apply_status_dict
old = '    if "trace_mode" in data:\n        bot.trace_mode = bool(data["trace_mode"])'
new = '    if "trace_mode" in data:\n        bot.trace_mode = bool(data["trace_mode"])\n    if "voice_mode" in data:\n        bot.voice_mode = bool(data["voice_mode"]'

if old in content:
    content = content.replace(old, new, 1)
    print('Successfully replaced trace_mode block in apply_status_dict')
else:
    print('WARNING: Pattern not found')
    idx = content.find('if "trace_mode" in data:')
    print('Found at index:', idx)
    print('Context around match:', repr(content[idx:idx+80]))

with open('system\\bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print('Done updating bot_instance.py')