"""
Abstractions on top of Win32 pipes for integration in the prompt_toolkit event
loop.
"""
import ctypes
import io
from asyncio import Event, Future, ensure_future, get_event_loop
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_char_p,
    c_int,
    c_long,
    c_ulong,
    c_void_p,
    pointer,
    py_object,
    windll,
)
from ctypes.wintypes import BOOL, DWORD, HANDLE, ULONG
from prompt_toolkit.input.win32 import _Win32Handles

__all__ = [
    "PipeReader",
    "PipeWriter",
]

INVALID_HANDLE_VALUE = -1
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
ERROR_BROKEN_PIPE = 109


class _US(Structure):
    _fields_ = [
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
    ]


class _U(Union):
    _fields_ = [
        ("s", _US),
        ("Pointer", c_void_p),
    ]

    _anonymous_ = ("s",)


class OVERLAPPED(Structure):
    _fields_ = [
        ("Internal", POINTER(ULONG)),
        ("InternalHigh", POINTER(ULONG)),
        ("u", _U),
        ("hEvent", HANDLE),
        # Custom fields.
        ("channel", py_object),
    ]

    _anonymous_ = ("u",)


class PipeReader:
    """
    Asynchronous reader for win32 pipes.
    """

    def __init__(self, pipe_name, read_callback, done_callback):
        self.pipe_name = pipe_name
        self.read_callback = read_callback
        self.done_callback = done_callback
        self.done = False

        self.handle = windll.kernel32.CreateFileW(
            pipe_name, GENERIC_READ, 0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None
        )

        if self.handle == INVALID_HANDLE_VALUE:
            error_code = windll.kernel32.GetLastError()
            raise Exception("Invalid pipe handle. Error code=%r." % error_code)

        # Create overlapped structure and event.
        self._overlapped = OVERLAPPED()
        self._event = HANDLE(windll.kernel32.CreateEventA(
            None,  # Default security attributes.
            BOOL(True),  # Manual reset event.
            BOOL(True),  # initial state = signaled.
            None,  # Unnamed event object.
        ))
        self._overlapped.hEvent = self._event

        self._win32_handles = _Win32Handles()

        self._pending = io.StringIO()
        self._stop = True

        # Start reader coroutine.
        ensure_future(self._async_reader())

    async def _wait_for_event(self):
        """
        Wraps a win32 event into a `Future` and wait for it.
        """
        f = Future()

        def ready() -> None:
            self._win32_handles.remove_win32_handle(self._event)
            f.set_result(None)

        self._win32_handles.add_win32_handle(self._event, ready)

        return await f

    async def _async_reader(self):
        buffer_size = 65536
        c_read = DWORD()
        buffer = ctypes.create_string_buffer(buffer_size + 1)

        while True:
            # Call read.
            success = windll.kernel32.ReadFile(
                self.handle,
                buffer,
                DWORD(buffer_size),
                ctypes.byref(c_read),
                ctypes.byref(self._overlapped),
            )

            if success:
                buffer[c_read.value] = b"\0"
                self.on_read(buffer.value)

            else:
                error_code = windll.kernel32.GetLastError()
                # Pending I/O. Wait for it to finish.
                if error_code == ERROR_IO_PENDING:
                    # Wait for event.
                    await self._wait_for_event()

                    # Get pending data.
                    success = windll.kernel32.GetOverlappedResult(
                        self.handle,
                        ctypes.byref(self._overlapped),
                        ctypes.byref(c_read),
                        BOOL(False),
                    )

                    if success:
                        buffer[c_read.value] = b"\0"
                        self.on_read(buffer.value)

                elif error_code == ERROR_BROKEN_PIPE:
                    self.stop_reading()
                    self.done_callback()
                    self.done = False
                    return

    def start_reading(self):
        if self._stop:
            text = self._pending.getvalue()
            if text:
                self.read_callback(text)
                # clear
                self._pending.read()
        self._stop = False

    def stop_reading(self):
        self._stop = True

    def on_read(self, value):
        text = value.decode("utf-8", "ignore")
        if self._stop:
            self._pending.write(text)
        else:
            self.read_callback(text)


class PipeWriter:
    """
    Wrapper around a win32 pipe.
    """

    def __init__(self, pipe_name):
        self.pipe_name = pipe_name

        self.handle = windll.kernel32.CreateFileW(
            pipe_name, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )

        if self.handle == INVALID_HANDLE_VALUE:
            error_code = windll.kernel32.GetLastError()
            raise Exception("Invalid stdin handle code=%r" % error_code)

    def write(self, text):
        " Write text to the stdin of the process. "
        data = text.encode("utf-8")
        c_written = DWORD()

        success = windll.kernel32.WriteFile(
            self.handle,
            ctypes.create_string_buffer(data),
            len(data),
            ctypes.byref(c_written),
            None,
        )

        # TODO: check 'written'.
