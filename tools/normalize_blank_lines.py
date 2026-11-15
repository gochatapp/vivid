"""Restore the intended blank-line layout of a Python source file.

Background
----------
``system/handlers.py`` was mangled twice.

1. A script inserted a blank line after every statement, which doubled the
   height of every affected function body (4,229 lines for 1,827 statements).
   The damage is not uniform: some functions were hit, others were not.
2. ``fix_handlers_spacing.py`` then tried to repair that and deleted *every*
   blank line in the file instead, gluing all definitions together.

Neither version is readable.  This tool rebuilds the layout from the damaged
file.  Each top-level block is inspected on its own: when nearly every gap in
a block holds a blank line, the block was double-spaced and exactly one
artefact blank is removed from each gap, so the author's paragraph breaks
survive.  Untouched blocks keep their blank lines verbatim.  PEP 8 spacing is
then enforced around top-level definitions (two blank lines) and methods (one
blank line), a block header (``def``, ``if``, ``try``, ``else``, ...) is never
followed by a blank line, and blank lines inside a docstring are repaired like
code because the corruption added them there too.

Blank lines inside any other multi-line string are content, so they are copied
through untouched.  Only whitespace changes: the tool refuses to write a file
whose abstract syntax tree or sequence of non-blank lines differs from the
input, ignoring blank lines inside string constants when it compares the tree.

Usage
-----
    python tools/normalize_blank_lines.py --report system/handlers.py
    python tools/normalize_blank_lines.py --apply  system/handlers.py
    python tools/normalize_blank_lines.py --check  system/handlers.py
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Windows consoles default to a legacy code page; degrade instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# A top-level block counts as double-spaced when at least this share of its
# gaps holds a blank line.
DAMAGED_RATIO = 0.6
# Blocks with fewer internal gaps than this are too small to classify, so they
# are left exactly as they are.
MIN_GAPS = 2
# PEP 8 wants two blank lines around top-level definitions, one around methods.
TOP_LEVEL_BLANKS = 2
METHOD_BLANKS = 1


def indentation(line: str) -> int:
    """Number of leading spaces/tabs of ``line``."""
    return len(line) - len(line.lstrip(" \t"))


def is_definition(line: str) -> bool:
    """True for ``def``/``async def``/``class`` lines (any indentation)."""
    return line.strip().startswith(("def ", "async def ", "class "))


def lines_inside_strings(source: str) -> set[int]:
    """1-based numbers of the lines that belong to a multi-line string literal."""
    protected: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, IndentationError, tokenize.TokenError):
        return protected

    string_start: int | None = None
    fstring_start = getattr(tokenize, "FSTRING_START", -1)
    fstring_end = getattr(tokenize, "FSTRING_END", -1)
    for token in tokens:
        if token.type == tokenize.STRING and token.end[0] > token.start[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
        elif token.type == fstring_start:
            string_start = token.start[0]
        elif token.type == fstring_end and string_start is not None:
            if token.end[0] > string_start:
                protected.update(range(string_start, token.end[0] + 1))
            string_start = None
    return protected


def docstring_lines(source: str) -> set[int]:
    """1-based numbers of the lines that belong to a docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    spans: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        spans.update(range(value.lineno, value.end_lineno + 1))
    return spans



