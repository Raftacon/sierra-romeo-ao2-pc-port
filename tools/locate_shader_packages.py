"""Locate complete cached guest programs in read-only, decoded retail packages.

Supports this game's 445/79 big-endian package summary and LZO chunk layout.
Matches are serialized asset evidence, not proof that a shader was drawn.
See UE Viewer Unreal/UnrealPackage/UnPackage3.cpp and UnPackage.h for the
versioned summary, generation and compressed-chunk field order.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time
import lzokay


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode(raw):
    require(110 <= len(raw) <= 256*1024*1024, 'Package size outside bounds')
    require(raw[:8] == bytes.fromhex('9e2a83c1004f01bd'), 'Unsupported package revision')
    length = struct.unpack_from('>i', raw, 12)[0]
    require(0 < length <= 1024 and 16+length <= len(raw) and raw[15+length] == 0,
            'Unsupported package-group string')
    at = 16 + length + 4
    require(at+48 <= len(raw), 'Truncated summary')
    tables = struct.unpack_from('>7i', raw, at)
    at += 28 + 16
    generations = struct.unpack_from('>i', raw, at)[0]
    require(0 <= generations <= 64, 'Invalid generation count')
    at += 4 + generations * 12
    require(at+16 <= len(raw), 'Truncated compression header')
    engine, cooker, flags, count = struct.unpack_from('>4I', raw, at)
    require((engine, cooker) == (3004, 41), 'Unsupported engine/cooker revision')
    at += 16
    require(count <= 256 and at + count*16 <= len(raw), 'Invalid chunk count')
    meta = {'group': raw[16:15+length].decode('ascii'), 'name_count': tables[0],
            'name_offset': tables[1], 'export_count': tables[2], 'export_offset': tables[3],
            'import_count': tables[4], 'import_offset': tables[5], 'depends_offset': tables[6],
            'compression': flags, 'chunks': count, 'summary_end': at}
    require(all(0 <= value <= 256*1024*1024 for value in tables), 'Invalid table bounds')
    if flags == 0:
        require(count == 0, 'Uncompressed package has chunks')
        require(all(tables[i] <= len(raw) for i in (1,3,5,6)), 'Tables outside package')
        return raw, meta
    require(flags == 2 and count > 0, 'Unsupported package compression')
    chunks = [struct.unpack_from('>4I', raw, at+i*16) for i in range(count)]
    end = max(offset+size for offset,size,_,_ in chunks)
    require(at < end <= 256*1024*1024, 'Decoded package size outside bounds')
    data = bytearray(end)
    data[:at] = raw[:at]
    previous, packed_end = at, at + count*16
    for offset, size, start, total in chunks:
        require(offset == previous and size > 0 and offset+size <= end and
                start == packed_end and total >= 16 and start+total <= len(raw),
                'Noncontiguous or truncated chunk')
        magic, block_size, packed, unpacked = struct.unpack_from('>4I', raw, start)
        require((magic, block_size, unpacked) == (0x9E2A83C1, 0x20000, size),
                'Unexpected compressed chunk header')
        blocks = (size + block_size-1) // block_size
        require(16+blocks*8 <= total, 'Truncated block table')
        entries = [struct.unpack_from('>2I', raw, start+16+i*8) for i in range(blocks)]
        require(sum(c for c,_ in entries) == packed and 16+blocks*8+packed == total and
                sum(u for _,u in entries) == size, 'Block sizes disagree')
        cursor, dest = start+16+blocks*8, offset
        for compressed, expanded in entries:
            require(0 < compressed <= packed and 0 < expanded <= block_size and
                    cursor+compressed <= start+total and dest+expanded <= offset+size,
                    'Invalid block bounds')
            # Equal lengths alone do not imply raw storage: try LZO normally.
            block = lzokay.decompress(raw[cursor:cursor+compressed], expanded)
            require(len(block) == expanded, 'Decompressed block size mismatch')
            data[dest:dest+expanded] = block
            cursor += compressed
            dest += expanded
        require(cursor == start+total and dest == offset+size, 'Chunk consumption mismatch')
        previous, packed_end = dest, start+total
    require(all(tables[i] <= end for i in (1,3,5,6)), 'Tables outside decoded package')
    return data, meta


def cache_programs(path, wanted):
    require(8 <= path.stat().st_size <= 256*1024*1024, 'Cache size outside bounds')
    raw = path.read_bytes()
    require(raw[:8] == b'XESH' + struct.pack('>I', 0x20201219), 'Unsupported cache header')
    at, result = 8, {}
    while at < len(raw):
        require(at+12 <= len(raw), 'Truncated cache record')
        guest, flags = struct.unpack_from('<QI', raw, at)
        size = (flags & 0x7fffffff)*4
        require(0 < size <= 4*1024*1024 and at+12+size <= len(raw), 'Invalid cache record size')
        name = '%016X' % guest
        if name in wanted:
            require(name not in result, 'Duplicate target in cache')
            result[name] = raw[at+12:at+12+size]
        at += 12+size
    require(set(result) == set(wanted), 'Missing requested guest program')
    return result, sha(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--guest', action='append', required=True)
    parser.add_argument('--glob', default='*.xxx')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists(): parser.error('Require a new output directory')
    wanted = {('%016X' % int(g,16)) for g in args.guest}
    require(1 <= len(wanted) <= 16, 'Require one to sixteen guest programs')
    programs, cache_hash = cache_programs(args.cache, wanted)
    files = sorted(args.packages.glob(args.glob))
    require(1 <= len(files) <= 2048 and all(p.is_file() for p in files), 'Invalid package selection')
    out.mkdir(parents=True)
    report = {'complete': False, 'cache': str(args.cache.resolve()), 'cache_sha256': cache_hash,
              'programs': {name: {'size': len(code), 'sha256': sha(code)} for name,code in programs.items()},
              'packages': [], 'matches': [], 'unsupported': [],
              'scope': 'Full byte-sequence matches in serialized assets, not live draw use'}
    started = time.monotonic()
    try:
        for index, path in enumerate(files):
            require(path.stat().st_size <= 256*1024*1024, 'Package exceeds input size bound')
            raw = path.read_bytes()
            item = {'path': str(path.resolve()), 'source_sha256': sha(raw), 'source_size': len(raw)}
            try:
                data, meta = decode(raw)
                item.update(meta, decoded_sha256=sha(data), decoded_size=len(data))
                for guest, code in programs.items():
                    # Cache instructions are big-endian. Also test word reversal
                    # explicitly rather than guessing a package's shader spelling.
                    variants = [('cache-big-endian', code), ('word-reversed', b''.join(code[i:i+4][::-1] for i in range(0,len(code),4)))]
                    for endian, needle in variants:
                        begin = 0
                        while True:
                            at = data.find(needle, begin)
                            if at < 0: break
                            require(len(report['matches']) < 10000, 'Too many matches')
                            report['matches'].append({'package': path.name, 'guest': guest,
                                'serialized_offset': at, 'bytes': len(needle), 'endian': endian})
                            begin = at+len(needle)
            except (ValueError, struct.error, lzokay.LzokayError) as error:
                item['unsupported'] = str(error)
                report['unsupported'].append({'package': path.name, 'reason': str(error)})
            # Check source bytes after decoding, before advancing to another file.
            require(sha(path.read_bytes()) == item['source_sha256'], 'Source package changed during scan')
            report['packages'].append(item)
            if index % 100 == 0:
                print(json.dumps({'scanned': index+1, 'matches': len(report['matches']),
                                  'unsupported': len(report['unsupported'])}), flush=True)
        require(sha(args.cache.read_bytes()) == cache_hash, 'Source cache changed during scan')
        report['sources_unchanged'] = True
        report['complete'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        report['seconds'] = time.monotonic()-started
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('complete','matches','unsupported','seconds')},indent=2))


if __name__ == '__main__':
    main()
