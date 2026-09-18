"""Replay-only projection test for the captured crate darkening material.

World r4 contains x,w,y,z; projection uses guest constants c0-c3. Replay must
check clip-space flags and preserve all material outputs. This changes no
installed renderer and is not a claim that the correct surface wins depth.
"""
import equipment_projection_variants as experiment

experiment.__doc__ = __doc__
if (experiment.HLSL.count('(double)world.z;') != 1 or
        experiment.HLSL.count('mat[1] * (double)world.y;') != 1):
    raise ValueError('Unexpected source projection layout')
experiment.HLSL = experiment.HLSL.replace('(double)world.z;', '(double)world.y;', 1).replace(
    'mat[1] * (double)world.y;', 'mat[1] * (double)world.z;', 1)
experiment.LAYOUTS = {
    'd398177c40a1c874c502f1626b346ccfda6afacf0143b7129ecbb598d001e0af':
        {'name': 'crate', 'position': 5, 'world': 4, 'scratch': 7, 'output': 3},
}

if __name__ == '__main__':
    experiment.main()