class Layout:
    """Blank-line layout of a source file, plus the rules that repair it."""

    def __init__(self, source: str) -> None:
        self.lines = source.split("\n")
        self.final_newline = bool(self.lines) and self.lines[-1] == ""
        if self.final_newline:
            self.lines.pop()
        self.protected = lines_inside_strings(source)
        self.docstrings = docstring_lines(source)
        # Blank lines inside these lines are content, not layout.
        self.frozen = self.protected - self.docstrings
        self.entries = [
            index for index, line in enumerate(self.lines) if line.strip()
        ]
        self.gaps = [
            self._gap(previous, current)
            for previous, current in zip(self.entries, self.entries[1:])
        ]
        self.block_start = self._block_starts()
        self.damaged = self._damaged_blocks()
        self.top_level_defs, self.nested_defs = self._definition_starts()

    def _block_starts(self) -> list[int]:
        """For every entry, the position of the top-level line that owns it."""
        starts: list[int] = []
        current = 0
        for position, line_index in enumerate(self.entries):
            if line_index + 1 in self.frozen:
                starts.append(current)
                continue
            if position and indentation(self.lines[line_index]) == 0:
                current = position
            starts.append(current)
        return starts

    def _gap(self, previous: int, current: int) -> dict:
        """Describe the blank lines between two non-blank lines."""
        between = range(previous + 1, current)
        return {
            "prev": previous,
            "cur": current,
            "run": sum(1 for index in between if not self.lines[index].strip()),
            "frozen": any(index + 1 in self.frozen for index in between),
        }

    def _block_gaps(self) -> dict[int, list[int]]:
        """Gap indexes of every top-level block, keyed by the block's position.

        Module-level gaps and string content are not evidence of doubling, so
        they are left out.
        """
        blocks: dict[int, list[int]] = {}
        for index, gap in enumerate(self.gaps):
            if gap["frozen"]:
                continue
            if indentation(self.lines[gap["prev"]]) == 0:
                # Module-level gaps are never doubled by the corruption.
                continue
            blocks.setdefault(self.block_start[index], []).append(index)
        return blocks

    def _damaged_blocks(self) -> set[int]:
        """Positions in ``entries`` of the top-level blocks that were double-spaced.

        The corruption inserted a blank line after every statement, so it is
        visible per block: nearly every gap inside an affected block holds a
        blank line, while an untouched block only holds the blank lines its
        author wrote.
        """
        damaged: set[int] = set()
        for block, indexes in self._block_gaps().items():
            if len(indexes) < MIN_GAPS:
                continue
            filled = sum(1 for index in indexes if self.gaps[index]["run"])
            if filled / len(indexes) > DAMAGED_RATIO:
                damaged.add(block)
        return damaged

    def report(self) -> list[str]:
        """One line per top-level block that was double-spaced."""
        lines: list[str] = []
        blocks = self._block_gaps()
        for block in sorted(self.damaged):
            indexes = blocks.get(block, [])
            filled = sum(1 for index in indexes if self.gaps[index]["run"])
            name = self.lines[self.entries[block]].strip()
            if len(name) > 58:
                name = name[:55] + "..."
            lines.append(f"    {name}  ({filled}/{len(indexes)} gaps filled)")
        return lines

    def _definition_starts(self) -> tuple[set[int], set[int]]:
        """Positions in ``entries`` where a definition group begins.

        Decorators and comment blocks that sit directly above a
        ``def``/``class`` belong to that definition, so the blank lines go
        above them.
        """
        top_level: set[int] = set()
        nested: set[int] = set()
        for position, line_index in enumerate(self.entries):
            line = self.lines[line_index]
            if line_index + 1 in self.protected:
                # Text inside a string literal can look like code.
                continue
            if not is_definition(line):
                continue
            level = indentation(line)
            start = position
            back = position - 1
            while back >= 0:
                previous = self.lines[self.entries[back]]
                stripped = previous.strip()
                if indentation(previous) == level and (
                    stripped.startswith("@") or stripped.startswith("#")
                ):
                    start = back
                    back -= 1
                    continue
                break
            (top_level if level == 0 else nested).add(start)
        return top_level, nested

    def blanks_before(self, position: int) -> int:
        """How many blank lines belong before ``entries[position]``.

        ``position`` is never zero, so a gap always exists ahead of it.
        """
        gap = self.gaps[position - 1]
        if gap["frozen"]:
            # Inside a multi-line string the blank lines are content.
            return gap["run"]

        previous = self.lines[gap["prev"]]
        current = self.lines[gap["cur"]]
        previous_indent = indentation(previous)
        current_indent = indentation(current)

        if self.block_start[position - 1] in self.damaged:
            # The corruption added one blank line to the gap; drop it and keep
            # whatever the author wrote on top of that.
            blanks = max(0, gap["run"] - 1)
        else:
            blanks = gap["run"]

        header = previous.lstrip()
        if header.startswith("@") or (
            current_indent > previous_indent and previous.rstrip().endswith(":")
        ):
            # No blank line between a block header and its body.
            return 0
        if position in self.top_level_defs:
            # A finished top-level block is followed by two blank lines.
            return TOP_LEVEL_BLANKS
        if current_indent == 0 and previous_indent > 0:
            return TOP_LEVEL_BLANKS
        if position in self.nested_defs and current_indent == previous_indent:
            return METHOD_BLANKS
        return blanks

    def render(self) -> str:
        """Return the source with the repaired blank-line layout."""
        output: list[str] = []
        for position, line_index in enumerate(self.entries):
            if position:
                gap = self.gaps[position - 1]
                if gap["frozen"]:
                    # Copy string content byte for byte.
                    output.extend(self.lines[gap["prev"] + 1:line_index])
                else:
                    output.extend([""] * self.blanks_before(position))
            output.append(self.lines[line_index])
        text = "\n".join(output)
        return text + ("\n" if self.final_newline else "")

    def summary(self) -> dict:
        """Numbers used by the report."""
        remaining = sum(
            self.blanks_before(position) for position in range(1, len(self.entries))
        )
        return {
            "lines": len(self.lines),
            "code_lines": len(self.entries),
            "blank_lines": len(self.lines) - len(self.entries),
            "damaged_blocks": len(self.damaged),
            "kept_blank_lines": remaining,
            "top_level_defs": len(self.top_level_defs),
            "nested_defs": len(self.nested_defs),
            "string_lines": len(self.protected),
            "docstring_lines": len(self.docstrings & self.protected),
        }


