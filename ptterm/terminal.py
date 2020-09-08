"""
The layout engine. This builds the prompt_toolkit layout.
"""
from typing import Callable, Iterable, List, Optional

from prompt_toolkit.application.current import get_app, get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_selection
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.processors import (
    HighlightIncrementalSearchProcessor,
    HighlightSearchProcessor,
    HighlightSelectionProcessor,
    Processor,
    Transformation,
)
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import Event, is_windows
from prompt_toolkit.widgets.toolbars import SearchToolbar

from .backends import Backend
from .process import Process

__all__ = ["Terminal"]


class _TerminalControl(UIControl):
    def __init__(
        self,
        backend: Backend,
        done_callback: Optional[Callable[[], None]] = None,
        bell_func: Optional[Callable[[], None]] = None,
    ) -> None:
        def has_priority() -> bool:
            # Give priority to the processing of this terminal output, if this
            # user control has the focus.
            app_or_none = get_app_or_none()

            if app_or_none is None:
                # The application has terminated before this process ended.
                return False

            return app_or_none.layout.has_focus(self)

        self.process = Process(
            lambda: self.on_content_changed.fire(),
            backend=backend,
            done_callback=done_callback,
            bell_func=bell_func,
            has_priority=has_priority,
        )

        self.on_content_changed = Event(self)
        self._running = False

    def create_content(self, width: int, height: int) -> UIContent:
        # Report dimensions to the process.
        self.process.set_size(width, height)

        # The first time that this user control is rendered. Keep track of the
        # 'app' object and start the process.
        if not self._running:
            self.process.start()
            self._running = True

        if not self.process.screen:
            return UIContent()

        pt_screen = self.process.screen.pt_screen
        pt_cursor_position = self.process.screen.pt_cursor_position
        data_buffer = pt_screen.data_buffer
        cursor_y = pt_cursor_position.y

        # Prompt_toolkit needs the amount of characters before the cursor in a
        # UIControl.  This doesn't correspond with the xpos in case of double
        # width characters. That's why we compute the wcwidth.
        cursor_row = data_buffer[pt_cursor_position.y]
        text_before_cursor = "".join(
            cursor_row[x].char for x in range(0, pt_cursor_position.x)
        )
        cursor_x = len(text_before_cursor)

        def get_line(number: int) -> StyleAndTextTuples:
            row = data_buffer[number]
            empty = True
            if row:
                max_column = max(row)
                empty = False
            else:
                max_column = 0

            if number == cursor_y:
                max_column = max(max_column, cursor_x)
                empty = False

            if empty:
                return [("", " ")]
            else:
                cells = [row[i] for i in range(max_column + 1)]
                return [(cell.style, cell.char) for cell in cells]

        if data_buffer:
            line_count = (
                max(data_buffer) + 1
            )  # TODO: substract all empty lines from the beginning. (If we need to. Not sure.)
        else:
            line_count = 1

        return UIContent(
            get_line,
            line_count=line_count,
            show_cursor=pt_screen.show_cursor,
            cursor_position=Point(x=cursor_x, y=cursor_y),
        )

    def get_key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add(Keys.Any)
        def handle_key(event):
            """
            Handle any key binding -> write it to the stdin of this terminal.
            """
            self.process.write_key(event.key_sequence[0].key)

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            self.process.write_input(event.data, paste=True)

        return bindings

    def get_invalidate_events(self) -> Iterable[Event]:
        yield self.on_content_changed

    def mouse_handler(self, mouse_event) -> None:
        """
        Handle mouse events in a pane. A click in a non-active pane will select
        it. A click in active pane will send the mouse event to the application
        running inside it.
        """
        app = get_app()

        process = self.process
        x = mouse_event.position.x
        y = mouse_event.position.y

        # The containing Window translates coordinates to the absolute position
        # of the whole screen, but in this case, we need the relative
        # coordinates of the visible area.
        y -= self.process.screen.line_offset

        if not app.layout.has_focus(self):
            # Focus this process when the mouse has been clicked.
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                app.layout.focus(self)
        else:
            # Already focussed, send event to application when it requested
            # mouse support.
            if process.screen.sgr_mouse_support_enabled:
                # Xterm SGR mode.
                try:
                    ev, m = {
                        MouseEventType.MOUSE_DOWN: (0, "M"),
                        MouseEventType.MOUSE_UP: (0, "m"),
                        MouseEventType.SCROLL_UP: (64, "M"),
                        MouseEventType.SCROLL_DOWN: (65, "M"),
                    }[mouse_event.event_type]
                except KeyError:
                    pass
                else:
                    self.process.write_input("\x1b[<%s;%s;%s%s" % (ev, x + 1, y + 1, m))

            elif process.screen.urxvt_mouse_support_enabled:
                # Urxvt mode.
                try:
                    ev = {
                        MouseEventType.MOUSE_DOWN: 32,
                        MouseEventType.MOUSE_UP: 35,
                        MouseEventType.SCROLL_UP: 96,
                        MouseEventType.SCROLL_DOWN: 97,
                    }[mouse_event.event_type]
                except KeyError:
                    pass
                else:
                    self.process.write_input("\x1b[%s;%s;%sM" % (ev, x + 1, y + 1))

            elif process.screen.mouse_support_enabled:
                # Fall back to old mode.
                if x < 96 and y < 96:
                    try:
                        ev = {
                            MouseEventType.MOUSE_DOWN: 32,
                            MouseEventType.MOUSE_UP: 35,
                            MouseEventType.SCROLL_UP: 96,
                            MouseEventType.SCROLL_DOWN: 97,
                        }[mouse_event.event_type]
                    except KeyError:
                        pass
                    else:
                        self.process.write_input(
                            "\x1b[M%s%s%s" % (chr(ev), chr(x + 33), chr(y + 33))
                        )

    def is_focusable(self) -> bool:
        return not self.process.suspended


