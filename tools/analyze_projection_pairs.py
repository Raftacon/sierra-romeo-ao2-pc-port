"""Resolve reused meshes in the first-frame projection-pass inventory.

Matches index bindings, vertex fetch 95, model and projection matrices. This
establishes pass correspondence, not equality of every transformed vertex.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

DEPTH = '3097f03d3ce3ebd7ea1b700f450daf6f1414c03d0b188e278604ffa172360112'
COLOR = '054f88253fa4c3831a5b1310a5c526a33351bc1a9fefdce67301804c4a299817'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    args = parser.parse_args()
    path = args.inventory
    output = path / 'correspondence.json'
    if output.exists(): parser.error('Correspondence report already exists')
    report = json.loads((path / 'pairs.json').read_text())
    if report.get('error'): raise ValueError('Incomplete GPU inventory: ' + report['error'])
    rows = {r['event']: r for r in report['draws']}
    if len(rows) != len(report['draws']): raise ValueError('Duplicate events')
    def constant(event, name):
        item = next(b for b in rows[event]['constants'] if b['name'] == name)
        if Path(item['file']).name != item['file']: raise ValueError('Invalid capture filename')
        raw = (path / item['file']).read_bytes()
        if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Changed constant readback')
        return raw
    def key(row):
        return tuple(row[k] for k in ('indices','index_offset','base_vertex','instances','ib_resource','ib_offset','ib_stride'))
    result = {'capture': report['capture'], 'scope': __doc__.strip(), 'pairs': [], 'unmatched': []}
    for candidate in report['depth_candidates']:
        depth = candidate['depth_event']
        if rows[depth]['vs_sha256'] != DEPTH: raise ValueError('Unexpected depth shader')
        df = constant(depth, 'xe_float_cbuffer')
        dv = constant(depth, 'xe_fetch_cbuffer')
        ds = constant(depth, 'xe_system_cbuffer')
        if len(df) != 4096 or len(dv) != 768 or len(ds) != 464: raise ValueError('Unexpected buffer layout')
        if struct.unpack_from('<I', ds)[0] & 14 != 8: raise ValueError('Unexpected depth position mode')
        matched, same_viewport, alternate_viewport = [], [], []
        for color in candidate['matching_index_draws']:
            if key(rows[depth]) != key(rows[color]): raise ValueError('Index binding mismatch')
            if rows[color]['vs_sha256'] == DEPTH: continue
            cf = constant(color, 'xe_float_cbuffer')
            cv = constant(color, 'xe_fetch_cbuffer')
            cs = constant(color, 'xe_system_cbuffer')
            if (df[:64] != cf[:64] or df[80:144] != cf[80:144] or dv[760:768] != cv[760:768]): continue
            if rows[color]['vs_sha256'] != COLOR: raise ValueError('Corresponding color shader is uncorrected')
            if struct.unpack_from('<I', cs)[0] & 14 != 8: raise ValueError('Unexpected color position mode')
            # Only xyz in system rows 8 and 9 belongs to host NDC conversion.
            # The later color pass uses a shorter viewport and correspondingly
            # different Y conversion; don't mislabel its clip output identical.
            if ds[128:140] + ds[144:156] == cs[128:140] + cs[144:156]:
                same_viewport.append(color)
            else:
                alternate_viewport.append(color)
            matched.append(color)
        item = {'depth_event': depth, 'color_events': matched, 'indices': rows[depth]['indices'],
                'matching_viewport_color_events': same_viewport,
                'alternate_viewport_color_events': alternate_viewport,
                'vertex_fetch95_hex': dv[760:768].hex(),
                'model_sha256': hashlib.sha256(df[80:144]).hexdigest(),
                'projection_sha256': hashlib.sha256(df[:64]).hexdigest()}
        result['pairs'].append(item)
        if len(matched) != 2 or len(same_viewport) != 1 or len(alternate_viewport) != 1:
            result['unmatched'].append(item)
    if len(result['pairs']) != 40 or result['unmatched']:
        raise ValueError('Expected all 40 depth draws to resolve to their two color passes')
    result['inventory_sha256'] = hashlib.sha256((path / 'pairs.json').read_bytes()).hexdigest()
    output.write_text(json.dumps(result, indent=2)+'\n')
    print('Resolved 40 depth draws to 80 corresponding corrected color passes.')


if __name__ == '__main__':
    main()
