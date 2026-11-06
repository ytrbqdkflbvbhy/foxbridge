"""Private console supervisor: EOF stops TUN even when the UI/worker crashes."""
import ctypes
from ctypes import wintypes
import subprocess
import sys
import threading


def own_job():
    # A Windows job guarantees the child cannot survive a supervisor crash.
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('r', 'w', 'o', 'rb', 'wb', 'ob')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                    ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def main():
    if len(sys.argv) != 3:
        return 2
    job = own_job()
    # This process has its OWN hidden console. Ctrl+Break never touches user terminals.
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    handler = handler_type(lambda signal: True)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    child = subprocess.Popen([sys.argv[1], 'run', '-c', sys.argv[2]],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='replace')
    stop = threading.Event()

    def input_loop():
        sys.stdin.readline()  # Also returns on parent crash (broken pipe).
        stop.set()

    def log_loop():
        for line in child.stdout:
            if 'sing-box started' in line:
                print('ready', flush=True)
        stop.set()

    threading.Thread(target=input_loop, daemon=True).start()
    threading.Thread(target=log_loop, daemon=True).start()
    try:
        stop.wait()
    finally:
        if child.poll() is None:
            ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, 0)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
