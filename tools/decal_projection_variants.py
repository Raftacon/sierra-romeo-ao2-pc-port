"""Build a replay-only precision experiment for the captured decal VS.

Uses the existing bounded DXBC snippet splicer with the verified decal layout.
The replay must validate clip-space flags (mask 14 equals 8) and zero user clip
planes. This experiment does not modify the installed renderer.
"""
import equipment_projection_variants as experiment

experiment.__doc__ = __doc__
experiment.HLSL = experiment.HLSL.replace('world.z;', 'world.w;', 1).replace(
    'mat[2] * (double)world.w;', 'mat[2] * (double)world.z;', 1)
experiment.LAYOUTS = {
    'cb0554cf65e315b18b5e419f5ebc07f0100505631a03a81889516670f76b632d':
        {'name': 'decal', 'position': 12, 'world': 1, 'scratch': 14, 'output': 6},
}

if __name__ == '__main__':
    experiment.main()
