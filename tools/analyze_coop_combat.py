"""Compare settled read-only health/ammunition snapshots from two co-op peers.

Object addresses are process-local; comparison uses reflected paths, FName
numbers and classes. Matching these sampled fields is not full combat parity.
"""
import argparse
import hashlib
import json
from pathlib import Path


def identity(item):
    return [item['path'], item['name_number'], item['class']]


def read_snapshot(path):
    data = path.read_bytes()
    snapshot = json.loads(data)
    if snapshot['failed_reads']:
        raise ValueError(f'{path}: failed memory reads')
    native = snapshot['campaign_network']
    entities = {}
    for item in native['combat_state']:
        key = json.dumps(identity(item), separators=(',', ':'))
        if key in entities:
            raise ValueError(f'{path}: ambiguous actor identity {key}')
        entities[key] = {field: identity(value) if isinstance(value, dict) else value
                         for field, value in item['values'].items()}
    if not entities:
        raise ValueError(f'{path}: no live combat-state actors')
    properties = {key: {field: item[field] for field in ['type', 'offset']}
                  for key, item in native['combat_properties'].items()}
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(data).hexdigest(),
                sampling_window=native.get('combat_sampling_window'),
                sampled_consecutive_mismatches=int(native['simulation']['words'][0x134//4], 16)), entities, properties


def differences(before, after):
    result = []
    for key in sorted(before.keys() | after.keys()):
        if before.get(key) != after.get(key):
            result.append(dict(entity=json.loads(key), before=before.get(key), after=after.get(key)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True, help='Host native probe directory')
    parser.add_argument('--peer', type=Path, required=True, help='Peer native probe directory')
    parser.add_argument('--stages', nargs='+', default=['before', 'host-fire', 'host-reload', 'peer-fire', 'peer-reload'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file')
    if len(set(args.stages)) != len(args.stages) or any(not s or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in s) for s in args.stages):
        parser.error('Use unique simple stage names')
    report = dict(stages={}, limitations=[
        'Processes are read independently without suspension; snapshots are not atomic.',
        'Matching sampled health/ammunition does not prove hits, damage, enemy AI or full combat parity.'])
    previous = {}
    mismatch = False
    for stage in args.stages:
        observations, entities, properties = {}, {}, {}
        for role, folder in [('host', args.host), ('peer', args.peer)]:
            observations[role], entities[role], properties[role] = read_snapshot(folder/f'combat-{stage}.json')
        diff = differences(entities['host'], entities['peer'])
        same_properties = properties['host'] == properties['peer']
        windows = {r: v['sampling_window'] for r, v in observations.items()}
        stable_frames = {r: window['begin']['consumed']
                         if window and window['begin']['consumed'] == window['end']['consumed'] else None
                         for r, window in windows.items()}
        same_frame = stable_frames['host'] is not None and stable_frames['host'] == stable_frames['peer']
        mismatch |= bool(diff) or not same_properties or any(x['sampled_consecutive_mismatches'] for x in observations.values())
        report['stages'][stage] = dict(sources=observations, entity_counts={r: len(v) for r, v in entities.items()},
            stable_consumed_frames=stable_frames, sampled_same_consumed_frame=same_frame,
            reflected_layouts_match=same_properties, peer_differences=diff,
            changes_since_previous={r: differences(previous[r], entities[r]) for r in previous})
        previous = entities
    report['sampled_fields_match'] = not mismatch
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(f"Compared {len(args.stages)} stages; sampled fields match: {not mismatch}.")
    return int(mismatch)


if __name__ == '__main__':
    raise SystemExit(main())
