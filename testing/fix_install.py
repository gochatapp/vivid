#!/usr/bin/env python3
"""Script to fix the install_command to accept full URLs."""

with open('c:\\dev\\vivid\\vivid\\vivid.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Get the exact old code from the file
idx = content.find('async def install_command')
if idx >= 0:
    # Find the end of the function signature and first few lines
    end_idx = content.find('status_msg = await update.message.reply_text(f"⬇️ Fetching gist `{gist_id}` via GitHub API...")', idx)
    if end_idx >= 0:
        end_idx += len('status_msg = await update.message.reply_text(f"⬇️ Fetching gist `{gist_id}` via GitHub API...")')
        old_code = content[idx:end_idx]
        print("Found old code:")
        print(repr(old_code))
    else:
        print("Could not find end marker")
else:
    print("Function not found")
    exit(1)

new_code = '''def extract_gist_id(input_str: str) -> str:
    """
    Extract gist ID from various input formats:
    - Plain gist ID: 30dd3afbbcbf2c42eb504f9361b41860
    - Full URL: https://gist.github.com/psychomate/30dd3afbbcbf2c42eb504f9361b41860
    - Full URL without username: https://gist.github.com/30dd3afbbcbf2c42eb504f9361b41860
    - Raw URL: https://gist.githubusercontent.com/psychomate/30dd3afbbcbf2c42eb504f9361b41860/raw/...
    """
    import re
    # Pattern to match gist ID (32+ hex characters)
    gist_id_pattern = r'([a-f0-9]{32,})'
    
    # Try to extract from URL first
    match = re.search(gist_id_pattern, input_str)
    if match:
        return match.group(1)
    
    # Fallback: return as-is (might be just the ID)
    return input_str.strip()


async def install_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Installs a role or story from a GitHub Gist using the GitHub API.
    Usage: .install <gist_id_or_url>
    Detects GOCHATAPPROLETAG_* for roles and GOCHATAPPSTORYTAG_* for stories.
    Accepts full gist URLs or just the gist ID.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, "❌ Usage: `.install <gist_id_or_url>`")
        return

    # Join all args in case URL was split (though Telegram usually keeps it as one arg)
    input_str = " ".join(context.args).strip()
    gist_id = extract_gist_id(input_str)
    
    status_msg = await update.message.reply_text(f"⬇️ Fetching gist `{gist_id}` via GitHub API...")'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open('c:\\dev\\vivid\\vivid\\vivid.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Replaced install_command')
else:
    print('OLD CODE NOT FOUND - whitespace mismatch')
    # Try with normalized whitespace
    import re
    old_normalized = re.sub(r'\\s+', ' ', old_code)
    content_normalized = re.sub(r'\\s+', ' ', content)
    if old_normalized in content_normalized:
        print('Found with normalized whitespace')
    else:
        print('Not found even with normalized whitespace')