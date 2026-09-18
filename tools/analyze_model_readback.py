"""Select brief dark-change candidates from lossless character captures for review.

Candidate scores are image-selection heuristics, not automatic defect detection.
Legitimate animation, lighting and missed game frames can affect the scores.
"""
import argparse
import hashlib
import heapq
import json
from pathlib import Path
import statistics
import subprocess
from PIL import Image, ImageChops, ImageStat


def maximum_channel(rgb):
    r, g, b = rgb.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    p = args.probe.resolve()
    video = p / 'characters'
    native = json.loads((p / 'probe.json').read_text())
    scenario = json.loads((p / 'model-scenario.json').read_text())
    result = json.loads((video / 'result.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup'] != 0 or not scenario['source_profile_unchanged']:
        raise ValueError('Native completion or source-profile preservation failed')
    if result['timed_out'] or result['exit_code'] != 0:
        raise ValueError('Capture did not complete')
    meta = json.loads((video / 'video.json').read_text())
    if meta['pid'] != native['pid'] or meta['executable_sha256'] != native['executable_sha256']:
        raise ValueError('Capture identity differs from the native probe')
    width, height = meta['crop_xywh'][2:] if meta.get('crop_xywh') else (meta['width'], meta['height'])
    stamps = [json.loads(line) for line in (video / 'frames.jsonl').read_text().splitlines()]
    if len(stamps) < 3 or [s['index'] for s in stamps] != list(range(len(stamps))):
        raise ValueError('Require contiguous timestamped samples')
    review = video / 'review'
    review.mkdir(exist_ok=True)
    command = [meta['command'][0], '-hide_banner', '-loglevel', 'error', '-i',
               str(video / 'game-window.mkv'), '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    frames, recent, candidates = [], [], []
    with (video / 'decode.log').open('w') as log:
        decoder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while True:
                data = decoder.stdout.read(width * height * 3)
                if not data:
                    break
                if len(data) != width * height * 3:
                    raise ValueError('Incomplete decoded RGB frame')
                frame = Image.frombytes('RGB', (width, height), data)
                index = len(frames)
                change = sum(ImageStat.Stat(ImageChops.difference(recent[-1], frame)).mean) / 3 if recent else 0
                frames.append({'index': index, 'rgb_sha256': hashlib.sha256(data).hexdigest(),
                               'adjacent_mean_absolute_rgb_change': change})
                recent.append(frame)
                if index == 0:
                    frame.save(review / 'first.png')
                if len(recent) == 3:
                    before, center, after = recent
                    # Neighbors agree within 8 in every channel, but the center
                    # is darker than both by >24 in luminance: a brief dark change.
                    stable = maximum_channel(ImageChops.difference(before, after)).point(lambda x: 255 if x <= 8 else 0)
                    dark = ImageChops.subtract(ImageChops.darker(before, after), center).convert('L').point(lambda x: 255 if x > 24 else 0)
                    mask = ImageChops.multiply(stable, dark)
                    score = mask.histogram()[255]
                    frames[index - 1]['brief_dark_pixels'] = score
                    heapq.heappush(candidates, (score, index - 1, tuple(recent)))
                    if len(candidates) > 3:
                        heapq.heappop(candidates)
                    recent.pop(0)
            decoder.wait(timeout=15)
        finally:
            decoder.stdout.close()
            if decoder.poll() is None:
                decoder.kill()
                decoder.wait()
    if decoder.returncode != 0 or len(frames) != len(stamps):
        raise ValueError('Decode failed or sample count differs from the capture sidecar')
    recent[-1].save(review / 'last.png')
    selected = []
    for score, index, triplet in sorted(candidates, reverse=True):
        for offset, frame in zip((-1, 0, 1), triplet):
            frame.save(review / f'candidate-{index:04d}-{offset:+d}.png')
        selected.append({'center_index': index, 'brief_dark_pixels': score})
    intervals = [(b['seconds'] - a['seconds']) * 1000 for a, b in zip(stamps, stamps[1:])]
    if min(intervals) <= 0:
        raise ValueError('Sample times must be strictly increasing')
    summary = {'readback': scenario['readback'], 'samples': len(frames),
               'distinct_rgb_frames': len({f['rgb_sha256'] for f in frames}),
               'mean_sampling_hz': 1000 / statistics.mean(intervals),
               'median_sample_interval_ms': statistics.median(intervals),
               'max_sample_interval_ms': max(intervals), 'selected_triplets': selected,
               'native_exit_code': native['exit_code_before_cleanup'],
               'native_elapsed_seconds': native['elapsed_seconds'],
               'executable_sha256': native['executable_sha256'], 'gpu_plugin': native['gpu_plugin'],
               'source_profile_unchanged': scenario['source_profile_unchanged'],
               'video_sha256': hashlib.sha256((video / 'game-window.mkv').read_bytes()).hexdigest(),
               'video_bytes': (video / 'game-window.mkv').stat().st_size,
               'crop_xywh': meta.get('crop_xywh'), 'interpretation': __doc__.strip()}
    (video / 'analysis.json').write_text(json.dumps({**summary, 'frames': frames}, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
