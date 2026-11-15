#!/usr/bin/env python3
with open('system/handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Add voice handler after trace handler (line 3975, 0-indexed 3974)
voice_handler_line = "\n    app.add_handler(PrefixHandler(\".\", \"voice\", toggle_voice))\n"

# Insert after line 3975 (0-indexed 3974)
insert_pos = 3976  # after the trace handler line

# Verify the position
if insert_pos <= len(lines):
    lines.insert(insert_pos, voice_handler_line)
    print(f'Inserted at position {insert_pos}')
else:
    print(f'Position {insert_pos} out of range, file has {len(lines)} lines')

with open('system/handlers.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(lines)

print('Done adding voice handler')