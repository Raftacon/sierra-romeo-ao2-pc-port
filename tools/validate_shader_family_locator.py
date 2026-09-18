"""Check locator rejection boundaries and specialization against retail controls.

Reads local fixtures without modifying them. This validates asset attribution,
not production shader correction or native rendering.
"""
import argparse
import json
from pathlib import Path
import struct
from inspect_package_shaders import tables, shaders
from locate_shader_families import signature
from locate_shader_packages import decode, cache_programs, require, sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', type=Path, required=True, help='Retail AO2Game.xxx control')
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    require(not a.output.exists(), 'Require a new output report')
    raw = a.package.read_bytes()
    data, meta = decode(raw)
    names, exports = tables(data, meta)
    export = next(e for e in exports if e['class'] == 'ShaderCache')
    values, _ = shaders(data, names, export)
    controls = {'EB6A7ACBD84D0443': 'FFinalToneMappingVertexShader',
                'B5A1208D037C2F4F': 'FModShadowVolumeVertexShader'}
    cached, cache_hash = cache_programs(a.cache, set(controls))
    report = {'complete': False, 'package_sha256': sha(raw), 'cache_sha256': cache_hash,
              'positive_controls': [], 'negative_controls': []}
    def rejected(label, action):
        try:
            action()
        except ValueError as error:
            report['negative_controls'].append({'label': label, 'reason': str(error)})
        else:
            raise AssertionError('Invalid input accepted: '+label)
    for guest, type_name in controls.items():
        target = signature(cached[guest], True)
        matches = [v for v in values if v['frequency'] == 0 and signature(v['gpu'], True) == target]
        require(matches and any(v['type'] == type_name for v in matches), 'Known shader type not matched: '+type_name)
        report['positive_controls'].append({'guest': guest, 'types': [v['type'] for v in matches]})
        # Every excluded byte must stay within an identified fetch instruction.
        code = cached[guest]
        excluded = {j for i in target[1] for j in range(i*12, i*12+12)}
        changed = 0
        for at in range(len(code)):
            if at in excluded: continue
            mutant = bytearray(code); mutant[at] ^= 1
            try:
                same = signature(mutant, True) == target
            except ValueError:
                same = False
            require(not same, 'Non-fetch byte disappeared from matching: '+str(at))
            changed += 1
        report['positive_controls'][-1]['non_fetch_mutations_rejected'] = changed
    rejected('negative object count', lambda: tables(data, dict(meta, name_count=-1)))
    rejected('truncated table', lambda: tables(data[:meta['name_offset']+2], meta))
    rejected('cache beyond package', lambda: shaders(data, names, dict(export, size=len(data))))
    first = values[0]
    def code_word(relative, value):
        changed = bytearray(data)
        struct.pack_into('>I', changed, first['code_offset']+relative, value)
        return changed
    rejected('container descriptor out of range', lambda: shaders(code_word(24, 0xffffffff), names, export))
    rejected('program outside resource', lambda: shaders(code_word(first['descriptor_offset'], 0xfffffffc), names, export))
    rejected('empty program', lambda: shaders(code_word(first['descriptor_offset']+4, 0), names, export))
    changed = bytearray(data)
    struct.pack_into('>I', changed, first['offset']+30, 12)
    rejected('short container', lambda: shaders(changed, names, export))
    require(sha(a.package.read_bytes()) == sha(raw) and sha(a.cache.read_bytes()) == cache_hash, 'Source changed')
    report.update(complete=True, sources_unchanged=True, shader_count=len(values),
                  literal_prefix_programs=sum(v['program_offset'] > 0 for v in values))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__': main()
