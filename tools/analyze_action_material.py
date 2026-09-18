"""Decode pinned HUD materials and follow their output expressions.

Requires lzokay 2.1.0 and UE Viewer's AO2Game.xxx -list output. Reads the supplied
package without changing it; writes graph evidence, never a replacement asset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import lzokay


def analyze(package, listing, material_name='action_menu_Mat'):
    source_hash = hashlib.sha256(package).hexdigest()
    if source_hash != 'acd7f71baa2335f9773ac144623293cc2aa9752aaae76d37fbb224631ee47360':
        raise ValueError('Unsupported AO2Game package revision')
    flags, count = struct.unpack_from('>II', package, 0x5D)
    if flags != 2 or count != 38:
        raise ValueError('Unexpected compression table')
    chunks = [struct.unpack_from('>4I', package, 0x65 + i * 16) for i in range(count)]
    end = max(offset + size for offset, size, _, _ in chunks)
    if end > 64 * 1024 * 1024:
        raise ValueError('Package exceeds analysis bound')
    data = bytearray(end)
    data[:0x65] = package[:0x65]
    previous, blocks = 0x65, 0
    for offset, size, start, total in chunks:
        if offset != previous or start + total > len(package):
            raise ValueError('Noncontiguous or truncated chunk')
        magic, block_size, packed, unpacked = struct.unpack_from('>4I', package, start)
        if (magic, block_size, unpacked) != (0x9E2A83C1, 0x20000, size):
            raise ValueError('Unexpected compressed chunk header')
        number = (size + block_size - 1) // block_size
        table = [struct.unpack_from('>II', package, start + 16 + i * 8) for i in range(number)]
        cursor, dest = start + 16 + number * 8, offset
        if sum(c for c, _ in table) != packed or 16 + number * 8 + packed != total:
            raise ValueError('Compressed sizes disagree')
        for compressed, expanded in table:
            if compressed == expanded or not 0 < expanded <= block_size:
                raise ValueError('Unsupported block size')
            decoded = lzokay.decompress(package[cursor:cursor + compressed], expanded)
            if len(decoded) != expanded or dest + expanded > offset + size:
                raise ValueError('Decompressed size mismatch')
            data[dest:dest + expanded] = decoded
            cursor += compressed
            dest += expanded
            blocks += 1
        if cursor != start + total or dest != offset + size:
            raise ValueError('Chunk consumption mismatch')
        previous = dest
    def word(offset):
        return struct.unpack_from('>I', data, offset)[0]
    names, cursor = [], 0x65
    for _ in range(25351):
        length = word(cursor)
        if not 0 < length < 2048 or data[cursor + 4 + length - 1] != 0:
            raise ValueError('Unsupported name entry')
        names.append(data[cursor + 4:cursor + 3 + length].decode('latin1'))
        cursor += length + 12
    if cursor != 0xC49F5:
        raise ValueError('Unexpected name-table endpoint')
    text = listing.decode('utf-16') if listing[:2] in (b'\xff\xfe', b'\xfe\xff') else listing.decode('utf-8-sig')
    exports = {int(m[1]) + 1: {'offset': int(m[2], 16), 'size': int(m[3], 16),
                             'class': m[4], 'name': m[5].strip()}
               for m in re.finditer(r'^\s*(\d+)\s+([0-9A-F]+)\s+([0-9A-F]+)\s+(\w+)\s+(.+)$', text, re.M)}
    if len(exports) != 59442:
        raise ValueError('Expected the complete UE Viewer export listing')
    def properties(cursor, limit, depth=0):
        if depth > 8:
            raise ValueError('Nested property limit exceeded')
        result = {}
        while cursor + 8 <= limit:
            key = names[word(cursor)]
            cursor += 8
            if key == 'None':
                return result, cursor
            if cursor + 16 > limit:
                raise ValueError('Truncated property tag')
            kind, size, index = names[word(cursor)], word(cursor + 8), word(cursor + 12)
            cursor += 16
            extra = None
            if kind == 'StructProperty':
                extra = names[word(cursor)]
                cursor += 8
            elif kind == 'BoolProperty':
                # This retail revision serializes tag booleans as four bytes.
                extra = bool(word(cursor))
                cursor += 4
            end = cursor + size
            if end > limit or index != 0 or key in result:
                raise ValueError('Unsupported property bounds or array element')
            value = bytes(data[cursor:end]).hex()
            if kind == 'StructProperty':
                if extra.endswith('Input'):
                    fields, consumed = properties(cursor, end, depth + 1)
                    if consumed != end:
                        raise ValueError('Unconsumed material input bytes')
                    value = {'type': extra, 'fields': fields}
                else:
                    value = {'type': extra, 'hex': value}
            elif kind == 'ObjectProperty':
                value = {'export': struct.unpack_from('>i', data, cursor)[0]}
            elif kind == 'NameProperty':
                value = names[word(cursor)]
            elif kind == 'FloatProperty':
                value = struct.unpack_from('>f', data, cursor)[0]
            elif kind == 'IntProperty':
                value = struct.unpack_from('>i', data, cursor)[0]
            elif kind == 'BoolProperty':
                value = extra
            result[key] = value
            cursor = end
        raise ValueError('No property terminator')
    material_specs = {'action_menu_Mat': (38047, 0x12925E4, 0x5A5, 25),
                      'coopCallIcon_Mat': (38059, 0x129932F, 0x654, 31)}
    reference, expected_offset, expected_size, expression_count = material_specs[material_name]
    material = exports[reference]
    if (material['name'], material['offset'], material['size']) != (material_name, expected_offset, expected_size):
        raise ValueError('Unexpected material export')
    props, _ = properties(material['offset'] + 4, material['offset'] + material['size'])
    raw = bytes.fromhex(props['Expressions'])
    ids = struct.unpack('>' + str(len(raw) // 4) + 'I', raw)
    if ids[0] != expression_count or len(ids) != expression_count + 1 or len(set(ids[1:])) != expression_count:
        raise ValueError('Unexpected expression array')
    nodes = {}
    for index in ids[1:]:
        export = exports[index]
        node, consumed = properties(export['offset'] + 4, export['offset'] + export['size'])
        if consumed != export['offset'] + export['size']:
            raise ValueError('Unconsumed expression export bytes')
        nodes[index] = {**export, 'properties': node}
    roots = {key: props[key]['fields']['Expression']['export'] for key in ('EmissiveColor', 'Opacity')}
    def references(value):
        if isinstance(value, dict):
            if set(value) == {'export'}:
                yield value['export']
            else:
                for item in value.values():
                    yield from references(item)
    reached, pending = set(), list(roots.values())
    while pending:
        index = pending.pop()
        if index in reached:
            continue
        reached.add(index)
        if index in nodes:
            pending.extend(references(nodes[index]['properties']))
        elif index not in exports or exports[index]['class'] != 'Texture2D':
            raise ValueError('Unexpected expression leaf')
    return {'source_sha256': source_hash, 'listing_sha256': hashlib.sha256(listing).hexdigest(),
            'decoded_sha256': hashlib.sha256(data).hexdigest(), 'chunks': count, 'blocks': blocks,
            'roots': roots, 'nodes': nodes,
            'reachable': {i: exports[i] for i in sorted(reached)},
            'unreachable_expressions': sorted(set(nodes) - reached),
            'interpretation': 'Serialized graph reachability; validate the actual native material draw separately.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--exports', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--material', choices=['action_menu_Mat', 'coopCallIcon_Mat'], default='action_menu_Mat')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file')
    result = analyze(args.package.read_bytes(), args.exports.read_bytes(), args.material)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(f"Decoded {result['blocks']} blocks; {len(result['reachable'])} exports feed the outputs")


if __name__ == '__main__':
    main()
