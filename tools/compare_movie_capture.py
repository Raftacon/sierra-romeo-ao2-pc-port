"""Match fullscreen Bink captures to decoded source frames for visual diagnosis.

Requires FFmpeg and Pillow. Scores describe differences, never an automatic
parity pass: color conversion, filtering, overlays and capture timing matter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageStat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--movie', type=Path, required=True)
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--first-second', type=int, required=True)
    parser.add_argument('--last-second', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg: parser.error('FFmpeg was not found')
    movie, captures, output = args.movie.resolve(), args.captures.resolve(), args.output.resolve()
    if output.exists(): parser.error('Use a new output directory')
    if args.first_second < 0 or args.last_second < args.first_second:
        parser.error('Specify an ordered, nonnegative capture range')
    with movie.open('rb') as f: header = f.read(36)
    if len(header) != 36 or header[:3] != b'BIK': parser.error('Expected a Bink 1 movie')
    count, width, height = struct.unpack_from('<I', header, 8)[0], *struct.unpack_from('<II', header, 20)
    if width * 9 != height * 16 or not 0 < count <= 10000:
        parser.error('This diagnostic supports 16:9 movies of at most 10000 frames')
    selected = []
    for path in sorted(captures.glob('frame-*-0.png')):
        second = int(path.name.split('-')[1])
        if args.first_second <= second <= args.last_second: selected.append(path)
    if not selected or len(selected) > 300: parser.error('Select between 1 and 300 captures')
    output.mkdir(parents=True)
    version = subprocess.run([ffmpeg, '-version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    rows = []
    with tempfile.TemporaryDirectory(prefix='decode-', dir=output) as temporary:
        temporary = Path(temporary)
        small = temporary / 'reference.rgb'
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(movie),
                   '-an', '-vf', 'scale=160:90', '-frames:v', str(count),
                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', str(small)]
        subprocess.run(command, check=True)
        raw = small.read_bytes()
        stride = 160 * 90 * 3
        if len(raw) != count * stride: raise RuntimeError('Decoded frame count differs from Bink header')
        # Match the central picture, excluding subtitles and the right edge
        # whose integrity is being investigated. Search every source frame.
        references = [Image.frombytes('RGB', (160, 90), raw[i:i + stride]).crop((8, 12, 148, 72))
                      for i in range(0, len(raw), stride)]
        del raw
        for path in selected:
            with Image.open(path) as image:
                if image.width * 9 != image.height * 16:
                    raise RuntimeError(f'{path.name} is not a fullscreen 16:9 capture')
                region = image.convert('RGB').resize((160, 90)).crop((8, 12, 148, 72))
            errors = [sum(ImageStat.Stat(ImageChops.difference(region, ref)).mean) / 3 for ref in references]
            frame = min(range(len(errors)), key=errors.__getitem__)
            rows.append({'capture': path.name, 'capture_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'reference_frame': frame, 'matching_rgb_mae': errors[frame],
                         'low_detail_match': max(ImageStat.Stat(region).stddev) < 2})
        for previous, current in zip(rows, rows[1:]):
            current['candidate_precedes_previous'] = current['reference_frame'] < previous['reference_frame']
        del references
        indices = sorted({row['reference_frame'] for row in rows})
        full = temporary / 'selected.rgb'
        select = '+'.join(rf'eq(n\,{n})' for n in indices)
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(movie),
                        '-an', '-vf', 'select=' + select, '-fps_mode', 'passthrough',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', str(full)], check=True)
        stride = width * height * 3
        if full.stat().st_size != len(indices) * stride:
            raise RuntimeError('Selected source frame count mismatch')
        by_frame = {frame: i for i, frame in enumerate(indices)}
        with full.open('rb') as decoded:
            for row in rows:
                decoded.seek(by_frame[row['reference_frame']] * stride)
                reference = Image.frombytes('RGB', (width, height), decoded.read(stride))
                with Image.open(captures / row['capture']) as image:
                    native = image.convert('RGB').resize((width, height))
                difference = ImageChops.difference(native, reference).convert('L')
                top, bottom = height * 12 // 90, height * 72 // 90
                row['right_8_pixels_luma_mae'] = ImageStat.Stat(difference.crop((width - 8, top, width, bottom))).mean[0]
                row['center_luma_mae'] = ImageStat.Stat(difference.crop((width // 20, top, width * 19 // 20, bottom))).mean[0]
                row['left_8_pixels_luma_mae'] = ImageStat.Stat(difference.crop((0, top, 8, bottom))).mean[0]
        # Save the pair with the largest right-edge difference for inspection.
        worst = max(rows, key=lambda row: row['right_8_pixels_luma_mae'])
        with full.open('rb') as decoded:
            decoded.seek(by_frame[worst['reference_frame']] * stride)
            Image.frombytes('RGB', (width, height), decoded.read(stride)).save(output / 'largest-edge-source.png')
        with Image.open(captures / worst['capture']) as image:
            image.convert('RGB').resize((width, height)).save(output / 'largest-edge-native.png')
    (output / 'comparison.json').write_text(json.dumps({
        'movie': str(movie), 'movie_sha256': hashlib.sha256(movie.read_bytes()).hexdigest(),
        'ffmpeg': version, 'source_size': [width, height], 'source_frame_count': count,
        'largest_edge_difference_capture': worst['capture'], 'frames': rows,
        'interpretation': 'Nearest source-frame matches; differences include timing, filtering and color conversion. No automatic parity pass.'
    }, indent=2) + '\n')
    print(f'Matched {len(rows)} captures; largest edge difference: {worst["capture"]}')


if __name__ == '__main__': main()
