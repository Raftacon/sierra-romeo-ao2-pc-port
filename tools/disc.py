"""Read-only XDVDFS inventory and extraction. Python 3.10+, no dependencies.

Format reference: xenia-project/xenia src/xenia/vfs/devices/disc_image_device.cc.
Game files and generated code belong in ignored local directories.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import struct

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
PARTITIONS = (0, 0xFB20, 0x20600, 0x2080000, 0xFD90000)


@dataclass(frozen=True)
class Entry:
    path: str
    offset: int
    size: int
    directory: bool


class Disc:
    def __init__(self, path: Path):
        self.path = path
        self.file = path.open("rb")
        self.size = path.stat().st_size
        try:
            for offset in PARTITIONS:
                if offset + 33 * SECTOR > self.size:
                    continue
                header = self.read(offset + 32 * SECTOR, SECTOR)
                if header[:20] == MAGIC and header[-20:] == MAGIC:
                    self.partition = offset
                    self.root_sector, self.root_size = struct.unpack_from("<II", header, 20)
                    break
            else:
                raise ValueError("No supported XDVDFS game partition found")
        except Exception:
            self.file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError(f"Disc range outside image: {offset:#x}+{size:#x}")
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise ValueError("Truncated image")
        return data

    def entries(self) -> list[Entry]:
        entries = []
        directories = [(self.root_sector, self.root_size, "")]
        seen_dirs = set()
        seen_paths = set()
        while directories:
            sector, size, parent = directories.pop()
            if not size:
                continue
            if (sector, size) in seen_dirs:
                raise ValueError("Cyclic or aliased directory")
            seen_dirs.add((sector, size))
            if not 14 <= size <= 32 * 1024 * 1024:
                raise ValueError("Invalid directory size")
            data = self.read(self.partition + sector * SECTOR, size)
            pending, visited = [0], set()
            while pending:
                pos = pending.pop()
                if pos in visited or pos + 14 > size:
                    raise ValueError("Cyclic or out-of-bounds directory node")
                visited.add(pos)
                left, right, child_sector, length, flags, n = struct.unpack_from("<HHIIBB", data, pos)
                if not n or pos + 14 + n > size:
                    raise ValueError("Invalid filename length")
                name = data[pos + 14:pos + 14 + n].decode("ascii")
                if name in (".", "..") or any(c in name for c in '/\\:<>"|?*') or name.endswith((".", " ")) or any(ord(c) < 32 for c in name):
                    raise ValueError(f"Unsafe filename: {name!r}")
                if name.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
                    raise ValueError(f"Reserved filename: {name!r}")
                path = f"{parent}/{name}" if parent else name
                if path.casefold() in seen_paths:
                    raise ValueError(f"Duplicate path: {path}")
                seen_paths.add(path.casefold())
                offset = self.partition + child_sector * SECTOR
                if offset + length > self.size:
                    raise ValueError(f"File outside image: {path}")
                directory = bool(flags & 0x10)
                entries.append(Entry(path, offset, length, directory))
                if directory:
                    directories.append((child_sector, length, path))
                pending.extend(x * 4 for x in (right, left) if x)
        return sorted(entries, key=lambda e: e.path.casefold())

    def extract(self, entry: Entry, root: Path) -> str | None:
        root = root.resolve()
        dest = root.joinpath(*entry.path.split('/'))
        if not dest.resolve().is_relative_to(root):
            raise ValueError(f"Extraction escapes destination: {entry.path}")
        if entry.directory:
            dest.mkdir(parents=True, exist_ok=True)
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        if dest.exists():
            if dest.stat().st_size != entry.size:
                raise ValueError(f"Existing file has different size: {dest}")
            with dest.open('rb') as existing:
                for offset in range(0, entry.size, 4 * 1024 * 1024):
                    block = self.read(entry.offset + offset, min(4 * 1024 * 1024, entry.size - offset))
                    if existing.read(len(block)) != block:
                        raise ValueError(f"Existing file differs from image: {dest}")
                    digest.update(block)
            return digest.hexdigest()
        try:
            with dest.open('xb') as output:
                for offset in range(0, entry.size, 4 * 1024 * 1024):
                    block = self.read(entry.offset + offset, min(4 * 1024 * 1024, entry.size - offset))
                    output.write(block)
                    digest.update(block)
        except Exception:
            # Preserve partial output for inspection; next run detects size mismatch.
            raise
        return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iso', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extract', type=Path)
    parser.add_argument('--only', help='Extract only this exact disc-relative path')
    args = parser.parse_args()
    with Disc(args.iso) as disc:
        entries = disc.entries()
        if args.only and not any(e.path.casefold() == args.only.casefold() for e in entries):
            parser.error(f'Path not found: {args.only}')
        report = {'iso_name': args.iso.name, 'iso_size': disc.size, 'partition_offset': disc.partition, 'entries': []}
        for entry in entries:
            item = asdict(entry)
            if args.extract and (not args.only or entry.path.casefold() == args.only.casefold()):
                item['sha256'] = disc.extract(entry, args.extract)
                print(f"Extracted {entry.path} ({entry.size:,} bytes)", flush=True)
            report['entries'].append(item)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(f"{len(entries)} entries; {sum(e.size for e in entries if not e.directory):,} file bytes")


if __name__ == '__main__':
    main()
