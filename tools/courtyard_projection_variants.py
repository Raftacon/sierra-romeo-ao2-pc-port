"""Replay-only projection test for the captured courtyard impact material.

The captured world register stores x,w,y,z. Preserve every material interpolator;
replay must validate clip-space mode and absence of user clip/cull outputs.
"""
import equipment_projection_variants as experiment

experiment.__doc__ = __doc__
if (experiment.HLSL.count('(double)world.z;') != 1 or
        experiment.HLSL.count('mat[1] * (double)world.y;') != 1):
    raise ValueError('Unexpected source projection layout')
experiment.HLSL = experiment.HLSL.replace('(double)world.z;', '(double)world.y;', 1).replace(
    'mat[1] * (double)world.y;', 'mat[1] * (double)world.z;', 1)
experiment.LAYOUTS = {
    'a7267fb37ed936e2292a79794b23943bb7a5a57bba5d7904779aec68a4f0a75c':
        {'name': 'courtyard', 'position': 9, 'world': 7, 'scratch': 11, 'output': 7},
}

if __name__ == '__main__':
    experiment.main()
