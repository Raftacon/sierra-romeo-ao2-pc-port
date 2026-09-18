"""Prepare persistent local saves without replacing an existing player profile."""
import argparse
from pathlib import Path
import shutil


def prepare(destination: Path, source: Path | None = None) -> str:
    destination = destination.resolve()
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f'Profile path is not a directory: {destination}')
        return 'existing'
    if source is not None and source.is_dir():
        source = source.resolve()
        if source == destination or source in destination.parents or destination in source.parents:
            raise ValueError('Source and destination profiles must be separate directories')
        # copytree refuses an existing destination. Preserve both the profile
        # settings and content headers alongside the checkpoint payload.
        shutil.copytree(source, destination)
        return 'copied'
    destination.mkdir(parents=True)
    return 'created'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    result = prepare(args.destination, args.source)
    print(f'Local profile ({result}): {args.destination.resolve()}')
