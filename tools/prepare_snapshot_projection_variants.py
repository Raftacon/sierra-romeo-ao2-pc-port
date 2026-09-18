"""Match complete source-snapshot translations to actual captured programs.

Require exact guest-control chunks and executable tokens; only source-map
comments may differ. Never insert export-time code into a shader missing its
earlier source captures.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
from equipment_projection_variants import chunks, shader_words, instructions


def normalized(data):
    parts=chunks(data)
    result={}
    for part in parts:
        tag=part[:4]
        if tag in result: raise ValueError('Duplicate DXBC chunk')
        if tag==b'SHEX':
            words=shader_words(parts)
            code=[word for _,op in instructions(words) if op[0]!=53 for word in op]
            values=[words[0],len(code)+2]+code
            part=tag+struct.pack('<I',len(values)*4)+struct.pack('<'+'I'*len(values),*values)
        result[tag]=part
    if b'SHEX' not in result: raise ValueError('Missing executable chunk')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inventory',type=Path)
    p.add_argument('matrix',type=Path)
    p.add_argument('output',type=Path)
    a=p.parse_args()
    if a.output.exists(): p.error('Require new output')
    scene=json.loads((a.inventory/'inventory.json').read_text())
    if not scene['complete']: raise ValueError('Incomplete capture inventory')
    coverage={r['guest']:r for r in csv.DictReader((a.matrix/'coverage.tsv').open(),delimiter='\t')}
    sources={}
    for row in scene['draws']:
        match=re.match(r'VS ([0-9A-F]{16})(?:,|$)',row['pipeline'])
        sha=row['shaders'].get('Vertex')
        if match and sha and coverage.get(match[1],{}).get('captured')=='1':
            if sha in sources and sources[sha]!=match[1]: raise ValueError('Ambiguous guest identity')
            sources[sha]=match[1]
    if not sources: raise ValueError('No source-snapshot programs are drawn in this capture')
    report={'scope':__doc__,'variants':[]}
    output_files={}
    for sha,guest in sources.items():
        captured=(a.inventory/(sha+'.dxbc')).read_bytes()
        if hashlib.sha256(captured).hexdigest()!=sha: raise ValueError('Captured shader hash changed')
        reference=normalized(captured)
        matches=[]
        for original in a.matrix.glob(guest+'-*-guest-original.dxbc'):
            raw=original.read_bytes()
            if normalized(raw)==reference:
                stem=original.name.removesuffix('-guest-original.dxbc')
                candidate=(a.matrix/(stem+'-precise.dxbc')).read_bytes()
                saved=(a.matrix/(stem+'-original.dxbc')).read_bytes()
                matches.append((original,candidate,saved))
        if not matches: raise ValueError('No exact compiler control matches captured '+guest)
        if any((candidate,saved)!=(matches[0][1],matches[0][2]) for _,candidate,saved in matches):
            raise ValueError('Equivalent controls have ambiguous replacements')
        original,candidate,saved=matches[0]
        output_files[sha+'.dxbc']=candidate
        output_files[sha+'-snapshot-only.dxbc']=saved
        report['variants'].append({'guest':guest,'original_sha256':sha,'file':sha+'.dxbc',
            'sha256':hashlib.sha256(candidate).hexdigest(),
            'snapshot_only_file':sha+'-snapshot-only.dxbc',
            'snapshot_only_sha256':hashlib.sha256(saved).hexdigest(),
            'matched_controls':[str(m[0]) for m in matches],
            'guest_control_sha256':hashlib.sha256(original.read_bytes()).hexdigest()})
    a.output.mkdir()
    for name,data in output_files.items(): (a.output/name).write_bytes(data)
    (a.output/'variants.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Matched',len(report['variants']),'captured shader programs')


if __name__=='__main__': main()
