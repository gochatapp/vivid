#!/usr/bin/env python3
import sys

with open('system\\handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Find the line with 'bot.save_active_context_and_status()' in handle_message
# and insert voice code after it (before the blank lines and verbose_mode check)
new_lines = []
inserted = False
for i, line in enumerate(lines):
    new_lines.append(line)
    # After line 1437 (0-indexed): '        bot.save_active_context_and_status()\\n'
    # and before line 1438: '\\n'
    if not inserted and i >= 1435 and i <= 1440:
        # Check if this is the right location (after save in handle_message)
        if 'bot.save_active_context_and_status()' in line and i >= 1435:
            # Insert voice code after this line
            voice_lines = [
                '\n',
                '# Send voice output if voice mode is enabled\n',
                '        if bot.voice_mode:\n',
                '            try:\n',
                '                # Strip markdown from the reply text\n',
                '                clean_text = strip_markdown(reply)\n',
                '                # Synthesize speech\n',
                '                mp3_bytes = await synthesize_speech(clean_text)\n',
                '                if mp3_bytes:\n',
                '                    try:\n',
                '                        await update.message.reply_voice(\n',
                '                            voice=InputFile(io.BytesIO(mp3_bytes), filename="voice.mp3"),\n',
                '                            caption=clean_text[:200] if clean_text else ""\n',
                '                        )\n',
                '                    except Exception as e:\n',
                '                        print(f" Voice send error: {e}")\n',
                '            except Exception as e:\n',
                '                print(f" TTS error: {e}")\n',
                '\n',
            ]
            new_lines.extend(voice_lines)
            inserted = True
            print(f'Inserted voice code after line {i+1}')

with open('system\\handlers.py', 'w', encoding='utf-8', errors='replace') as f:
    f.writelines(new_lines)

if not inserted:
    print('WARNING: Could not insert voice code - pattern not found')
    sys.exit(1)

print('Done fixing handlers.py')