def code_lines(source: str) -> list[str]:
    """Every non-blank line of ``source``, in order."""
    return [line for line in source.split("\n") if line.strip()]


def _without_blank_lines(text: str) -> str:
    """``text`` with its blank lines dropped; every other line is untouched."""
    return "\n".join(line for line in text.split("\n") if line.strip())


def _shape(source: str) -> str:
    """The syntax tree of ``source``, ignoring blank lines inside strings.

    Docstrings are repaired like code, so their blank lines may legitimately
    differ.  Comparing the tree with those blank lines removed still catches
    any change to real string content.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = _without_blank_lines(node.value)
    return ast.dump(tree)


def verify(original: str, updated: str) -> list[str]:
    """Reasons ``updated`` is not a whitespace-only change of ``original``."""
    problems: list[str] = []
    try:
        original_shape = _shape(original)
        updated_shape = _shape(updated)
    except SyntaxError as error:
        return [f"cannot parse source: {error}"]
    if original_shape != updated_shape:
        problems.append("abstract syntax tree changed")
    if code_lines(original) != code_lines(updated):
        problems.append("non-blank lines changed")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalise blank lines in a Python source file.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(BASE_DIR / "system" / "handlers.py"),
        help="file to inspect (default: system/handlers.py)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print extra detail about the layout that would be written",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repaired layout back to the file",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit with status 1 when the file still needs normalising",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    original = path.read_text(encoding="utf-8")
    layout = Layout(original)
    updated = layout.render()

    problems = verify(original, updated)
    if problems:
        print(f"refusing to write {path}: " + "; ".join(problems))
        return 2

    before = layout.summary()
    after = Layout(updated).summary()

    print(path)
    print(f"  lines            {before['lines']} -> {len(updated.splitlines())}")
    print(f"  code lines       {before['code_lines']} (unchanged)")
    print(f"  blank lines      {before['blank_lines']} -> {after['blank_lines']}")
    print(f"  damaged blocks   {before['damaged_blocks']} double-spaced blocks repaired")
    print(f"  definitions      {before['top_level_defs']} top-level, {before['nested_defs']} nested")
    print(f"  string lines     {before['string_lines']} (docstrings {before['docstring_lines']})")

    if args.report:
        print("  double-spaced blocks:")
        for line in layout.report():
            print(line)

    if args.check:
        if updated == original:
            print("  spaced correctly")
            return 0
        print("  needs normalising")
        return 1

    if args.apply:
        path.write_text(updated, encoding="utf-8", newline="")
        print("  written")
    else:
        print("  (dry run, pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

