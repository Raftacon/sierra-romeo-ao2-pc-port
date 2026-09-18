"""Own one read-only NVIDIA sampler; never change GPU clocks or power policy."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


FIELDS = ('timestamp,index,uuid,pstate,temperature.gpu,utilization.gpu,'
          'utilization.memory,clocks.current.graphics,clocks.current.memory,'
          'power.draw,power.limit,memory.used,clocks_event_reasons.active,'
          'clocks_event_reasons.sw_power_cap,'
          'clocks_event_reasons.sw_thermal_slowdown,'
          'clocks_event_reasons.hw_thermal_slowdown').split(',')


def calibration():
    # Python 3.10's time.time uses the coarse Windows system clock. Use the
    # precise API when bracketing it with QPC so timer ticks are not clock drift.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    value = wintypes.FILETIME()
    before = time.perf_counter() * 1000
    kernel.GetSystemTimePreciseAsFileTime(ctypes.byref(value))
    after = time.perf_counter() * 1000
    wall = (((value.dwHighDateTime << 32) | value.dwLowDateTime) - 116444736000000000) / 10000
    return dict(wall_ms=wall, steady_ms=(before + after) / 2,
                uncertainty_ms=(after - before) / 2,
                utc_offset_seconds=datetime.now().astimezone().utcoffset().total_seconds())


class GpuTelemetry:
    def __init__(self, output):
        self.output = Path(output)
        executable = shutil.which('nvidia-smi')
        if not executable:
            raise RuntimeError('NVIDIA telemetry requested but nvidia-smi is unavailable')
        self.command = [executable, '--query-gpu=' + ','.join(FIELDS),
                        '--format=csv,noheader,nounits', '--loop-ms=1000']
        self.report = dict(command=self.command, fields=FIELDS,
                           binary_sha256=hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                           complete=False, scope='All NVIDIA GPUs, including other processes')
        self.process = None
        self.files = ExitStack()

    def __enter__(self):
        try:
            stream = self.files.enter_context((self.output / 'gpu-telemetry.csv').open('xb'))
            error = self.files.enter_context((self.output / 'gpu-telemetry.stderr').open('xb'))
            self.report['start'] = calibration()
            self.process = subprocess.Popen(self.command, stdout=stream, stderr=error,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            self.report['pid'] = self.process.pid
            return self
        except BaseException:
            self.files.close()
            raise

    def check(self):
        if self.process.poll() is not None:
            raise RuntimeError('NVIDIA sampler exited early: ' + str(self.process.returncode))

    def __exit__(self, kind, value, traceback):
        try:
            self.report['end'] = calibration()
            self.report['requested_stop'] = self.process.poll() is None
            if self.report['requested_stop']:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.report['exit_code'] = self.process.returncode
            self.report['complete'] = kind is None and self.report['requested_stop']
        finally:
            self.files.close()
            (self.output / 'gpu-telemetry.json').write_text(json.dumps(self.report, indent=2) + '\n')
