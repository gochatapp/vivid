#!/usr/bin/env python3
with open('system/handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Find LAZY_COMMAND_MAP and add voice
for i, line in enumerate(lines):
    if '\"llmctx\"' in line and 'mc' in line and 'mcontext' in line:
        # Found the LAZY_COMMAND_MAP section
        # Add voice after mcontext
        voice_entry = '    \"voice\": toggle_voice,\n'
        # Insert after this line
        lines.insert(i+1, voice_entry)
        print(f'Added voice to LAZY_COMMAND_MAP at line {i+1}')
        break

with open('system/handlers.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(lines)

print('Done adding voice to LAZY_COMMAND_MAP')