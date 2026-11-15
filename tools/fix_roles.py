with open('system/handlers.py', 'r', encoding='utf8') as f:
    content = f.read()

new_content = content.replace('ROLES_DIR.glob', 'CHAT_ROLES_DIR.glob')

with open('system/handlers.py', 'w', encoding='utf8') as f:
    f.write(new_content)

print('Done replacing ROLES_DIR with CHAT_ROLES_DIR')