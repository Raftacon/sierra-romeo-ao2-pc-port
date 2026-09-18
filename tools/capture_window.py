"""Capture windows belonging to one explicitly selected test process."""
import argparse
from pathlib import Path
from probe import windows, capture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pid', type=int)
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
found = windows(args.pid)
if not found:
    raise SystemExit('No windows found for the selected process')
for i, (hwnd, title, width, height) in enumerate(found):
    path = args.output if i == 0 else args.output.with_stem(args.output.stem + f'-{i}')
    if capture(hwnd, width, height, path): print(f'{title}: {path}')
