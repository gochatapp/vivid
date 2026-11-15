#!/usr/bin/env python3
import sys

# Read the file
with open('system\\bot_instance.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add voice_mode after trace_mode in get_bot_status_dict
old1 = '''    if "trace_mode" in data:
        bot.trace_mode = bool(data["trace_mode"])
    """'''

new1 = '''    if "trace_mode" in data:
        bot.trace_mode = bool(data["trace_mode"])
    if "voice_mode" in data:
        bot.voice_mode = bool(data["voice_mode"])
    """'''

if old1 in content:
    content = content.replace(old1, new1, 1)
    print("Replaced first trace_mode block (get_bot_status_dict)")
else:
    print("WARNING: Could not find first trace_mode block pattern")

# Add voice_mode after trace_mode in apply_status_dict
# Find the second occurrence of "if \"trace_mode\" in data:"
first_marker = 'if "trace_mode" in data:'
first_idx = content.find(first_marker)
second_idx = content.find(first_marker, first_idx + 1)

if second_idx >= 0:
    # Find the end of this block (look for the next line that starts a new major section or """
    after_second = content[second_idx:]
    # Find next """ 
    next_triple = after_second.find('"""')
    if next_triple >= 0:
        # The trace_mode line in apply_status_dict is followed by a blank line typically
        # Let me find the exact position after "bot.trace_mode = bool(data["trace_mode"])"
        # Look for the pattern:        bot.trace_mode = bool(data["trace_mode"])
        trace_line_pattern = '        bot.trace_mode = bool(data["trace_mode"])'
        trace_line_idx = after_second.find(trace_line_pattern)
        if trace_line_idx >= 0:
            # Insert after this line
            insert_pos = second_idx + trace_line_idx + len(trace_line_pattern)
            # But we need to find the actual position in the full content
            # Let me compute: the trace_line_idx is relative to after_second, so:
            actual_insert_pos = second_idx + trace_line_idx + len(trace_line_pattern)
            
            insert_text = '\n    if "voice_mode" in data:\n        bot.voice_mode = bool(data["voice_mode"])\n'
            
            new_content = content[:actual_insert_pos] + insert_text + content[actual_insert_pos:]
            content = new_content
            print(f"Inserted voice_mode after second trace_mode at position {actual_insert_pos}")
        else:
            print("WARNING: Could not find trace_mode line in apply_status_dict")
    else:
        print("WARNING: Could not find closing triple quote after second trace_mode")
else:
    print("WARNING: Could not find second trace_mode marker")

with open('system\\bot_instance.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done updating bot_instance.py")