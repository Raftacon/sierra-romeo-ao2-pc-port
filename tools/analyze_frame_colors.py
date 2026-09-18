"""Check contiguous native output-color coverage; magenta detection is a heuristic."""
import argparse
import csv
import json
from pathlib import Path


def analyze(path):
    summary = json.loads((path / 'summary.json').read_text())
    if not summary['complete'] or not 0 < summary['processed'] == summary['requested'] <= 60000:
        raise ValueError('Incomplete native output color probe')
    with (path / 'frames.csv').open() as stream:
        raw = list(csv.DictReader(stream))
    if len(raw) != summary['processed']:
        raise ValueError('Missing output timeline rows')
    rows = []
    images = 0
    for index, record in enumerate(raw):
        row = {k: int(v) for k, v in record.items() if k != 'image'}
        width, height = row['width'], row['height']
        if (row['sequence'] != index + 1 or row['microseconds'] < 0 or
                not 0 < width <= 8192 or not 0 < height <= 8192 or
                not 0 <= row['magenta_pixels'] <= width * height or
                not 0 <= row['max_row_pixels'] <= width or not 0 <= row['wide_rows'] <= height or
                (rows and row['microseconds'] < rows[-1]['microseconds'])):
            raise ValueError('Invalid sequence, time or pixel count')
        row['image'] = record['image']
        if row['wide_rows'] >= 5 and images < 32:
            if row['image'] != f"wide-{row['sequence']}.ppm":
                raise ValueError('Missing candidate image')
        elif row['image']:
            raise ValueError('Unexpected candidate image')
        if row['image']:
            image = path / row['image']
            with image.open('rb') as stream:
                if stream.readline() != b'P6\n' or stream.readline() != f'{width} {height}\n'.encode() or stream.readline() != b'255\n':
                    raise ValueError('Unexpected candidate image layout')
                offset = stream.tell()
            if image.stat().st_size != offset + width * height * 3:
                raise ValueError('Truncated candidate image')
            images += 1
        rows.append(row)
    wide = [r for r in rows if r['wide_rows'] >= 5]
    if len(wide) != summary['wide_frames'] or images != summary['images_saved']:
        raise ValueError('Summary differs from output timeline')
    start = summary['steady_clock_start_us']
    return dict(complete=True, scope=__doc__, outputs=len(rows),
                steady_clock_first_ms=(start + rows[0]['microseconds']) / 1000,
                steady_clock_last_ms=(start + rows[-1]['microseconds']) / 1000,
                duration_seconds=(rows[-1]['microseconds'] - rows[0]['microseconds']) / 1e6,
                maximum_output_interval_ms=max((b['microseconds']-a['microseconds'])/1000
                                               for a,b in zip(rows,rows[1:])) if len(rows)>1 else 0,
                largest_magenta_pixels=max(r['magenta_pixels'] for r in rows),
                wide_flash_frames=wide, images_saved=images)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Require a new output file')
    result = analyze(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
