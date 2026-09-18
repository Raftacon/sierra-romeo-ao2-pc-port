"""Verify captured depth conversion and distinguish current/adjacent sources."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def quantize(value):
    bits = struct.unpack('<I', struct.pack('<f', value * 2))[0]
    return struct.unpack('<f', struct.pack('<I', bits & ~7))[0]


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    source = args.source / 'source.json'
    report = json.loads(source.read_text())
    if not report.get('complete') or report.get('error'):
        raise ValueError('Require a completed source replay')
    output = args.source / 'conversion.json'
    if output.exists(): raise ValueError('Require new output')
    values = {}
    for row in report['sources']:
        raw = (args.source / (row['label'] + '.bin')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256'] or len(raw) != 25 * row['stride']:
            raise ValueError('Changed source readback')
        values[row['label']] = [struct.unpack_from('<f', raw, i * row['stride'])[0] for i in range(25)]
    resolved, copied = values['resolved'], values['copied']
    comparisons = []
    for label, depths in values.items():
        if not label.startswith('epoch-'): continue
        converted = [quantize(v) for v in depths]
        comparisons.append({'label': label,
            'matching_pixels': sum(a == b for a, b in zip(converted, resolved)),
            'center_match': converted[12] == resolved[12],
            'center_converted': converted[12],
            'center_difference': converted[12] - resolved[12]})
    expected = [f32(f32(v - f32(1e-6)) * 0.5) for v in resolved]
    copy_matches = sum(a == b for a, b in zip(expected, copied))
    result = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'scope': 'Captured 5x5 region around (620,260), epoch 20; no global timing verdict',
              'depth_copy_matching_pixels': copy_matches, 'pixels': 25,
              'source_comparisons': comparisons}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
