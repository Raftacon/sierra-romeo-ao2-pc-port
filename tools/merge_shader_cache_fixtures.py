"""Combine closed-run guest caches for the offline translation matrix.

Preserves records byte-for-byte, rejects conflicting duplicates and malformed
bounds, and records provenance. The matrix independently checks every guest
XXH3 checksum before translation. Never use this to replace a live game cache.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New fixture directory')
    parser.add_argument('caches', type=Path, nargs='+')
    args = parser.parse_args()
    if not 1 <= len(args.caches) <= 8: parser.error('Require one to eight caches')
    if args.output.exists(): parser.error('Require new output directory')
    header = b'XESH'+struct.pack('>I', 0x20201219)
    records, sources = {}, []
    for path in args.caches:
        if not 8 <= path.stat().st_size <= 256*1024*1024: raise ValueError('Unbounded cache')
        data = path.read_bytes()
        if data[:8] != header: raise ValueError('Unknown cache header')
        at, count = 8, 0
        while at < len(data):
            if len(data)-at < 12: raise ValueError('Truncated record header')
            guest, flags = struct.unpack_from('<QI', data, at)
            words = flags & 0x7fffffff
            if not 0 < words <= 0x100000 or words*4 > len(data)-at-12: raise ValueError('Invalid record bounds')
            end = at+12+words*4; record = data[at:end]
            if guest in records and records[guest] != record: raise ValueError('Conflicting guest records')
            records.setdefault(guest, record); at = end; count += 1
        sources.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(), 'records': count})
    result = header+b''.join(records.values())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'combined.xsh').write_bytes(result)
    (args.output/'sources.json').write_text(json.dumps({'sources': sources, 'unique_records': len(records),
        'sha256': hashlib.sha256(result).hexdigest(), 'guest_checksum_validation': 'Required by aot_projection_matrix'}, indent=2)+'\n')
    print(args.output/'combined.xsh')


if __name__ == '__main__': main()
