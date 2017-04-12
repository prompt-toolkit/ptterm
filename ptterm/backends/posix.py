from __future__ import unicode_literals
from prompt_toolkit.input.posix_utils import PosixStdinReader
from prompt_toolkit.eventloop import Future, get_event_loop
from .base import Terminal
from .posix_utils import set_terminal_size, pty_make_controlling_tty
import os
import sys
import signal
import time
import resource
import traceback

__all__ = (
    'PosixTerminal',
)


class PosixTerminal(Terminal):
    def __init__(self, exec_func):
        self.exec_func = exec_func

        # Create pseudo terminal for this pane.
        self.master, self.slave = os.openpty()

        # Master side -> attached to terminal emulator.
        self._reader = PosixStdinReader(self.master, errors='replace')
        self._reader_connected = False
        self._input_ready_callbacks = []

        self.ready_f = Future()
        self.loop = get_event_loop()
        self.pid = None

    def add_input_ready_callback(self, callback):
        self._input_ready_callbacks.append(callback)

    @classmethod
    def from_command(cls, command, before_exec_func=None):
        """
        Create Process from command,
        e.g. command=['python', '-c', 'print("test")']

        :param before_exec_func: Function that is called before `exec` in the
            process fork.
        """
        assert isinstance(command, list)
        assert before_exec_func is None or callable(before_exec_func)

        def execv():
            if before_exec_func:
                before_exec_func()

            for p in os.environ['PATH'].split(':'):
                path = os.path.join(p, command[0])
                if os.path.exists(path) and os.access(path, os.X_OK):
                    os.execv(path, command)

        return cls(execv)

    def connect_reader(self):
        if self.master is not None and not self._reader_connected:
            def ready():
                for cb in self._input_ready_callbacks:
                    cb()

            self.loop.add_reader(self.master, ready)
            self._reader_connected = True

    @property
    def closed(self):
        return self._reader.closed

    def disconnect_reader(self):
        if self.master is not None and self._reader_connected:
            self.loop.remove_reader(self.master)
            self._reader_connected = False

    def read_text(self, amount=4096):
        return self._reader.read(amount)

    def write_text(self, text):
        self.write_bytes(text.encode('utf-8'))

    def write_bytes(self, data):
        while self.master is not None:
            try:
                os.write(self.master, data)
            except OSError as e:
                # This happens when the window resizes and a SIGWINCH was received.
                # We get 'Error: [Errno 4] Interrupted system call'
                if e.errno == 4:
                    continue
            return

    def set_size(self, width, height):
        """
        Set terminal size.
        """
        assert isinstance(width, int)
        assert isinstance(height, int)

        if self.master is not None:
            set_terminal_size(self.master, height, width)

    def start(self):
        """
        Create fork and start the child process.
        """
        pid = os.fork()

        if pid == 0:
            self._in_child()
        elif pid > 0:
            # We wait a very short while, to be sure the child had the time to
            # call _exec. (Otherwise, we are still sharing signal handlers and
            # FDs.) Resizing the pty, when the child is still in our Python
            # code and has the signal handler from prompt_toolkit, but closed
            # the 'fd' for 'call_from_executor', will cause OSError.
            time.sleep(0.1)

            self.pid = pid

            # Wait for the process to finish.
            self._waitpid()

    def close(self):
        # TODO: os.kill
        pass

    def _in_child(self):
        " Will be executed in the forked child. "
        os.close(self.master)

        # Remove signal handler for SIGWINCH as early as possible.
        # (We don't want this to be triggered when execv has not been called
        # yet.)
        signal.signal(signal.SIGWINCH, 0)

        pty_make_controlling_tty(self.slave)

        # In the fork, set the stdin/out/err to our slave pty.
        os.dup2(self.slave, 0)
        os.dup2(self.slave, 1)
        os.dup2(self.slave, 2)

        # Execute in child.
        try:
            self._close_file_descriptors()
            self.exec_func()
        except Exception:
            traceback.print_exc()
            time.sleep(5)

            os._exit(1)
        os._exit(0)

    def _close_file_descriptors(self):
        # Do not allow child to inherit open file descriptors from parent.
        # (In case that we keep running Python code. We shouldn't close them.
        # because the garbage collector is still active, and he will close them
        # eventually.)
        max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[-1]

        try:
            os.closerange(3, max_fd)
        except OverflowError:
            # On OS X, max_fd can return very big values, than closerange
            # doesn't understand, e.g. 9223372036854775807. In this case, just
            # use 4096. This is what Linux systems report, and should be
            # sufficient. (I hope...)
            os.closerange(3, 4096)

    def _waitpid(self):
        """
        Create an executor that waits and handles process termination.
        """
        def wait_for_finished():
            " Wait for PID in executor. "
            os.waitpid(self.pid, 0)
            self.loop.call_from_executor(done)

        def done():
            " PID received. Back in the main thread. "
            # Close pty and remove reader.

            self.disconnect_reader()
            os.close(self.master)
            os.close(self.slave)

            self.master = None

            # Callback.
            self.is_terminated = True

            self.ready_f.set_result(None)

        self.loop.run_in_executor(wait_for_finished)

    def get_name(self):
        if self.master is not None:
            return get_name_for_fd(self.master)

    def get_cwd(self):
        if self.pid:
            return get_cwd_for_pid(self.pid)



if sys.platform in ('linux', 'linux2', 'cygwin'):
    def get_name_for_fd(fd):
        """
        Return the process name for a given process ID.

        :param fd: Slave file descriptor. (Often the master fd works as well,
            but apparentsly on WSL only the slave FD works.)
        """
        try:
            pgrp = os.tcgetpgrp(fd)
        except OSError:
            # See: https://github.com/jonathanslenders/pymux/issues/46
            return

        try:
            with open('/proc/%s/cmdline' % pgrp, 'rb') as f:
                return f.read().decode('utf-8', 'ignore').partition('\0')[0]
        except IOError:
            pass
elif sys.platform == 'darwin':
    from .darwin import get_proc_name

    def get_name_for_fd(fd):
        """
        Return the process name for a given process ID.

        NOTE: on Linux, this seems to require the master FD.
        """
        try:
            pgrp = os.tcgetpgrp(fd)
        except OSError:
            return

        try:
            return get_proc_name(pgrp)
        except IOError:
            pass
else:
    def get_name_for_fd(fd):
        """
        Return the process name for a given process ID.
        """
        return


def get_cwd_for_pid(pid):
    """
    Return the current working directory for a given process ID.
    """
    if sys.platform in ('linux', 'linux2', 'cygwin'):
        try:
            return os.readlink('/proc/%s/cwd' % pid)
        except OSError:
            pass
