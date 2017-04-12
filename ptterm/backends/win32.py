from __future__ import unicode_literals

from prompt_toolkit.eventloop import get_event_loop
from prompt_toolkit.eventloop.future import Future

from yawinpty import Pty, SpawnConfig

import abc
import os
import six
import time
import win32con
import win32file
import win32security

from .base import Terminal

__all__ = (
    'Win32Terminal',
)


class Win32Terminal(Terminal):
    """
    Terminal backend for Windows, on top of winpty.
    """
    def __init__(self):
        self.pty = Pty()
        self.ready_f = Future()
        self._input_ready_callbacks = []
        self.loop = get_event_loop()

        self.stdout_handle = win32file.CreateFile(
            self.pty.conout_name(),
            win32con.GENERIC_READ,
            0,
            win32security.SECURITY_ATTRIBUTES(),
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_OVERLAPPED,
            0)

        self.stdin_handle = win32file.CreateFile(
            self.pty.conin_name(),
            win32con.GENERIC_WRITE,
            0,
            win32security.SECURITY_ATTRIBUTES(),
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_OVERLAPPED,
            0)
        self._buffer = []

    def add_input_ready_callback(self, callback):
        """
        Add a new callback to be called for when there's input ready to read.
        """
        def poll():
            while True:
                try:
                    status, from_pipe = win32file.ReadFile(self.stdout_handle, 65536)
                except Exception:
                    # The pipe has ended.
                    self.ready_f.set_result(None)
                    return

                result = from_pipe.decode('utf-8', 'ignore')
                self._buffer.append(result)
                self.loop.call_from_executor(callback)

        self.loop.run_in_executor(poll, _daemon=True)

    def read_text(self, amount):
        " Read terminal output and return it. "
        result = ''.join(self._buffer)
        self._buffer = []
        return result

    def write_text(self, text):
        " Write text to the stdin of the process. "
        win32file.WriteFile(self.stdin_handle, text.encode('utf-8'))

    def connect_reader(self):
        """
        Connect the reader to the event loop.
        """
        return
        #def ready():
        #    for cb in self._input_ready_callbacks:
        #        cb()
        #self.loop.add_win32_handle(self.stdout_handle.handle, ready)

    def disconnect_reader(self):
        """
        Connect the reader to the event loop.
        """
        return
        #self.loop.remove_win32_handle(self.stdout_handle.handle)

    @property
    def closed(self):
        return self.ready_f.done()

    def set_size(self, width, height):
        " Set terminal size. "
        self.pty.set_size(width, height)

    def start(self):
        """
        Start the terminal process.
        """
        self.pty.spawn(SpawnConfig(
            SpawnConfig.flag.auto_shutdown,
            cmdline=r'C:\windows\system32\cmd.exe'))

    def close(self):
        self.process.close()

    def get_name(self):
        """
        Return the name for this process, or `None` when unknown.
        """ 
        return 'cmd.exe'

    def get_cwd(self):
        return
