"""Temp harness: double a clean file, repair it, diff against the original.

Deleted once the rules in normalize_blank_lines.py reproduce the reference.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize_blank_lines as nbl  # noqa: E402


def double(source: str) -> str:
    """"Join every line with two newlines" -- the corruption's signature."""
    lines = source.split("\n")
    return "\n\n".join(lines)


def gaps(source: str) -> list[tuple[str, str, int]]:
    """(prev line, current line, blank count) for each gap between code lines."""
    layout = nbl.Layout(source)
    out = []
    for gap in layout.gaps:
        out.append(
            (
                layout.lines[gap["prev"]],
                layout.lines[gap["cur"]],
                gap["run"],
            )
        )
    return out


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "tools/_vivid_ref.py")
    reference = path.read_text(encoding="utf-8")
    doubled = double(reference)
    repaired = nbl.Layout(doubled).render()

    if repaired == reference:
        print("exact match: the rules reproduce the reference")
        return 0

    ref_gaps = gaps(reference)
    mine_gaps = gaps(repaired)
    print(f"reference gaps {len(ref_gaps)}, repaired gaps {len(mine_gaps)}")
    if len(ref_gaps) != len(mine_gaps):
        print("gap counts differ -- code lines diverged")
        return 1

    diff = collections.Counter()
    shown = 0
    for index, (ref, mine) in enumerate(zip(ref_gaps, mine_gaps)):
        if ref == mine:
            continue
        diff[(ref[2], mine[2])] += 1
        if shown < 25:
            print(f"  gap {index}: reference {ref[2]} vs repaired {mine[2]}")
            print(f"    prev {ref[0]!r}")
            print(f"    cur  {ref[1]!r}")
            shown += 1
    print("histogram (reference blanks, repaired blanks) -> count")
    for key, count in sorted(diff.items()):
        print(f"  {key} -> {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
