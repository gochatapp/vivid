#!/usr/bin/env python3
with open('system\\bot_instance.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# 1. Add voice_mode to save_session metadata
old1 = '                \"trace_mode\": self.trace_mode\\n            },'
new1 = '                \"trace_mode\": self.trace_mode,\\n                \"voice_mode\": self.voice_mode\n            },'

if old1 in content:
    content = content.replace(old1, new1, 1)
    print('Added voice_mode to save_session metadata')
else:
    print('WARNING: Could not find save_session metadata pattern')
    # Try to find and print context
    idx = content.find('trace_mode\": self.trace_mode')
    if idx >= 0:
        print('Found at index', idx)
        print('Context:', repr(content[idx:idx+60]))

# 2. Add voice_mode restoration in load_session
old2 = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n'
new2 = '                        self.trace_mode = meta.get(\"trace_mode\", False)\\n                        self.voice_mode = meta.get(\"voice_mode\", False)\\n'

if old2 in content:
    content = content.replace(old2, new2, 1)
    print('Added voice_mode restoration in load_session')
else:
    print('WARNING: Could not find load_session pattern')
    idx = content.find('self.trace_mode = meta.get')
    if idx >= 0:
        print('Found at index', idx)
        print('Context:', repr(content[idx:idx+60]))

with open('system\\bot_instance.py', 'w', encoding='utf-8', errors='replace') as f:
    f.write(content)

print('Done updating bot_instance.py')