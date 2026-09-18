"""Find serialized vertex-shader candidates with identical ALU/control flow.

The default ignores two data-layout words of CF-identified vertex fetches.
Broader explicit modes locate candidates by full fetch exclusion or ordered ALU
instructions alone. This is an asset-location heuristic, never a correction or
proof that different vertex declarations are semantically equivalent.
"""
import argparse
import json
from pathlib import Path
import struct
import time
import lzokay
from locate_shader_packages import decode, cache_programs, require, sha
from inspect_package_shaders import tables, shaders

EXEC = (1,2,3,4,5,6,13,14)


def signature(code, whole_fetch=False, arithmetic_only=False):
    require(len(code) >= 12 and len(code) % 4 == 0, 'Invalid instruction bytes')
    words = struct.unpack('>'+'I'*(len(code)//4), code)
    bound, i = len(words)//3, 0
    pairs = []
    while i < bound:
        a,b,c = words[i*3:i*3+3]
        pair = (a | (b & 0xffff) << 32, (b >> 16) | c << 16)
        pairs.extend(pair)
        for cf in pair:
            if cf >> 44 in EXEC: bound = min(bound, cf & 4095)
        i += 1
    require(i == bound and bound > 0, 'Invalid CF instruction boundary')
    kinds = {}
    for cf in pairs:
        if cf >> 44 not in EXEC: continue
        address, count, sequence = cf & 4095, (cf >> 12) & 7, (cf >> 16) & 4095
        require(address >= bound and address+count <= len(words)//3, 'Exec outside instructions')
        for offset in range(count):
            index, fetch = address+offset, bool(sequence & (1 << (offset*2)))
            require(index not in kinds or kinds[index] == fetch, 'Instruction interpreted as both ALU and fetch')
            kinds[index] = fetch
    normalized = bytearray(code)
    fetches = sorted(index for index,is_fetch in kinds.items() if is_fetch and words[index*3] & 31 == 0)
    if arithmetic_only:
        return sha(b''.join(code[index*12:index*12+12] for index,is_fetch in sorted(kinds.items()) if not is_fetch)), fetches
    for index in fetches:
        begin = index*12 + (0 if whole_fetch else 4)
        normalized[begin:index*12+12] = b'\0'*(12 if whole_fetch else 8)
    return sha(normalized), fetches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--guest', action='append', required=True)
    parser.add_argument('--glob', default='*.xxx')
    parser.add_argument('--fetch-scope', choices=('layout','instruction'), default='layout',
                        help='Candidate locator only: ignore layout words or complete CF-identified vertex fetches')
    parser.add_argument('--match-scope', choices=('control-flow','arithmetic'), default='control-flow',
                        help='Optional weaker lookup: match ordered ALU instructions without assuming unchanged CF/fetch addresses')
    args = parser.parse_args()
    require(not args.output.exists(), 'Require a new output directory')
    wanted = {'%016X' % int(g,16) for g in args.guest}
    require(1 <= len(wanted) <= 16, 'Require one to sixteen targets')
    programs, cache_hash = cache_programs(args.cache, wanted)
    whole_fetch = args.fetch_scope == 'instruction'
    arithmetic_only = args.match_scope == 'arithmetic'
    targets = {g: signature(c, whole_fetch, arithmetic_only) for g,c in programs.items()}
    files = sorted(args.packages.glob(args.glob))
    require(0 < len(files) <= 2048, 'Invalid package selection')
    args.output.mkdir(parents=True)
    report = {'complete': False, 'cache_sha256': cache_hash,
              'target_signatures': targets, 'packages': [], 'candidates': [], 'unsupported': [],
              'scope': __doc__.strip(), 'fetch_scope': args.fetch_scope, 'match_scope': args.match_scope}
    started = time.monotonic()
    try:
        for index,path in enumerate(files):
            require(path.stat().st_size <= 256*1024*1024, 'Input package exceeds bound')
            raw = path.read_bytes()
            item = {'path': str(path.resolve()), 'source_sha256': sha(raw)}
            try:
                data, meta = decode(raw)
                names, exports = tables(data,meta)
                item.update(decoded_sha256=sha(data),vertex_programs=0,shader_caches=0)
                for export in exports:
                    if export['class'] != 'ShaderCache': continue
                    values, details = shaders(data,names,export)
                    item['shader_caches'] += 1
                    for p in values:
                        if p['frequency'] != 0: continue
                        item['vertex_programs'] += 1
                        if not arithmetic_only and len(p['gpu']) not in {len(c) for c in programs.values()}: continue
                        try:
                            normalized, fetches = signature(p['gpu'], whole_fetch, arithmetic_only)
                        except ValueError as error:
                            report['unsupported'].append({'package': path.name, 'shader_index': p['index'],
                                                          'type': p['type'], 'reason': str(error)})
                            continue
                        for guest, (target_sig, target_fetches) in targets.items():
                            if normalized != target_sig or (not arithmetic_only and fetches != target_fetches): continue
                            diffs = [{'byte_offset': at, 'package': a, 'runtime': b}
                                     for at,(a,b) in enumerate(zip(p['gpu'],programs[guest])) if a != b]
                            record = {k:v for k,v in p.items() if k not in ('code','gpu')}
                            report['candidates'].append(dict(record, guest=guest, package=path.name,
                                export=export['name'], fetch_instruction_indices=fetches, differences=diffs,
                                runtime_program_size=len(programs[guest]),
                                difference_scope='Byte offsets in overlapping program lengths; arithmetic mode does not establish alignment'))
            except (ValueError,struct.error,lzokay.LzokayError) as error:
                item['unsupported'] = str(error)
                report['unsupported'].append({'package': path.name, 'reason': str(error)})
            require(sha(path.read_bytes()) == item['source_sha256'], 'Source package changed')
            report['packages'].append(item)
            if index % 100 == 0:
                print(json.dumps({'scanned': index+1, 'candidates': len(report['candidates']),
                                  'unsupported': len(report['unsupported'])}),flush=True)
        require(sha(args.cache.read_bytes()) == cache_hash, 'Source cache changed')
        report['sources_unchanged'] = True
        report['complete'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        report['seconds'] = time.monotonic()-started
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'complete': True, 'candidates': len(report['candidates']),
                      'unsupported': len(report['unsupported']), 'seconds': report['seconds']}))


if __name__ == '__main__': main()
