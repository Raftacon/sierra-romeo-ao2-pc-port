"""Exercise telemetry time alignment and rejection of incomplete measurements."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from analyze_gpu_telemetry import analyze
from gpu_telemetry import FIELDS


def main():
    checks = []
    with TemporaryDirectory(prefix='aot-gpu-telemetry-') as temporary:
        directory = Path(temporary)
        def write(name, value):
            (directory / name).write_text(json.dumps(value))
        def rows(values):
            with (directory / 'gpu-telemetry.csv').open('w', newline='') as stream:
                csv.writer(stream).writerows(values)
        origin = 1789560000000
        capture = dict(complete=True, fields=FIELDS,
                       start=dict(wall_ms=origin, steady_ms=1000, uncertainty_ms=.01, utc_offset_seconds=-25200),
                       end=dict(wall_ms=origin+100000, steady_ms=101000, uncertainty_ms=.01, utc_offset_seconds=-25200))
        write('gpu-telemetry.json', capture)
        write('quiet-pacing.json', dict(complete=True))
        write('quiet-analysis.json', dict(complete=True, foreground_verified=True))
        write('quiet-window.json', dict(steady_clock_start_ms=11000, steady_clock_end_ms=91000))
        data = []
        for second in range(101):
            stamp = datetime.fromtimestamp(origin/1000+second, timezone(timedelta(hours=-7)))
            data.append([stamp.strftime('%Y/%m/%d %H:%M:%S.%f')[:-3], '0', 'test-gpu', 'P0',
                         '70', '98', '30', '1950', '7001', '150', '200', '1600', '0x0',
                         'Not Active', 'Not Active', 'Not Active'])
        rows(data)
        result = analyze(directory)['gpus']['test-gpu']
        assert result['samples'] == 81 and result['maximum_gap_ms'] == 1000
        assert result['metrics']['clocks.current.graphics']['mean'] == 1950
        checks.append('Known UTC offset and QPC window select exactly 81 samples')
        def rejects(label):
            try:
                analyze(directory)
            except ValueError:
                checks.append(label)
            else:
                raise AssertionError(label)
        rows(data[:30] + data[34:])
        rejects('Missing four interior samples rejected')
        rows(data[20:])
        rejects('Missing beginning coverage rejected')
        rows(data[:40] + [data[39]] + data[40:])
        rejects('Duplicate timestamps rejected')
        rows(data)
        capture['end']['wall_ms'] += 101
        write('gpu-telemetry.json', capture)
        rejects('Wall-clock step rejected')
        capture['end']['wall_ms'] -= 101
        write('gpu-telemetry.json', capture)
        data[20][9] = '[N/A]'
        rows(data)
        assert analyze(directory)['gpus']['test-gpu']['metrics']['power.draw']['missing'] == 1
        checks.append('Unavailable metric retained as missing, not zero')
        data[20][9] = 'nan'
        rows(data)
        rejects('Non-finite metric rejected')
    print(json.dumps(dict(complete=True, checks=checks), indent=2))


if __name__ == '__main__':
    main()
