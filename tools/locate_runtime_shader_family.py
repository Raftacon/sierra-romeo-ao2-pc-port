"""Find cached relatives of a guest shader, then check recorded draw-use logs.

This is a capture-location heuristic, not a shader-correction rule or evidence
of equivalent vertex layouts. Vertex fetch instructions are excluded; arithmetic
and control flow must match. Read only closed caches (the runtime locks its cache).
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct

from locate_shader_families import signature
from locate_shader_packages import require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, action='append', required=True)
    parser.add_argument('--guest', required=True)
    parser.add_argument('--shader-use', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Require a new report path')
    require(1 <= len(args.cache) <= 16 and len(args.shader_use) <= 32, 'Too many inputs')
    target = '%016X' % int(args.guest, 16)
    require(len(target) == 16, 'Guest hash outside 64-bit range')
    programs, inventories = {}, []
    for path in args.cache:
        require(8 <= path.stat().st_size <= 256 * 1024**2, 'Cache size outside bounds')
        raw = path.read_bytes()
        require(raw[:8] == b'XESH' + struct.pack('>I', 0x20201219), 'Unsupported cache header')
        at, seen = 8, set()
        while at < len(raw):
            require(at + 12 <= len(raw), 'Truncated cache record')
            guest, flags = struct.unpack_from('<QI', raw, at)
            size = (flags & 0x7fffffff) * 4
            require(0 < size <= 4 * 1024**2 and at + 12 + size <= len(raw), 'Invalid cache record')
            name = '%016X' % guest
            record = (flags >> 31, raw[at + 12:at + 12 + size])
            require(name not in seen, 'Duplicate shader in cache')
            require(name not in programs or programs[name] == record, 'Inconsistent cached shader')
            seen.add(name)
            programs[name] = record
            at += 12 + size
        digest = hashlib.sha256(raw).hexdigest()
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Cache changed during read')
        inventories.append(dict(path=str(path.resolve()), sha256=digest, programs=len(seen)))
    require(target in programs, 'Target missing from caches')
    target_stage, code = programs[target]
    wanted = signature(code, whole_fetch=True)
    matches, unsupported = [], []
    for guest, (stage, code) in sorted(programs.items()):
        if stage != target_stage:
            continue
        try:
            candidate = signature(code, whole_fetch=True)
        except (ValueError, struct.error) as error:
            unsupported.append(dict(guest=guest, reason=str(error)))
            continue
        if candidate == wanted:
            matches.append(dict(guest=guest, bytes=len(code),
                                sha256=hashlib.sha256(code).hexdigest()))
    match_names = {row['guest'] for row in matches}
    observations = []
    for path in args.shader_use:
        raw = path.read_bytes()
        rows = list(csv.DictReader(raw.decode().splitlines()))
        require(all('vs_hash' in row and 'ps_hash' in row for row in rows), 'Invalid draw-use columns')
        hits = [row for row in rows if '%016X' % int(row['vs_hash'], 16) in match_names]
        observations.append(dict(path=str(path.resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                                 rows=len(rows), matching_draw_use_rows=hits))
    result = dict(complete=True, target=target, stage=target_stage, caches=inventories,
                  unique_programs=len(programs), matches=matches, unsupported=unsupported,
                  observations=observations, limits=__doc__.strip())
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(matches=matches, unsupported=len(unsupported),
                         observed_matches=sum(len(r['matching_draw_use_rows']) for r in observations))))


if __name__ == '__main__':
    main()