class _Window(Window):
    """
    """

    def __init__(self, terminal_control: _TerminalControl, **kw) -> None:
        self.terminal_control = terminal_control
        super().__init__(**kw)

    def write_to_screen(self, *a, **kw) -> None:
        # Make sure that the bottom of the terminal is always visible.
        screen = self.terminal_control.process.screen

        # NOTE: the +1 is required because max_y starts counting at 0, while
        #       lines counts the numbers of lines, starting at 1 for one line.
        self.vertical_scroll = screen.max_y - screen.lines + 1

        super().write_to_screen(*a, **kw)


def create_backend(
    command: List[str], before_exec_func: Optional[Callable[[], None]]
) -> Backend:
    if is_windows():
        from .backends.win32 import Win32Backend

        return Win32Backend()
    else:
        from .backends.posix import PosixBackend

        return PosixBackend.from_command(command, before_exec_func=before_exec_func)


class Terminal:
    """
    Terminal widget for use in a prompt_toolkit layout.

    :param commmand: List of command line arguments.
        For instance: `['python', '-c', 'print("test")']`
    :param before_exec_func: Function which is called in the child process,
        right before calling `exec`. Useful for instance for changing the
        current working directory or setting environment variables.
    """

    def __init__(
        self,
        command=["/bin/bash"],
        before_exec_func=None,
        backend: Optional[Backend] = None,
        bell_func: Optional[Callable[[], None]] = None,
        style: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:

        if backend is None:
            backend = create_backend(command, before_exec_func)

        self.terminal_control = _TerminalControl(
            backend=backend, bell_func=bell_func, done_callback=done_callback,
        )

        self.terminal_window = _Window(
            terminal_control=self.terminal_control,
            content=self.terminal_control,
            wrap_lines=False,
        )

        # Key bindigns for copy buffer.
        kb = KeyBindings()

        @kb.add("c-c")
        def _exit(event):
            self.exit_copy_mode()

        @kb.add("space")
        def _reset_selection(event):
            " Reset selection. "
            event.current_buffer.start_selection()

        @kb.add("enter", filter=has_selection)
        def _copy_selection(event):
            " Copy selection. "
            data = event.current_buffer.copy_selection()
            event.app.clipboard.set_data(data)

        self.search_toolbar = SearchToolbar(
            forward_search_prompt="Search down: ", backward_search_prompt="Search up: "
        )

        self.copy_buffer = Buffer(read_only=True)
        self.copy_buffer_control = BufferControl(
            buffer=self.copy_buffer,
            search_buffer_control=self.search_toolbar.control,
            include_default_input_processors=False,
            input_processors=[
                _UseStyledTextProcessor(self),
                HighlightSelectionProcessor(),
                HighlightSearchProcessor(),
                HighlightIncrementalSearchProcessor(),
            ],
            preview_search=True,  # XXX: not sure why we need twice preview_search.
            key_bindings=kb,
        )

        self.copy_window = Window(content=self.copy_buffer_control, wrap_lines=False)

        self.is_copying = False

        @Condition
        def is_copying() -> bool:
            return self.is_copying

        self.container = FloatContainer(
            content=HSplit(
                [
                    # Either show terminal window or copy buffer.
                    VSplit(
                        [  # XXX: this nested VSplit should not have been necessary,
                            # but the ConditionalContainer which width can become
                            # zero will collapse the other elements.
                            ConditionalContainer(
                                self.terminal_window, filter=~is_copying
                            ),
                            ConditionalContainer(self.copy_window, filter=is_copying),
                        ]
                    ),
                    ConditionalContainer(self.search_toolbar, filter=is_copying),
                ],
                style=style,
                width=width,
                height=height,
            ),
            floats=[
                Float(
                    top=0,
                    right=0,
                    height=1,
                    content=ConditionalContainer(
                        Window(
                            content=FormattedTextControl(
                                text=self._copy_position_formatted_text
                            ),
                            style="class:copy-mode-cursor-position",
                        ),
                        filter=is_copying,
                    ),
                )
            ],
        )

    def _copy_position_formatted_text(self) -> str:
        """
        Return the cursor position text to be displayed in copy mode.
        """
        render_info = self.copy_window.render_info
        if render_info:
            return "[%s/%s]" % (
                render_info.cursor_position.y + 1,
                render_info.content_height,
            )
        else:
            return "[0/0]"

    def enter_copy_mode(self) -> None:
        # Suspend process.
        self.terminal_control.process.suspend()

        # Copy content into copy buffer.
        data_buffer = self.terminal_control.process.screen.pt_screen.data_buffer

        text = []
        styled_lines = []

        if data_buffer:
            for line_index in range(min(data_buffer), max(data_buffer) + 1):
                line = data_buffer[line_index]
                styled_line = []

                if line:
                    for column_index in range(0, max(line) + 1):
                        char = line[column_index]
                        text.append(char.char)
                        styled_line.append((char.style, char.char))

                text.append("\n")
                styled_lines.append(styled_line)
            text.pop()  # Drop last line ending.

        text_str = "".join(text)

        self.copy_buffer.set_document(
            Document(text=text_str, cursor_position=len(text_str)), bypass_readonly=True
        )

        self.styled_lines = styled_lines

        # Enter copy mode.
        self.is_copying = True
        get_app().layout.focus(self.copy_window)

    def exit_copy_mode(self) -> None:
        # Resume process.
        self.terminal_control.process.resume()

        # focus terminal again.
        self.is_copying = False
        get_app().layout.focus(self.terminal_window)

    def __pt_container__(self) -> FloatContainer:
        return self.container

    @property
    def process(self):
        return self.terminal_control.process


class _UseStyledTextProcessor(Processor):
    """
    In order to allow highlighting of the copy region, we use a preprocessed
    list of (style, text) tuples. This processor returns just that list for the
    given pane.

    This processor should go before all others, because it replaces the list of
    (style, text) tuples.
    """

    def __init__(self, terminal: Terminal) -> None:
        self.terminal = terminal

    def apply_transformation(self, transformation_input) -> Transformation:
        try:
            line = self.terminal.styled_lines[transformation_input.lineno]
        except IndexError:
            line = []
        return Transformation(line)
