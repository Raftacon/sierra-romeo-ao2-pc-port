"""Generate a hardware comparison from the actual two projection snippet bodies."""
import argparse
from pathlib import Path
from equipment_projection_variants import HLSL
from projection_fma_snippet import candidate_source


def function(name, source):
    body = source.split('float4 main(', 1)[1].split('{', 1)[1].rsplit('}', 1)[0]
    if body.count('  p.xyz =') != 1:
        raise ValueError('Viewport anchor changed')
    body = body.replace('  p.xyz =', '  world_dot = p;\n  p.xyz =')
    body = body.replace('sys[8]', 'viewport_scale').replace('sys[9]', 'viewport_offset')
    return (f'float4 {name}(float4 world, float4 mat[4], float4 viewport_scale, '
            f'float4 viewport_offset, out double4 world_dot) {{' + body + '}\n')


TEST = r'''
RWStructuredBuffer<uint4> results : register(u0);
uint Mix(uint x) {
  x ^= x >> 16; x *= 0x7feb352d; x ^= x >> 15;
  x *= 0x846ca68b; return x ^ (x >> 16);
}
float Finite(inout uint seed) {
  seed = Mix(seed + 0x9e3779b9);
  uint bits = seed;
  if ((bits & 0x7f800000) == 0x7f800000) bits ^= 0x00800000;
  return asfloat(bits);
}
float Normal(inout uint seed) {
  seed = Mix(seed + 0x9e3779b9);
  return (float(int(seed & 0xffff) - 32768)) / 8192.0;
}
bool SameDouble(double a, double b) {
  uint al, ah, bl, bh; asuint(a, al, ah); asuint(b, bl, bh);
  return al == bl && ah == bh;
}
bool NanDouble(double value) {
  uint low, high; asuint(value, low, high);
  return (high & 0x7ff00000) == 0x7ff00000 && ((high & 0xfffff) != 0 || low != 0);
}
bool NanFloat(float value) {
  uint bits = asuint(value);
  return (bits & 0x7f800000) == 0x7f800000 && (bits & 0x7fffff) != 0;
}
[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID) {
  uint index = tid.x, scenario = index >> 17, seed = Mix(index + 1);
  if (scenario >= 8) return;
  float4 mat[4]; float4 world, scale, offset;
  [unroll] for (uint lane = 0; lane < 4; ++lane) {
    world[lane] = Finite(seed); scale[lane] = Finite(seed); offset[lane] = Finite(seed);
    [unroll] for (uint row = 0; row < 4; ++row) mat[row][lane] = Finite(seed);
  }
  if (scenario >= 1 && scenario <= 3) {
    [unroll] for (uint lane = 0; lane < 4; ++lane) {
      world[lane] = Normal(seed) * 65536.0;
      scale[lane] = Normal(seed); offset[lane] = Normal(seed);
      [unroll] for (uint row = 0; row < 4; ++row) mat[row][lane] = Normal(seed);
    }
    if (scenario >= 2) {
      world.w = world.z; mat[2] = -mat[3];
      if (scenario == 3) mat[2] = asfloat(asuint(mat[2]) ^ 1);
    }
  } else if (scenario == 4) {
    world = asfloat(uint4(0x7f7fffff, 0xff7fffff, 0x7f7ffffe, 0xff7ffffe));
    mat[3] = asfloat(uint4(0x7f7fffff, 0xff7fffff, 0x00800000, 0x80800000));
    mat[2] = -mat[3]; scale = 1; offset = 0;
  } else if (scenario == 5) {
    [unroll] for (uint lane = 0; lane < 4; ++lane) {
      world[lane] = Normal(seed);
      [unroll] for (uint row = 0; row < 4; ++row)
        mat[row][lane] = asfloat(asuint(Finite(seed)) & 0x807fffff);
    }
    scale = 1; offset = 0;
  } else if (scenario == 6) {
    [unroll] for (uint row = 0; row < 4; ++row)
      [unroll] for (uint lane = 0; lane < 4; ++lane)
        mat[row][lane] = asfloat(((index >> (row * 4 + lane)) & 1) << 31);
    world = asfloat(uint4(0, 0x80000000, 0x3f800000, 0xbf800000));
    scale = 1; offset = 0;
  } else if (scenario == 7) {
    const uint special[8] = {0x7f800000, 0xff800000, 0x7fc00000, 0xffc00123,
                             0x7fa00001, 0, 0x80000000, 0x3f800000};
    [unroll] for (uint lane = 0; lane < 4; ++lane) {
      mat[3][lane] = asfloat(special[(index >> (lane * 3)) & 7]);
      mat[2][lane] = -mat[3][lane];
    }
    world.w = world.z; scale = 1; offset = 0;
  }
  double4 old_dot, new_dot;
  float4 old_value = Original(world, mat, scale, offset, old_dot);
  float4 new_value = Candidate(world, mat, scale, offset, new_dot);
  // NEGATIVE_CONTROL
  uint world_mask = 0, output_mask = 0, semantic_mask = 0;
  [unroll] for (uint lane = 0; lane < 4; ++lane) {
    bool same_world = SameDouble(old_dot[lane], new_dot[lane]);
    bool same_output = asuint(old_value[lane]) == asuint(new_value[lane]);
    if (!same_world) world_mask |= 1 << lane;
    if (!same_output) output_mask |= 1 << lane;
    if (!same_world && !(NanDouble(old_dot[lane]) && NanDouble(new_dot[lane]))) semantic_mask |= 1 << lane;
    if (!same_output && !(NanFloat(old_value[lane]) && NanFloat(new_value[lane]))) semantic_mask |= 16 << lane;
  }
  results[index] = uint4(index, world_mask, output_mask, semantic_mask);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--negative-control', action='store_true')
    args = parser.parse_args()
    body = TEST
    if args.negative_control:
        body = body.replace('// NEGATIVE_CONTROL',
                            'if (index == 42) new_value.x = asfloat(asuint(old_value.x) ^ 1);')
    with args.output.open('x') as stream:
        stream.write(function('Original', HLSL) + function('Candidate', candidate_source(HLSL)) + body)


if __name__ == '__main__':
    main()
