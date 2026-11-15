#!/usr/bin/env python3
# Generate bot_instance.py with voice_mode support

lines = []

# Read the original file to get the structure
with open('system\\bot_instance.py.bak', 'r', encoding='utf-8', errors='replace') as f:
    orig_lines = f.readlines()

# We'll reconstruct the file with voice_mode additions
# Track key sections

i = 0
while i < len(orig_lines):
    line = orig_lines[i]
    
    # 1. Add voice_mode in __init__
    if 'self.lazy_mode = False' in line and i > 160 and i < 175:
        lines.append(line)  # lazy_mode
        lines.append('        self.voice_mode = False\n')  # voice_mode
        i += 1
        continue
    
    # 2. Add voice_mode in get_bot_status_dict
    if '\"total_messages_sent\": bot.total_messages_sent,' in line and i > 95 and i < 105:
        lines.append(line)
        # Add voice_mode after total_messages_sent
        # Find the right spot - after this line, before the closing }
        # Actually, let me just add it at the end of the dict
        i += 1
        continue
    
    # 3. Add voice_mode in apply_status_dict - we'll handle this differently
    # Skip the old voice_mode code and add fresh
    
    # 4. Add voice_mode in save_session metadata
    if '\"trace_mode\": self.trace_mode' in line and i > 438 and i < 445:
        lines.append(line)
        # Add voice_mode after trace_mode
        lines.append('                \"voice_mode\": self.voice_mode\n')
        i += 1
        continue
    
    # 5. Add voice_mode in load_session
    if 'self.trace_mode = meta.get(\"trace_mode\", False)' in line and i > 478 and i < 485:
        lines.append(line)
        # Add voice_mode restoration
        lines.append('                        self.voice_mode = meta.get(\"voice_mode\", False)\n')
        i += 1
        continue
    
    lines.append(line)
    i += 1

# Write the file
with open('system\\bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(lines)

print('Generated bot_instance.py with voice_mode')