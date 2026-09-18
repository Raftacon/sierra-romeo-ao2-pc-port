"""Read the identifying strings from an original retail checkpoint, without edits."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def checkpoint_metadata(path):
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError('Truncated checkpoint version')
    version = struct.unpack_from('>I', data)[0]
    # These identifying-string offsets are observed across the original files.
    # The other header fields and trailing gameplay state are not decoded here.
    offsets = {2: 8, 4: 20, 5: 24}
    if version not in offsets:
        raise ValueError('Unsupported checkpoint version')
    offset, values = offsets[version], []
    for _ in range(5):
        if offset + 4 > len(data):
            raise ValueError('Truncated checkpoint string length')
        length = struct.unpack_from('>i', data, offset)[0]
        offset += 4
        if not 0 < length <= 1024 or offset + length > len(data) or data[offset + length - 1] != 0:
            raise ValueError('Require bounded, null-terminated ASCII checkpoint strings')
        values.append(data[offset:offset + length - 1].decode('ascii'))
        offset += length
    header_name, checkpoint_name, world_name, level_name, actor_path = values
    if checkpoint_name != path.name or not actor_path.startswith(level_name + '.TheWorld.PersistentLevel.'):
        raise ValueError('Checkpoint identity does not match its file or level')
    return {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'version': version, 'header_name': header_name, 'checkpoint_name': checkpoint_name,
            'world_name': world_name, 'level_name': level_name, 'actor_path': actor_path,
            'remaining_bytes': len(data) - offset}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    args = parser.parse_args()
    print(json.dumps(checkpoint_metadata(args.checkpoint), indent=2))


if __name__ == '__main__':
    main()
