#!/usr/bin/env python3
with open('system\\handlers.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Insert toggle_voice function before s_command
idx = content.find('async def s_command')
if idx >= 0:
    new_function = '''\nasync def toggle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    \"\"\"Toggles voice output for bot messages.\"\"\"
    if update.effective_user.id != get_telegram_user_id():
        return

    bot = get_bot(context, update)

    bot.voice_mode = not bot.voice_mode
    bot.save_active_context_and_status()
    status = \"ON \\U0001F50A\" if bot.voice_mode else \"OFF \\U0001F507\"
    await reply_transient(update, context, f\"Voice Mode: {status}\")

'''
    new_content = content[:idx] + new_function + content[idx:]
    with open('system\\handlers.py', 'w', encoding='utf-8', errors='replace') as f:
        f.write(new_content)
    print('Inserted toggle_voice function')
else:
    print('Could not find s_command')