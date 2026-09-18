"""Report frame timing and temporal image differences for a graphics probe.

These metrics include legitimate animation/lighting changes. They locate useful
comparisons; they do not establish rendering correctness by themselves.
"""
import argparse
import csv
import json
from pathlib import Path
import statistics
from PIL import Image, ImageChops, ImageStat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    result = {}
    timing = args.probe / 'frame-times.csv'
    if timing.exists():
        elapsed, frames = 0.0, []
        for row in csv.DictReader(timing.open()):
            interval = float(row['interval_ms'])
            elapsed += interval / 1000
            if 75 <= elapsed < 110: frames.append(interval)
        if frames:
            ordered = sorted(frames)
            result['timing_game_elapsed_75_to_110_seconds'] = {
                'frames': len(frames), 'mean_ms': statistics.mean(frames),
                'covered_seconds': sum(frames) / 1000,
                'p95_ms': ordered[int(len(ordered) * .95)],
                'p99_ms': ordered[int(len(ordered) * .99)],
                'over_25ms': sum(value > 25 for value in frames)}
    regions = {'right_wall': (1150, 340, 1260, 490),
               'player_shoulder': (860, 355, 1000, 460),
               'right_edge': (1268, 70, 1286, 700)}
    result['regions_window_pixels'] = regions
    result['bursts'] = {}
    for directory in sorted(args.probe.iterdir()):
        paths = sorted(directory.glob('*.png')) if directory.is_dir() else []
        if len(paths) < 2: continue
        metrics = {name: [] for name in regions}
        previous = None
        for path in paths:
            with Image.open(path) as image:
                current = {name: image.crop(rect).convert('RGB') for name, rect in regions.items()}
            if previous:
                for name in regions:
                    diff = ImageChops.difference(current[name], previous[name])
                    hist = diff.convert('L').histogram()
                    metrics[name].append({'mean_absolute_rgb': statistics.mean(ImageStat.Stat(diff).mean),
                        'fraction_luma_delta_over_16': sum(hist[17:]) / sum(hist)})
            previous = current
        result['bursts'][directory.name] = {'frames': len(paths), 'regions': {
            name: {'mean_absolute_rgb': statistics.mean(item['mean_absolute_rgb'] for item in values),
                   'max_fraction_luma_delta_over_16': max(item['fraction_luma_delta_over_16'] for item in values)}
            for name, values in metrics.items()}}
    (args.probe / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
