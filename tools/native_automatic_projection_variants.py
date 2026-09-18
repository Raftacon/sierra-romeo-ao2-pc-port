"""Apply audited arithmetic layouts to all recognized VS programs in a capture.

Uses guest identities from named native pipelines, not a material whitelist.
Inputs are closed-frame inventory plus compiler audit/matrix reports.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from equipment_projection_variants import chunks

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inventory',type=Path);p.add_argument('matrix',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--guest',action='append',default=[],help='Limit this comparison to an explicitly selected guest shader; repeatable')
    a=p.parse_args()
    if a.output.exists():p.error('Require new output')
    d=json.loads((a.inventory/'inventory.json').read_text())
    if not d['complete']:raise ValueError('Incomplete inventory')
    coverage={r['guest']:r for r in csv.DictReader((a.matrix/'coverage.tsv').open(),delimiter='\t')}
    metadata={}
    for r in csv.DictReader((a.matrix/'translations.tsv').open(),delimiter='\t'):
        if r['modification']=='0000000000000000' and r['bindless']=='1' and r['rov']=='0':metadata[r['guest']]=r
    guests={}
    for r in d['draws']:
        m=re.match(r'VS ([0-9A-F]{16})(?:,|$)',r['pipeline']);sha=r['shaders'].get('Vertex')
        if m and sha:
            if sha in guests and guests[sha]!=m[1]:raise ValueError('Ambiguous guest identity')
            guests[sha]=m[1]
    helper=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    selected={g.upper() for g in a.guest}
    if any(not re.fullmatch('[0-9A-F]{16}',g) for g in selected) or selected-set(guests.values()):
        raise ValueError('Invalid or absent selected guest shader')
    a.output.mkdir();report={'variants':[],'unchanged':[],'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}
    for sha,guest in guests.items():
        if selected and guest not in selected:
            report['unchanged'].append({'guest':guest,'sha256':sha,'reason':'outside selected diagnostic comparison'});continue
        source=a.inventory/(sha+'.dxbc');raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Inventory binary hash mismatch')
        c=coverage.get(guest)
        if c is None or c['world']=='4294967295':
            report['unchanged'].append({'guest':guest,'sha256':sha,'reason':'unrecognized arithmetic'});continue
        if c.get('captured')=='1':
            raise ValueError('Source snapshots require the complete translated candidate, not export-time patching')
        feature=next(x for x in chunks(raw) if x[:4]==b'SFI0')
        if struct.unpack_from('<I',feature,8)[0]&1:
            report['unchanged'].append({'guest':guest,'sha256':sha,'reason':'already uses double operations; retained captured binary'});continue
        m=metadata[guest];target=a.output/(sha+'.dxbc')
        command=[str(helper),guest,'0',str(source),str(target)]+[m[k] for k in ('position','vector_result','system_constants','float_constants')]+[c['world'],*c['components']]
        subprocess.run(command,check=True,timeout=10)
        report['variants'].append({'guest':guest,'original_sha256':sha,'file':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'command':command})
    (a.output/'variants.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Patched',len(report['variants']),'unique shaders; retained',len(report['unchanged']))

if __name__=='__main__':main()
