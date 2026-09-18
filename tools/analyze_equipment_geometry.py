"""Compare GPU triangle output, measured pixel depths and projection precision.

The float64 projection is a CPU counterfactual using GPU-extracted world values;
it is not a tested renderer correction or an Xbox hardware parity claim.
"""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.probe/'equipment-world-geometry'
    geometry = json.loads((source/'geometry.json').read_text())
    history = json.loads((args.probe/'bright-owner.json').read_text())
    if geometry.get('error') or history.get('error') or geometry['capture'] != history['capture']:
        raise ValueError('Require successful observations of the same capture')
    if ([(d['event'],d['primitive']) for d in geometry['draws']] !=
            [(7428,570),(7456,573),(21488,570),(21516,573)]
            or [(p['x'],p['y']) for p in history['pixels']] != [(311,428),(309,429)]):
        raise ValueError('Require all four identified draws and both pixel histories')
    report = {'scope': __doc__.strip(), 'draws': [], 'max_baseline_depth_error': 0.,
              'max_float32_position_error': 0.}
    for row in geometry['draws']:
        if not row.get('restoration_exact'):
            raise ValueError('Missing exact GPU restoration control')
        event = row['event']
        c = np.frombuffer((source/('%d-cb2.bin'%event)).read_bytes(),dtype='<f4').reshape(-1,4)[:4]
        system = np.frombuffer((source/('%d-cb0.bin'%event)).read_bytes(),dtype='<f4').reshape(-1,4)
        viewport = row['rasterizer']['viewports'][0]
        if [float(viewport[k]) for k in ('x','y','width','height','minDepth','maxDepth')] != [0,0,1280,720,0,.5]:
            raise ValueError('Unexpected captured viewport')
        actual = np.array([v['clip_position'] for v in row['vertices']])
        world = np.array([v['clip_position'] for v in row['world_vertices']],dtype=np.float32)[:,[0,1,3,2]]
        if not np.all(world[:,3] == 1):
            raise ValueError('Unexpected exported homogeneous position')
        # Preserve the translated multiply/add evaluation order and float32
        # intermediate rounding. No shader interpreter is involved.
        raw32 = ((world[:,3:]*c[3]+world[:,2:3]*c[2])+world[:,1:2]*c[1])+world[:,:1]*c[0]
        cpu32 = raw32.copy()
        cpu32[:,:3] = raw32[:,:3]*system[8,:3]+raw32[:,3:]*system[9,:3]
        error = float(abs(cpu32-actual).max())
        report['max_float32_position_error'] = max(report['max_float32_position_error'],error)
        if error > 1e-5 or not np.array_equal(cpu32[:,2:], actual[:,2:]):
            raise ValueError('Float32 reconstruction does not match GPU depth coordinates')
        precise = world.astype(np.float64)@c.astype(np.float64)
        precise[:,:3] = precise[:,:3]*system[8,:3]+precise[:,3:]*system[9,:3]
        z_only = actual.copy()
        z_only[:,2] = precise[:,2]
        result = {'event': event, 'world_xyzw': world.tolist(), 'actual_clip': actual.tolist(),
                  'precise_clip': precise.tolist(), 'float32_error': error, 'pixels': []}
        for mode, positions in [('original',actual),('precise',precise),('precise_z_only',z_only)]:
            ndc = positions[:,:3]/positions[:,3:]
            screen = np.column_stack(((ndc[:,0]+1)*640,(1-ndc[:,1])*360,ndc[:,2]*.5))
            # Match the captured rasterization's 8-bit subpixel grid, then
            # interpolate depth at standard two-sample MSAA sample zero.
            screen[:,:2] = np.rint(screen[:,:2]*256)/256
            plane = np.linalg.solve(np.column_stack((screen[:,:2],np.ones(3))),screen[:,2])
            for pixel in history['pixels']:
                expected = float(next(h for h in pixel['history'] if h['event']==event)['details']['shaderOut']['depth'])
                predicted = float(np.dot([pixel['x']+.75,pixel['y']+.75,1],plane))
                if mode == 'original':
                    delta = abs(predicted-expected)
                    report['max_baseline_depth_error'] = max(report['max_baseline_depth_error'],delta)
                    if delta > 1e-8:
                        raise ValueError('Post-VS geometry does not explain measured depth')
                result['pixels'].append({'mode':mode,'xy':[pixel['x'],pixel['y']],
                                         'predicted_depth':predicted,'original_measured_depth':expected})
        report['draws'].append(result)
    with args.output.open('x') as target:
        json.dump(report,target,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in ('draws','scope')}))


if __name__ == '__main__':
    main()
