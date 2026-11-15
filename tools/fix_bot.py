#!/usr/bin/env python3
with open('system\\bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# The issue is line 146 has an unclosed string. Let me find and fix it.
# The problem line is: '        bot.voice_mode = bool(data["voice_mode"])\n'
# But there might be a missing closing quote or paren.

# Let me just replace the problematic section in apply_status_dict
# Find the pattern and ensure it's correct
old = """    if "voice_mode" in data:
        bot.voice_mode = bool(data["voice_mode"])
    if "voice_mode" in data:
        bot.voice_mode = bool(data["voice_mode"])"""

new = """    if "voice_mode" in data:
        bot.voice_mode = bool(data["voice_mode"])"""

if old in content:
    content = content.replace(old, new, 1)
    print('Fixed duplicate voice_mode in apply_status_dict')
else:
    print('Pattern not found, trying alternative')
    # Try just removing the second occurrence
    idx = content.find('if "voice_mode" in data:')
    idx2 = content.find('if "voice_mode" in data:', idx + 1)
    if idx2 >= 0:
        # Find the end of the second block
        print('Found second occurrence at', idx2)
    
with open('system\\bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print('Done')