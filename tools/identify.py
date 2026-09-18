"""Report non-secret XEX2 identity fields and verify the targeted revision."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def identify(path):
    data = Path(path).read_bytes()
    if len(data) < 24 or data[:4] != b'XEX2':
        raise ValueError('Not an XEX2 executable')
    u32 = lambda offset: struct.unpack_from('>I', data, offset)[0]
    header_size, security, count = u32(8), u32(16), u32(20)
    if header_size > len(data) or 24 + count * 8 > header_size or security + 0x184 > header_size:
        raise ValueError('Invalid XEX header bounds')
    headers = dict(struct.unpack_from('>II', data, 24 + i * 8) for i in range(count))
    execution = headers[0x40006]
    if execution + 24 > header_size:
        raise ValueError('Invalid execution info bounds')
    version = u32(execution + 4)
    report = {
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
        'title_id': f'{u32(execution + 12):08X}',
        'media_id': f'{u32(execution):08X}',
        'version': f'{version >> 28}.{(version >> 24) & 15}.{(version >> 8) & 65535}.{version & 255}',
        'image_base': f'{u32(security + 0x110):08X}',
        'image_size': u32(security + 4),
        'entry_point': f'{headers[0x10100]:08X}',
        'disc_number': data[execution + 18],
        'disc_count': data[execution + 19],
    }
    if 0x183FF in headers:
        offset = headers[0x183FF]
        size = u32(offset)
        if size < 4 or offset + size > header_size:
            raise ValueError('Invalid original name bounds')
        report['original_pe_name'] = data[offset + 4:offset + size].split(b'\0')[0].decode('ascii')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xex', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expect', type=Path, help='Fail if SHA-256 differs from this identity JSON')
    args = parser.parse_args()
    report = identify(args.xex)
    if args.expect:
        expected = json.loads(args.expect.read_text())
        if report['sha256'] != expected['sha256']:
            raise SystemExit('Executable revision mismatch: refusing address-specific configuration')
    result = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding='utf-8')
    print(result, end='')


if __name__ == '__main__':
    main()
