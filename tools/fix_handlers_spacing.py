#!/usr/bin/env python3
"""
Fix spacing in handlers.py - remove extra blank lines between statements,
but keep proper spacing between top-level function/class definitions (PEP 8: 2 blank lines).
"""
import re

def is_top_level_def(line: str) -> bool:
    """Check if line is a top-level function or class definition."""
    stripped = line.strip()
    return (stripped.startswith('def ') or 
            stripped.startswith('class ') or 
            stripped.startswith('async def ')) and not line.startswith(' ')

def fix_spacing(content: str) -> str:
    lines = content.split('\n')
    
    # First pass: remove ALL blank lines inside function bodies
    # Keep track of indentation level to know when we're inside a function
    result = []
    indent_stack = [0]  # Stack of indentation levels
    
    for line in lines:
        stripped = line.strip()
        current_indent = len(line) - len(line.lstrip()) if line.strip() else 0
        
        # Update indent stack
        if stripped and not stripped.startswith('#'):
            # Non-empty, non-comment line
            while indent_stack and current_indent < indent_stack[-1]:
                indent_stack.pop()
            if current_indent > indent_stack[-1]:
                indent_stack.append(current_indent)
        
        # Determine if we're inside a function body (indent > 0 and not at module level)
        # Module level is indent 0, function body is indent > 0
        inside_function = len(indent_stack) > 1 or (len(indent_stack) == 1 and indent_stack[0] > 0)
        
        is_blank = stripped == ''
        
        if is_blank:
            # Only keep blank line if we're at module level (not inside function)
            if not inside_function:
                result.append(line)
        else:
            result.append(line)
    
    # Second pass: ensure proper blank lines between top-level definitions
    # We iterate through non-blank lines and insert blank lines as needed
    final_result = []
    
    i = 0
    while i < len(result):
        line = result[i]
        stripped = line.strip()
        
        # Add current line
        final_result.append(line)
        
        # Look ahead to next non-blank line
        j = i + 1
        while j < len(result) and result[j].strip() == '':
            j += 1
        
        if j < len(result):
            next_line = result[j].strip()
            current_is_def = is_top_level_def(stripped)
            next_is_def = is_top_level_def(next_line)
            current_is_import = stripped.startswith('from ') or stripped.startswith('import ')
            current_is_docstring = stripped.startswith('"""')
            
            # Determine blank lines needed
            blank_lines_needed = 0
            
            if current_is_def and next_is_def:
                # Between two top-level definitions: 2 blank lines (PEP 8)
                blank_lines_needed = 2
            elif current_is_import and next_is_def:
                # After imports before first definition: 1 blank line
                blank_lines_needed = 1
            elif current_is_docstring and next_is_def:
                # After module docstring: 1 blank line
                blank_lines_needed = 1
            elif current_is_def and next_line.startswith('class '):
                # Before class definition: 2 blank lines
                blank_lines_needed = 2
            
            # Add the needed blank lines
            for _ in range(blank_lines_needed):
                final_result.append('')
            
            # Skip any existing blank lines in input
            i = j
        else:
            i += 1
    
    return '\n'.join(final_result)

def main():
    input_file = r'c:\dev\vivid\pychat\system\handlers.py'
    output_file = r'c:\dev\vivid\pychat\system\handlers.py'
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    fixed = fix_spacing(content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(fixed)
    
    print(f"Fixed spacing in {input_file}")
    print(f"Original lines: {len(content.split(chr(10)))}")
    print(f"Fixed lines: {len(fixed.split(chr(10)))}")

if __name__ == '__main__':
    main()