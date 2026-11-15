"""Repair CP437 mojibake in Vivid Chat source files.

Background
----------
At some point ``system/handlers.py`` was round-tripped through the Windows
legacy code page (cp437): the UTF-8 bytes of the emoji/icons were decoded as
cp437 and the result was written back to disk as UTF-8.  A rocket (``U+1F680``,
UTF-8 ``F0 9F 9A 80``) therefore turned into the four characters
``U+2261 U+0192 U+00DC U+00C7``.

This tool reverses that transformation (cp437 -> bytes -> UTF-8).  It can also
remove the decoded emoji/icons so the source stays pure ASCII, which makes the
file immune to that class of corruption.

Usage
-----
    python tools/fix_mojibake.py --report system/handlers.py
    python tools/fix_mojibake.py --apply  system/handlers.py
    python tools/fix_mojibake.py --apply --strip-icons system/handlers.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Windows consoles default to a legacy code page that cannot encode emoji, so
# printing the report would raise UnicodeEncodeError.  Degrade instead of crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Icons that only exist to decorate a message.  When --strip-icons is used they
# are deleted; plain text is easier to read and cannot be mangled by a code page.
ICON_CHARS = set(
    "\U0001F680"  # rocket
    "\U0001F4DC"  # scroll
    "\U0001F464"  # bust in silhouette (user)
    "\U0001F916"  # robot (assistant)
    "\U0001F310"  # globe
    "\u2705"      # white heavy check mark
    "\u274c"      # cross mark
    "\u26a0"      # warning sign
    "\u2699"      # gear
    "\u23f3"      # hourglass
    "\u2714"      # heavy check mark
    "\ufe0f"      # variation selector-16 (emoji presentation)
    "\u200d"      # zero width joiner
)

# Punctuation that stays but is normalised to plain ASCII.
ASCII_REPLACEMENTS = {
    "\u2022": "-",    # bullet
    "\u2019": "'",    # right single quote
    "\u2018": "'",    # left single quote
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2013": "-",    # en dash
    "\u2014": "--",   # em dash
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",    # non breaking space
}


def _decode_chunk(chunk: str) -> str | None:
    """Return the UTF-8 text hidden in ``chunk``, or None if it is not mojibake."""
    try:
        raw = chunk.encode("cp437")
    except UnicodeEncodeError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _repair_run(run: str) -> str:
    """Repair one contiguous block of non-ASCII characters."""
    candidate = _decode_chunk(run)
    if candidate is not None:
        return candidate

    out: list[str] = []
    rest = run
    while rest:
        for size in range(len(rest), 0, -1):
            decoded = _decode_chunk(rest[:size])
            if decoded is not None:
                out.append(decoded)
                rest = rest[size:]
                break
        else:
            out.append(rest[0])
            rest = rest[1:]
    return "".join(out)


def repair_mojibake(text: str) -> str:
    """Reverse cp437 mojibake, leaving ASCII and genuine Unicode untouched."""
    out: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        char = text[pos]
        if char.isascii():
            out.append(char)
            pos += 1
            continue
        end = pos
        while end < length and not text[end].isascii():
            end += 1
        out.append(_repair_run(text[pos:end]))
        pos = end
    return "".join(out)


def strip_icons(text: str) -> str:
    """Remove decorative emoji/icons and normalise fancy punctuation to ASCII.

    Only the icon itself (plus a single space directly after it) is dropped, so
    Python indentation and alignment spaces are never touched.
    """
    out: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        char = text[pos]
        if char in ICON_CHARS:
            pos += 1
            if pos < length and text[pos] == " ":
                pos += 1
            continue
        out.append(ASCII_REPLACEMENTS.get(char, char))
        pos += 1
    return "".join(out)


def _non_ascii_runs(line: str):
    run: list[str] = []
    for char in line:
        if char.isascii():
            if run:
                yield "".join(run)
                run = []
        else:
            run.append(char)
    if run:
        yield "".join(run)


def read_text(path: Path) -> str:
    """Read UTF-8 without newline translation so CRLF survives untouched."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def report(path: Path) -> int:
    text = read_text(path)
    repaired = repair_mojibake(text)
    bad_lines = [
        (idx, line)
        for idx, line in enumerate(text.splitlines(), start=1)
        if any(not ch.isascii() for ch in line)
    ]
    print(f"{path}: {len(bad_lines)} line(s) containing non-ASCII characters")
    seen: dict[str, list] = {}
    for _idx, line in bad_lines:
        for chunk in _non_ascii_runs(line):
            fixed = _repair_run(chunk)
            entry = seen.setdefault(chunk, [fixed, 0])
            entry[1] += 1
    for chunk, (fixed, count) in sorted(seen.items(), key=lambda item: -item[1][1]):
        print(f"  {chunk!r:<26} -> {fixed!r:<30} x{count}")
    remaining = sorted({ch for ch in repaired if not ch.isascii()})
    print(f"  decoded non-ASCII characters: {''.join(remaining)!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair cp437 mojibake in source files.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", type=Path, help="files to inspect or repair")
    parser.add_argument("--report", action="store_true", help="show what would change")
    parser.add_argument("--apply", action="store_true", help="rewrite the files in place")
    parser.add_argument(
        "--strip-icons",
        action="store_true",
        help="also remove decorative emoji/icons (pure ASCII output)",
    )
    args = parser.parse_args(argv)

    if not args.report and not args.apply:
        parser.error("choose --report and/or --apply")

    for raw_path in args.paths:
        path = raw_path if raw_path.is_absolute() else (BASE_DIR / raw_path)
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 1
        if args.report:
            report(path)
        if args.apply:
            original = read_text(path)
            fixed = repair_mojibake(original)
            if args.strip_icons:
                fixed = strip_icons(fixed)
            if fixed == original:
                print(f"{path}: already clean")
            else:
                write_text(path, fixed)
                before = sum(1 for ch in original if not ch.isascii())
                after = sum(1 for ch in fixed if not ch.isascii())
                print(
                    f"{path}: repaired "
                    f"({before} -> {after} non-ASCII characters)"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

