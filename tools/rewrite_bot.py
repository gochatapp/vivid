#!/usr/bin/env python3
import sys
import re

# Read the file
with open('system\\bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Find and fix the apply_status_dict function
# We need to ensure the voice_mode block is correctly placed
# The function should have:
# - if "trace_mode" in data: bot.trace_mode = bool(data["trace_mode"])
# - if "voice_mode" in data: bot.voice_mode = bool(data["voice_mode"])
# - then continue with lazy_mode, think_mode, verbose_mode, etc.

# Let me rebuild the apply_status_dict function correctly
new_lines = []
in_apply_status = False
voice_mode_added = False
brace_count = 0

for i, line in enumerate(lines):
    if 'def apply_status_dict' in line:
        in_apply_status = True
        new_lines.append(line)
        continue
    
    if in_apply_status:
        # Count braces to know when the function ends
        brace_count += line.count('{') - line.count('}')
        
        # Check if this is the trace_mode line we need to add voice_mode after
        if 'if "trace_mode" in data:' in line and not voice_mode_added:
            new_lines.append(line)
            # Add voice_mode after this line
            new_lines.append('    if "voice_mode" in data:\n')
            new_lines.append('        bot.voice_mode = bool(data["voice_mode"])\n')
            voice_mode_added = True
            # Don't increment brace_count here since we're adding lines
            continue
        
        # Check if we've already added voice_mode and we're at the lazy_mode line
        if voice_mode_added and 'if "lazy_mode" in data:' in line:
            new_lines.append(line)
            continue
        
        # Check if we're at the end of the function (braces balanced)
        if brace_count <= 0 and 'if "lazy_mode"' in line:
            # Function is ending, add the rest of the lines from original
            # but skip any duplicate voice_mode lines
            in_apply_status = False
    
    new_lines.append(line)

# Write back
with open('system\\bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(new_lines)

print('Rewrote apply_status_dict function')