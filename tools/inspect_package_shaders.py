"""Read retail package tables and serialized ShaderCache programs without edits.

Package table order follows UE Viewer's versioned 445/79 serializer. ShaderCache
record fields are checked against their serialized end offsets. This produces
analysis records, not replacement packages or claims about live shader use.
"""
import argparse
import json
from pathlib import Path
import struct
from locate_shader_packages import decode, require, sha


def tables(data, meta):
    def word(at):
        require(0 <= at <= len(data)-4, 'Table read outside package')
        return struct.unpack_from('>i', data, at)[0]
    def fname(at):
        index, number = word(at), word(at+4)
        require(0 <= index < len(names) and 0 <= number < 1000000, 'Invalid table FName')
        return names[index] + ('_' + str(number-1) if number else '')
    names, at = [], meta['name_offset']
    require(0 <= meta['name_count'] <= 100000 and 0 <= meta['export_count'] <= 200000 and
            0 <= meta['import_count'] <= 100000, 'Invalid package object counts')
    for _ in range(meta['name_count']):
        count = word(at); at += 4
        require(0 < abs(count) <= 2048, 'Invalid name length')
        length = count if count > 0 else -count*2
        require(at+length+8 <= len(data), 'Truncated name')
        raw = bytes(data[at:at+length]); at += length+8
        require(raw.endswith(b'\0' if count > 0 else b'\0\0'), 'Unterminated name')
        names.append(raw[:-1].decode('latin1') if count > 0 else raw[:-2].decode('utf-16-be'))
    require(at == meta['import_offset'], 'Unexpected name-table endpoint')
    imports = []
    for _ in range(meta['import_count']):
        imports.append({'class_package': fname(at), 'class': fname(at+8),
                        'outer': word(at+16), 'name': fname(at+20)})
        at += 28
    require(at == meta['export_offset'], 'Unexpected import-table endpoint')
    exports = []
    for _ in range(meta['export_count']):
        item = {'class_index': word(at), 'super': word(at+4), 'outer': word(at+8),
                'name': fname(at+12), 'archetype': word(at+20),
                'size': word(at+32), 'offset': word(at+36)}
        require(item['size'] >= 0 and item['offset'] >= 0 and
                item['offset']+item['size'] <= len(data), 'Export outside package')
        at += 40
        count = word(at); require(0 <= count <= 16384, 'Invalid component map')
        at += 4 + count*12 + 4  # map then export flags
        generations = word(at); require(0 <= generations <= 64, 'Invalid export generations')
        at += 4 + generations*4 + 16
        exports.append(item)
    require(at == meta['depends_offset'], 'Unexpected export-table endpoint')
    def describe(index):
        require(-len(imports) <= index <= len(exports), 'Invalid package object reference')
        return imports[-index-1]['name'] if index < 0 else exports[index-1]['name'] if index else 'Class'
    for item in exports:
        item['class'] = describe(item['class_index'])
    return names, exports


def shaders(data, names, export):
    at, limit = export['offset'], export['offset'] + export['size']
    require(0 <= at <= limit <= len(data), 'ShaderCache outside package')
    def word(offset):
        require(at <= offset <= limit-4, 'Shader read outside export')
        return struct.unpack_from('>I', data, offset)[0]
    def name(offset):
        index, number = word(offset), word(offset+4)
        require(index < len(names) and number == 0, 'Unexpected shader FName')
        return names[index]
    require(export['class'] == 'ShaderCache' and export['size'] >= 21, 'Expected ShaderCache export')
    require(name(at+4) == 'None', 'Unexpected ShaderCache tagged properties')
    at += 12
    require(data[at] == 2, 'Unexpected ShaderCache platform')
    at += 1
    for section in ('shader_type_crc', 'vertex_factory_crc'):
        count = word(at); require(count <= 4096, 'Invalid '+section+' count')
        at += 4
        require(at+count*12 <= limit, 'Truncated CRC map')
        for i in range(count): name(at+i*12)
        at += count*12
    count = word(at); require(count <= 100000, 'Invalid shader count'); at += 4
    result = []
    for index in range(count):
        type_name = name(at)
        guid = bytes(data[at+8:at+24]).hex()
        end = word(at+24)
        require(at+34 <= end <= limit, 'Invalid shader record endpoint')
        platform, frequency = data[at+28:at+30]
        size = word(at+30)
        require(platform == 2 and frequency in (0,1) and 36 <= size <= 4*1024*1024 and
                at+34+size <= end, 'Invalid serialized shader code')
        code = bytes(data[at+34:at+34+size])
        magic, resource_offset, resource_size = struct.unpack_from('>3I', code)
        require(magic == 0x102A1100 + (1-frequency) and resource_offset >= 36 and resource_size > 0 and
                resource_size % 4 == 0 and resource_offset+resource_size <= len(code),
                'Unsupported shader container: '+repr((index,type_name,hex(magic),resource_offset,resource_size,len(code))))
        descriptor_offset = struct.unpack_from('>I', code, 24)[0]
        require(36 <= descriptor_offset <= resource_offset-8, 'Invalid shader descriptor offset')
        program_offset, gpu_size = struct.unpack_from('>2I', code, descriptor_offset)
        require(program_offset % 4 == 0 and gpu_size > 0 and gpu_size % 4 == 0 and
                program_offset+gpu_size <= resource_size, 'Program outside GPU resource')
        # Some resources start with shader literal constants. The descriptor's
        # offset identifies the instruction bytes after that prefix.
        gpu_offset = resource_offset+program_offset
        result.append({'index': index, 'type': type_name, 'guid': guid, 'frequency': frequency,
                       'offset': at, 'end': end, 'code_offset': at+34, 'code_size': size,
                       'code_sha256': sha(code), 'gpu_offset': gpu_offset, 'gpu_size': gpu_size,
                       'resource_offset': resource_offset, 'resource_size': resource_size,
                       'descriptor_offset': descriptor_offset, 'program_offset': program_offset,
                       'gpu_sha256': sha(code[gpu_offset:gpu_offset+gpu_size]),
                       'code': code, 'gpu': code[gpu_offset:gpu_offset+gpu_size]})
        at = end
    return result, {'shader_count': count, 'remaining_material_map_offset': at,
                    'remaining_material_map_bytes': limit-at}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Require a new output directory')
    raw = args.package.read_bytes()
    data, meta = decode(raw)
    names, exports = tables(data, meta)
    args.output.mkdir(parents=True)
    report = {'package': str(args.package.resolve()), 'source_sha256': sha(raw),
              'decoded_sha256': sha(data), 'tables': meta, 'caches': [], 'complete': False}
    try:
        for export in exports:
            if export['class'] != 'ShaderCache': continue
            programs, details = shaders(data, names, export)
            for p in programs:
                stem = str(len(report['caches']))+'-'+str(p['index'])
                (args.output/(stem+'.code.bin')).write_bytes(p.pop('code'))
                (args.output/(stem+'.gpu.bin')).write_bytes(p.pop('gpu'))
            report['caches'].append({'export': export, 'summary': details, 'programs': programs})
        require(sha(args.package.read_bytes()) == sha(raw), 'Source package changed during inspection')
        report['complete'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'complete': True, 'caches': len(report['caches']),
                      'programs': sum(len(c['programs']) for c in report['caches'])}))


if __name__ == '__main__': main()
