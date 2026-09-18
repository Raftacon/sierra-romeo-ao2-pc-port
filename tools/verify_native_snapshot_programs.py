"""Verify that a closed native capture used the tested snapshot translations.

Matches all DXBC chunks and executable tokens, excluding only source-map
comments. Does not replace shaders or claim whole-scene visual correctness.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from prepare_snapshot_projection_variants import normalized


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe',type=Path,required=True)
    p.add_argument('--matrix',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists(): p.error('Require new output')
    done=json.loads((a.probe/'probe.json').read_text())
    travel=json.loads((a.probe/'travel.json').read_text())
    scene=json.loads((a.probe/'scene-inventory-001/inventory.json').read_text())
    if (done.get('timed_out') or done.get('exit_code_before_cleanup')!=0 or
        not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged'] or
        not scene['complete']): raise ValueError('Require normally completed capture with source files unchanged')
    log=(a.probe/'runtime.log').read_text(errors='replace')
    if 'AOT projection source snapshots: enabled=true' not in log or 'unsupported translation' in log:
        raise ValueError('Native snapshots not enabled or translation rejected')
    coverage={r['guest'] for r in csv.DictReader((a.matrix/'coverage.tsv').open(),delimiter='\t') if r['captured']=='1'}
    candidates={}
    for draw in scene['draws']:
        m=re.match(r'VS ([0-9A-F]{16})(?:,|$)',draw['pipeline'])
        digest=draw['shaders'].get('Vertex')
        if m and m[1] in coverage and digest:
            candidates.setdefault((m[1],digest),[]).append(draw['event'])
    if not candidates: raise ValueError('No snapshot-source shader drawn')
    report={'complete':False,'scope':__doc__.strip(),'programs':[]}
    for (guest,digest),events in candidates.items():
        code=(a.probe/'scene-inventory-001'/(digest+'.dxbc')).read_bytes()
        if hashlib.sha256(code).hexdigest()!=digest: raise ValueError('Captured shader changed')
        reference=normalized(code)
        matches=[]
        for path in a.matrix.glob(guest+'-*-precise.dxbc'):
            raw=path.read_bytes()
            if normalized(raw)==reference:
                matches.append({'path':str(path.resolve()),'sha256':hashlib.sha256(raw).hexdigest()})
        if not matches: raise ValueError('Native shader differs from tested correction: '+guest)
        report['programs'].append({'guest':guest,'native_sha256':digest,'draw_events':events,'matches':matches})
    report['complete']=True
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'complete':True,'programs':len(report['programs'])}))


if __name__=='__main__':main()
