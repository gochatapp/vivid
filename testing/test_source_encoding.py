"""
Regression tests: project sources must stay valid UTF-8 and free of cp437
mojibake, and every text-mode file write must pass an explicit encoding.

Why: ``system/handlers.py`` was once round-tripped through the Windows legacy
code page (cp437), so UTF-8 bytes were decoded as cp437 and re-saved.  Telegram
then showed a rocket as the four characters U+2261 U+0192 U+00DC U+00C7 instead
of U+1F680.  These checks catch that class of corruption (and the unencoded
I/O calls that cause it on Windows) before it reaches a chat.

Run:  python testing/test_source_encoding.py
"""

import io
import ast
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fix_mojibake import repair_mojibake, strip_icons  # noqa: E402

SKIP_DIRS = {".venv", "__pycache__", ".git", "chats_stories", "settings"}
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".yaml", ".yml", ".sh", ".bat"}
TEXT_NAMES = {"vlin.sh", "vwin.bat"}

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def source_files():
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            yield path


TEXT_IO_NAMES = {"open", "read_text", "write_text"}
BINARY_MODE = re.compile(r"['\"][rwax]b\+?['\"]")


def unencoded_io(path):
    """Return [(line_no, line)] for text-mode I/O that forgets encoding=.

    Uses the tokenizer, so prose in docstrings/comments cannot trip the check.
    """
    problems = []
    source = path.read_bytes().decode("utf-8")
    lines = source.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        # Leftover scratch scripts in system/ are not importable; skip them here.
        return problems
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in TEXT_IO_NAMES:
            continue
        if index and tokens[index - 1].string != "." and token.string != "open":
            # Only Path.read_text()/write_text() methods require an encoding;
            # a bare read_text() call is a local helper, not file I/O by path.
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].string != "(":
            continue
        depth = 0
        args = []
        for inner in tokens[index + 1:]:
            if inner.type == tokenize.OP and inner.string in "([{":
                depth += 1
                if depth == 1:
                    continue
            elif inner.type == tokenize.OP and inner.string in ")]}":
                depth -= 1
                if depth == 0:
                    break
            args.append(inner)
        texts = [arg.string for arg in args]
        if any(arg.type == tokenize.NAME and arg.string == "encoding" for arg in args):
            continue
        if texts and any(BINARY_MODE.search(text or "") for text in texts):
            continue
        line_no = token.start[0]
        problems.append((line_no, lines[line_no - 1].strip()))
    return problems


def run():
    files = list(source_files())
    print(f"scanning {len(files)} source file(s) under {ROOT}")

    print("\nsources decode as UTF-8")
    undecodable = []
    for path in files:
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            undecodable.append(f"{path.relative_to(ROOT)}: {exc}")
    check("all sources are valid UTF-8", not undecodable, "; ".join(undecodable))

    print("\nno cp437 mojibake (UTF-8 bytes decoded as a legacy code page)")
    mangled = []
    for path in files:
        text = path.read_bytes().decode("utf-8")
        if repair_mojibake(text) != text:
            mangled.append(str(path.relative_to(ROOT)))
    check("no mojibake sequences found", not mangled, f"in {mangled}")

    print("\nhandlers.py keeps the intended emoji icons")
    handlers = (ROOT / "system" / "handlers.py").read_text(encoding="utf-8")
    for icon, context in [
        ("\U0001f680", "Using:"),
        ("\U0001f464", "user role icon"),
        ("\U0001f916", "assistant role icon"),
        ("\u2699", "system role icon"),
        ("\u274c", "error marker"),
    ]:
        check(f"{context} renders as {icon!r}", icon in handlers)

    print("\napp modules parse cleanly")
    app_modules = [
        "vivid.py",
        "system/__init__.py",
        "system/__main__.py",
        "system/config.py",
        "system/providers.py",
        "system/tracker.py",
        "system/bot_instance.py",
        "system/content.py",
        "system/handlers.py",
        "system/web_server.py",
        "system/runner.py",
        "tools/fix_mojibake.py",
        "testing/test_source_encoding.py",
    ]
    broken = []
    for name in app_modules:
        path = ROOT / name
        if not path.exists():
            broken.append(f"{name}: missing")
            continue
        try:
            ast.parse(path.read_bytes().decode("utf-8"), filename=name)
        except SyntaxError as exc:
            broken.append(f"{name}: {exc}")
    check("all app modules compile", not broken, "; ".join(broken))

    print("\ntext-mode file I/O always declares an encoding")
    offenders = []
    for path in files:
        if path.suffix != ".py":
            continue
        for line_no, line in unencoded_io(path):
            offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {line}")
    check("no unencoded text-mode open()/read_text()/write_text()",
          not offenders, "\n    " + "\n    ".join(offenders[:10]))

    print("\nrepair helpers")
    sample = "\u2261\u0192\u00dc\u00c7 Using: `sloth-llama`"
    expected = "\U0001f680 Using: `sloth-llama`"
    check("mojibake rocket restores to U+1F680",
          repair_mojibake(sample) == expected, repr(repair_mojibake(sample)))
    check("repair is idempotent",
          repair_mojibake(expected) == expected)
    check("strip_icons keeps indentation and drops the icon",
          strip_icons("    x = '\u274c err'") == "    x = 'err'",
          repr(strip_icons("    x = '\u274c err'")))

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
