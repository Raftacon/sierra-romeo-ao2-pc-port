"""Label closed-capture GPU costs using exact SDK bytecode and shader semantics.

Costs are sums of per-event replay medians, not native GPU frame durations.
Categories partition events; stage costs in the input grouping overlap.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from native_projection_variants import disassemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.groups.read_bytes()
    source = json.loads(raw)
    if not source['complete'] or source.get('error') or args.output.exists():
        raise ValueError('Require completed groups and new output')
    sdk_shaders = {}
    for header in (args.sdk / 'src').glob('**/bytecode/d3d12_5_1/*.h'):
        match = re.search(r'const\s+BYTE\s+\w+\[\]\s*=\s*\{([^}]*)\}', header.read_text(), re.S)
        if match:
            code = bytes(int(n, 0) for n in re.findall(r'0x[\da-fA-F]+|\d+', match[1]))
            sdk_shaders[hashlib.sha256(code).hexdigest()] = str(header)
    shaders = {}
    for sha, metadata in source['shaders'].items():
        code = (args.groups.parent / (sha + '.dxbc')).read_bytes()
        if hashlib.sha256(code).hexdigest() != sha:
            raise ValueError('Captured shader hash mismatch')
        shaders[sha] = dict(metadata, sdk_header=sdk_shaders.get(sha), assembly=disassemble(code))
    categories = {}
    for event in source['events']:
        stages = event.get('shaders', {})
        vs = shaders.get(stages.get('Vertex'), {})
        ps = shaders.get(stages.get('Pixel'), {})
        cs = shaders.get(stages.get('Compute'), {})
        header = Path(vs.get('sdk_header') or '').name
        if header == 'passthrough_position_xy_vs.h':
            assembly = ps.get('assembly', '')
            if 'xe_transfer_stencil_mask' in assembly and 'discard_' in assembly:
                category = 'render_target_transfer_stencil_bits'
            elif re.search(r'^dcl_output oDepth', assembly, re.M):
                category = 'render_target_transfer_depth'
            else:
                category = 'render_target_transfer_color_or_other'
        elif header == 'guest_output_triangle_strip_rect_vs.h':
            category = 'output_' + Path(ps.get('sdk_header') or 'unknown').stem
        elif cs.get('sdk_header'):
            category = 'compute_' + Path(cs['sdk_header']).stem
        elif 'Vertex' in stages:
            category = 'guest_draw_with_doubles' if (vs.get('feature_flags') or 0) & 1 else 'guest_draw_without_doubles'
        else:
            category = event['name'].split('(')[0]
        entry = categories.setdefault(category, {'name': category, 'count': 0, 'median_sum_ms': 0.,
                                                  'pass_sum_ms': [0., 0., 0.], 'events': []})
        entry['count'] += 1
        entry['median_sum_ms'] += event['median_ms']
        entry['pass_sum_ms'] = [a + b for a, b in zip(entry['pass_sum_ms'], event['samples_ms'])]
        entry['events'].append(event['event'])
    report = {'complete': True, 'source': str(args.groups.resolve()),
              'source_sha256': hashlib.sha256(raw).hexdigest(), 'capture_sha256': source['capture_sha256'],
              'limits': __doc__, 'sdk_matches': {k: v['sdk_header'] for k, v in shaders.items() if v['sdk_header']},
              'categories': sorted(categories.values(), key=lambda r: r['median_sum_ms'], reverse=True)}
    if sum(c['count'] for c in categories.values()) != len(source['events']):
        raise ValueError('Attribution does not partition the measured events')
    guest_csv = args.groups.parent.parent / 'draws.csv'
    if guest_csv.is_file():
        with guest_csv.open() as f:
            guest_draws = list(csv.DictReader(f))
        frames = sorted({r['gpu_frame'] for r in guest_draws})
        attributed = sum(c['count'] for name, c in categories.items() if name.startswith('guest_draw_'))
        report['guest_draw_check'] = {'csv': str(guest_csv.resolve()), 'frames': frames,
                                     'csv_count': len(guest_draws), 'attributed_count': attributed}
        if len(frames) != 1 or attributed != len(guest_draws):
            raise ValueError('Attributed guest draws do not match the single-frame native CSV')
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for category in report['categories']:
        print('%5d %9.4f ms %s' % (category['count'], category['median_sum_ms'], category['name']))


if __name__ == '__main__':
    main()
