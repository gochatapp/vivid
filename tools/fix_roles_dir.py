#!/usr/bin/env python3
import sys

with open('system/handlers.py', 'r') as f:
    content = f.read()

# Replace all ROLES_DIR.glob with CHAT_ROLES_DIR.glob
new_content = content.replace('ROLES_DIR.glob', 'CHAT_ROLES_DIR.glob')

with open('system/handlers.py', 'w') as f:
    f.write(new_content)

print('Replacements done')