"""Compare the same captured equipment draws before/after projection correction.

Fixed-ROI brightness statistics describe this sequence, not general visual
parity, motion alignment, native frame rate, or all reported flicker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--corrected',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    reports=[json.loads((d/'sequence.json').read_text()) for d in (args.baseline,args.corrected)]
    a,b=reports
    if (a.get('error') or b.get('error') or not b.get('restoration_exact') or
            a['capture']!=b['capture'] or a['roi']!=b['roi'] or a['sample']!=b['sample'] or
            [d['event'] for d in a['draws']]!=[d['event'] for d in b['draws']]):
        raise ValueError('Require matching successful GPU sequences and exact restoration')
    if a['roi']!=[260,360,100,120] or len(a['draws'])!=180:
        raise ValueError('Require the complete identified 90-frame draw sequence')
    report={'scope':__doc__.strip(),'capture':a['capture'],'patch_native_xywh':[306,427,6,7],
            'frames':[],'zero_roi_draws':[]}
    for old,new in zip(a['draws'],b['draws']):
        arrays=[]
        for directory,row in [(args.baseline,old),(args.corrected,new)]:
            data=(directory/row['file']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=row['sha256'] or len(data)!=100*120*8:
                raise ValueError('Changed or incomplete raw GPU output')
            array=np.frombuffer(data,dtype='<f2').reshape(120,100,4).astype(np.float64)[:,:,:3]
            if not np.isfinite(array).all(): raise ValueError('Nonfinite RGB')
            arrays.append(array)
        if not np.any(arrays[0]):
            if np.any(arrays[1]): raise ValueError('Originally zero ROI is no longer zero')
            report['zero_roi_draws'].append(old['event'])
            continue
        if not np.any(arrays[1]): raise ValueError('Corrected main ROI became black')
        report['frames'].append({'event':old['event'],
            'baseline_patch_mean_rgb':float(arrays[0][67:74,46:52].mean()),
            'corrected_patch_mean_rgb':float(arrays[1][67:74,46:52].mean()),
            'changed_rgb_pixels':int(np.any(arrays[0]!=arrays[1],axis=2).sum())})
    if len(report['frames'])!=90 or len(report['zero_roi_draws'])!=90:
        raise ValueError('Unexpected main/secondary ROI split')
    for label in ('baseline','corrected'):
        values=np.array([f[label+'_patch_mean_rgb'] for f in report['frames']])
        report[label]={'minimum':float(values.min()),'maximum':float(values.max()),
            'mean':float(values.mean()),'standard_deviation':float(values.std()),
            'mean_absolute_adjacent_change':float(abs(np.diff(values)).mean()),
            'maximum_absolute_adjacent_change':float(abs(np.diff(values)).max())}
    with args.output.open('x') as target: json.dump(report,target,indent=2)
    print(json.dumps({k:report[k] for k in ('baseline','corrected')}))


if __name__=='__main__': main()
