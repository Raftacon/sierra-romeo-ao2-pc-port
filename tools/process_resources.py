"""Read cumulative CPU time and committed/working memory for a Windows process."""
import ctypes as c
from ctypes import wintypes as w


class MemoryCounters(c.Structure):
    _fields_=[('cb',w.DWORD),('PageFaultCount',w.DWORD)]+[
        (name,c.c_size_t) for name in (
            'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
            'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage',
            'PagefileUsage','PeakPagefileUsage','PrivateUsage')]


class ProcessResources:
    def __init__(self,pid):
        self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.kernel.GetProcessTimes.argtypes=[w.HANDLE]+[c.POINTER(w.FILETIME)]*4
        self.kernel.K32GetProcessMemoryInfo.argtypes=[w.HANDLE,c.POINTER(MemoryCounters),w.DWORD]
        self.handle=self.kernel.OpenProcess(0x1000,False,pid)
        if not self.handle:raise c.WinError(c.get_last_error())

    def sample(self):
        times=[w.FILETIME() for _ in range(4)]
        memory=MemoryCounters();memory.cb=c.sizeof(memory)
        if not self.kernel.GetProcessTimes(self.handle,*[c.byref(t) for t in times]):
            raise c.WinError(c.get_last_error())
        if not self.kernel.K32GetProcessMemoryInfo(self.handle,c.byref(memory),c.sizeof(memory)):
            raise c.WinError(c.get_last_error())
        return {'cpu_seconds':sum((t.dwHighDateTime<<32)|t.dwLowDateTime for t in times[2:])/1e7,
                'private_bytes':memory.PrivateUsage,'working_set_bytes':memory.WorkingSetSize,
                'page_faults':memory.PageFaultCount}

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle);self.handle=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
