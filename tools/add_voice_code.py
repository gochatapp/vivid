#!/usr/bin/env python3
with open('system\\handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find the position to insert voice code
# Look for the pattern: '        bot.save_active_context_and_status()\\n', followed by blank lines and '        if bot.verbose_mode:'

old = '''        bot.save_active_context_and_status()'''

new = '''        bot.save_active_context_and_status()'''

if old in content:
    # Now find the surrounding context and insert voice code
    idx = content.find(old)
    # Insert after this line - add the voice output code
    voice_code = '''\n        # Send voice output if voice mode is enabled\n        if bot.voice_mode:\n            try:\n                # Strip markdown from the reply text\n                clean_text = strip_markdown(reply)\n                # Synthesize speech\n                mp3_bytes = await synthesize_speech(clean_text)\n                if mp3_bytes:\n                    try:\n                        await update.message.reply_voice(\n                            voice=InputFile(io.BytesIO(mp3_bytes), filename=\"voice.mp3\"),\n                            caption=clean_text[:200] if clean_text else \"\"\n                        )\n                    except Exception as e:\n                        print(f\" Voice send error: {e}\")\n            except Exception as e:\n                print(f\" TTS error: {e}\")\n\n'''
    
    # Insert after the save_active_context_and_status line
    # Find the newline after the save line
    after_save = content[idx:].find('\\n')
    insert_pos = idx + after_save + 1
    
    new_content = content[:insert_pos] + voice_code + content[insert_pos:]
    content = new_content
    
    with open('system\\handlers.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(content)
    
    print('Inserted voice output code')
else:
    print('Could not find save_active_context_and_status pattern')
EOF