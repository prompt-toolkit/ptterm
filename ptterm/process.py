"""
The child process.
"""
import time
from asyncio import get_event_loop

from prompt_toolkit.document import Document
from prompt_toolkit.eventloop import call_soon_threadsafe
from prompt_toolkit.utils import is_windows

from .key_mappings import prompt_toolkit_key_to_vt100_key
from .screen import BetterScreen
from .stream import BetterStream

__all__ = ("Process",)


def create_terminal(command, before_exec_func):
    if is_windows():
        from .backends.win32 import Win32Terminal

        return Win32Terminal()
    else:
        from .backends.posix import PosixTerminal

        return PosixTerminal.from_command(command, before_exec_func=before_exec_func)


class Process:
    """
    Child process.
    Functionality for parsing the vt100 output (the Pyte screen and stream), as
    well as sending input to the process.

    Usage:

        p = Process(loop, ...):
        p.start()

    :param invalidate: When the screen content changes, and the renderer needs
        to redraw the output, this callback is called.
    :param bell_func: Called when the process does a `bell`.
    :param commmand: List of command line arguments.
        For instance: `['python', '-c', 'print("test")']`
    :param before_exec_func: Function which is called in the child process,
        right before calling `exec`. Useful for instance for changing the
        current working directory or setting environment variables.
    :param done_callback: Called when the process terminates.
    :param has_priority: Callable that returns True when this Process should
        get priority in the event loop. (When this pane has the focus.)
        Otherwise output can be delayed.
    """

    def __init__(
        self,
        invalidate,
        command=None,
        before_exec_func=None,
        bell_func=None,
        done_callback=None,
        has_priority=None,
    ):
        assert callable(invalidate)
        assert bell_func is None or callable(bell_func)
        assert done_callback is None or callable(done_callback)
        assert has_priority is None or callable(has_priority)

        self.loop = get_event_loop()
        self.invalidate = invalidate
        self.done_callback = done_callback
        self.has_priority = has_priority or (lambda: True)

        self.suspended = False
        self._reader_connected = False

        # Create terminal interface.
        self.terminal = create_terminal(command, before_exec_func=before_exec_func)
        self.terminal.add_input_ready_callback(self._read)

        if done_callback is not None:
            self.terminal.ready_f.add_done_callback(lambda _: done_callback())

        # Create output stream and attach to screen
        self.sx = 0
        self.sy = 0

        self.screen = BetterScreen(
            self.sx, self.sy, write_process_input=self.write_input, bell_func=bell_func
        )

        self.stream = BetterStream(self.screen)
        self.stream.attach(self.screen)

    def start(self):
        """
        Start the process: fork child.
        """
        self.set_size(120, 24)
        self.terminal.start()
        self.terminal.connect_reader()

    def set_size(self, width, height):
        """
        Set terminal size.
        """
        assert isinstance(width, int)
        assert isinstance(height, int)

        if (self.sx, self.sy) != (width, height):
            self.terminal.set_size(width, height)
        self.screen.resize(lines=height, columns=width)

        self.screen.lines = height
        self.screen.columns = width

        self.sx = width
        self.sy = height

    def write_input(self, data, paste=False):
        """
        Write user key strokes to the input.

        :param data: (text, not bytes.) The input.
        :param paste: When True, and the process running here understands
            bracketed paste. Send as pasted text.
        """
        # send as bracketed paste?
        if paste and self.screen.bracketed_paste_enabled:
            data = "\x1b[200~" + data + "\x1b[201~"

        self.terminal.write_text(data)

    def write_key(self, key):
        """
        Write prompt_toolkit Key.
        """
        data = prompt_toolkit_key_to_vt100_key(
            key, application_mode=self.screen.in_application_mode
        )
        self.write_input(data)

    def _read(self):
        """
        Read callback, called by the loop.
        """
        d = self.terminal.read_text(4096)
        assert isinstance(d, str), "got %r" % type(d)
        # Make sure not to read too much at once. (Otherwise, this
        # could block the event loop.)

        if not self.terminal.closed:

            def process():
                self.stream.feed(d)
                self.invalidate()

            # Feed directly, if this process has priority. (That is when this
            # pane has the focus in any of the clients.)
            if self.has_priority():
                process()

            # Otherwise, postpone processing until we have CPU time available.
            else:
                self.terminal.disconnect_reader()

                def do_asap():
                    " Process output and reconnect to event loop. "
                    process()
                    if not self.suspended:
                        self.terminal.connect_reader()

                # When the event loop is saturated because of CPU, we will
                # postpone this processing max 'x' seconds.

                # '1' seems like a reasonable value, because that way we say
                # that we will process max 1k/1s in case of saturation.
                # That should be enough to prevent the UI from feeling
                # unresponsive.
                timestamp = time.time() + 1

                call_soon_threadsafe(do_asap, max_postpone_time=timestamp)
        else:
            # End of stream. Remove child.
            self.terminal.disconnect_reader()

    def suspend(self):
        """
        Suspend process. Stop reading stdout. (Called when going into copy mode.)
        """
        if not self.suspended:
            self.suspended = True
            self.terminal.disconnect_reader()

    def resume(self):
        """
        Resume from 'suspend'.
        """
        if self.suspended:
            self.terminal.connect_reader()
            self.suspended = False

    def get_cwd(self):
        """
        The current working directory for this process. (Or `None` when
        unknown.)
        """
        return self.terminal.get_cwd()

    def get_name(self):
        """
        The name for this process. (Or `None` when unknown.)
        """
        # TODO: Maybe cache for short time.
        return self.terminal.get_name()

    def kill(self):
        """
        Kill process.
        """
        self.terminal.kill()

    @property
    def is_terminated(self):
        return self.terminal.closed

    def create_copy_document(self):
        """
        Create a Document instance and token list that can be used in copy
        mode.
        """
        data_buffer = self.screen.data_buffer
        text = []
        token_lists = []

        first_row = min(data_buffer.keys())
        last_row = max(data_buffer.keys())

        def token_has_no_background(token):
            try:
                # Token looks like ('C', color, bgcolor, bold, underline, ...)
                return token[2] is None
            except IndexError:
                return True

        for lineno in range(first_row, last_row + 1):
            token_list = []

            row = data_buffer[lineno]
            max_column = max(row.keys()) if row else 0

            # Remove trailing whitespace. (If the background is transparent.)
            row_data = [row[x] for x in range(0, max_column + 1)]

            while (
                row_data
                and row_data[-1].char.isspace()
                and token_has_no_background(row_data[-1].token)
            ):
                row_data.pop()

            # Walk through row.
            char_iter = iter(range(len(row_data)))

            for x in char_iter:
                c = row[x]
                text.append(c.char)
                token_list.append((c.token, c.char))

                # Skip next cell when this is a double width character.
                if c.width == 2:
                    try:
                        next(char_iter)
                    except StopIteration:
                        pass

            token_lists.append(token_list)
            text.append("\n")

        def get_tokens_for_line(lineno):
            try:
                return token_lists[lineno]
            except IndexError:
                return []

        # Calculate cursor position.
        d = Document(text="".join(text))

        return (
            Document(
                text=d.text,
                cursor_position=d.translate_row_col_to_index(
                    row=self.screen.pt_screen.cursor_position.y,
                    col=self.screen.pt_screen.cursor_position.x,
                ),
            ),
            get_tokens_for_line,
        )
