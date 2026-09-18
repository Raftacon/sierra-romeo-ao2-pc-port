"""Summarize bounded native eviction/fence observations, not unobserved copies."""
import argparse
import collections
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    p = args.probe
    native = json.loads((p / 'probe.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup'] != 0:
        raise ValueError('Require normal native completion')
    if native['input_environment'].get('AOT_TRACE_READBACK_RETIREMENT') != '1':
        raise ValueError('Retirement observation was not enabled')
    text = (p / 'runtime.log').read_text(errors='replace')
    records = []
    for line in text.splitlines():
        if 'Readback retirement: ' not in line:
            continue
        fields = dict(re.findall(r'(\w+)=([0-9]+|true|false)', line.split('Readback retirement: ', 1)[1]))
        expected = {'frame','key','count','last_frame','written0','written1','completed','retire','bytes0','bytes1','limit'}
        if set(fields) != expected or fields.pop('limit') != 'false':
            raise ValueError('Malformed or truncated retirement trace')
        row = {key:int(value) for key,value in fields.items()}
        if row['completed'] == 2**64-1 or any(row['written'+str(i)] > row['retire'] for i in (0,1)):
            raise ValueError('Invalid completion/writer observation')
        row['cause'] = 'capacity' if row['count'] > 256 else 'age'
        row['pending_slots'] = [i for i in (0,1) if row['written'+str(i)] > row['completed']]
        if row['cause'] == 'age' and row['frame'] - row['last_frame'] <= 60:
            raise ValueError('Eviction with no supported trigger')
        records.append(row)
    pending = [row for row in records if row['pending_slots']]
    report = {
        'complete':True, 'observed_evictions':len(records),
        'causes':dict(collections.Counter(row['cause'] for row in records)),
        'largest_cache_at_eviction':max((row['count'] for row in records), default=None),
        'evicted_allocated_bytes':sum(row['bytes0']+row['bytes1'] for row in records),
        'pending_evictions':len(pending),
        'pending_slots':sum(len(row['pending_slots']) for row in pending),
        'pending_allocated_bytes':sum(row['bytes'+str(i)] for row in pending for i in row['pending_slots']),
        'pending_records':pending,
        'executable_sha256':native['executable_sha256'], 'gpu_plugin':native['gpu_plugin'],
        'limits':'Actual fence values sampled at observed evictions. Does not test CPU reads of still-cached buffers, prove absence of leaks, or establish a visual/performance fix.'}
    (p/'readback-retirement-analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='pending_records'},indent=2))


if __name__ == '__main__':
    main()
