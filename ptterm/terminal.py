# encoding: utf-8
"""
The layout engine. This builds the prompt_toolkit layout.
"""
from __future__ import unicode_literals

from prompt_toolkit.application.current import get_app, NoRunningApplicationError
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import Window, HSplit, VSplit, ConditionalContainer
from prompt_toolkit.layout.controls import UIControl, UIContent, BufferControl
from prompt_toolkit.layout.processors import Processor, HighlightSearchProcessor, HighlightIncrementalSearchProcessor, merge_processors, Transformation
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.widgets.toolbars import SearchToolbar
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import Event

import six
from six.moves import range

from .process import Process

__all__ = (
    'TerminalControl',
    'Terminal',
)


class TerminalControl(UIControl):
    def __init__(self, command=['/bin/bash'], done_callback=None,
                 before_exec_func=None, bell_func=None):
        assert isinstance(command, list)
        assert done_callback is None or callable(done_callback)
        assert before_exec_func is None or callable(before_exec_func)
        assert bell_func is None or callable(bell_func)

        def has_priority():
            # Give priority to the processing of this terminal output, if this
            # user control has the focus.
            try:
                app = get_app(raise_exception=True)
            except NoRunningApplicationError:
                # The application has terminated before this process ended.
                return False
            else:
                return app.layout.has_focus(self)

        self.process = Process(
            lambda: self.on_content_changed.fire(),
            command=command,
            before_exec_func=before_exec_func,
            done_callback=done_callback,
            bell_func=bell_func,
            has_priority=has_priority)

        self.on_content_changed = Event(self)
        self._running = False

    def create_content(self, width, height):
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
        data_buffer = pt_screen.data_buffer
        cursor_y = pt_screen.cursor_position.y

        # Prompt_toolkit needs the amount of characters before the cursor in a
        # UIControl.  This doesn't correspond with the xpos in case of double
        # width characters. That's why we compute the wcwidth.
        cursor_row = data_buffer[pt_screen.cursor_position.y]
        text_before_cursor = ''.join(cursor_row[x].char for x in range(0, pt_screen.cursor_position.x))
        cursor_x = len(text_before_cursor)

        def get_line(number):
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
                return [('', ' ')]
            else:
                cells = [row[i] for i in range(max_column + 1)]
                return [(cell.style, cell.char) for cell in cells]

        if data_buffer:
            line_count = max(data_buffer) + 1    # TODO: substract all empty lines from the beginning. (If we need to. Not sure.)
        else:
            line_count = 1

        return UIContent(
            get_line, line_count=line_count,
            show_cursor=pt_screen.show_cursor,
            cursor_position=Point(x=cursor_x, y=cursor_y))

    def get_key_bindings(self):
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

    def get_invalidate_events(self):
        yield self.on_content_changed

    def mouse_handler(self, mouse_event):
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
                ev, m = {
                    MouseEventType.MOUSE_DOWN: ('0', 'M'),
                    MouseEventType.MOUSE_UP: ('0', 'm'),
                    MouseEventType.SCROLL_UP: ('64', 'M'),
                    MouseEventType.SCROLL_DOWN: ('65', 'M'),
                }.get(mouse_event.event_type)

                self.process.write_input(
                    '\x1b[<%s;%s;%s%s' % (ev, x + 1, y + 1, m))

            elif process.screen.urxvt_mouse_support_enabled:
                # Urxvt mode.
                ev = {
                    MouseEventType.MOUSE_DOWN: 32,
                    MouseEventType.MOUSE_UP: 35,
                    MouseEventType.SCROLL_UP: 96,
                    MouseEventType.SCROLL_DOWN: 97,
                }.get(mouse_event.event_type)

                self.process.write_input(
                    '\x1b[%s;%s;%sM' % (ev, x + 1, y + 1))

            elif process.screen.mouse_support_enabled:
                # Fall back to old mode.
                if x < 96 and y < 96:
                    ev = {
                            MouseEventType.MOUSE_DOWN: 32,
                            MouseEventType.MOUSE_UP: 35,
                            MouseEventType.SCROLL_UP: 96,
                            MouseEventType.SCROLL_DOWN: 97,
                    }.get(mouse_event.event_type)

                    self.process.write_input('\x1b[M%s%s%s' % (
                        six.unichr(ev),
                        six.unichr(x + 33),
                        six.unichr(y + 33)))

    def is_focusable(self):
        return not self.process.suspended


class _Window(Window):
    """
    """
    def __init__(self, terminal_control, **kw):
        self.terminal_control = terminal_control
        super(_Window, self).__init__(**kw)

    def write_to_screen(self, *a, **kw):
        # Make sure that the bottom of the terminal is always visible.
        screen = self.terminal_control.process.screen

        # NOTE: the +1 is required because max_y starts counting at 0, while
        #       lines counts the numbers of lines, starting at 1 for one line.
        self.vertical_scroll = screen.max_y - screen.lines + 1

        super(_Window, self).write_to_screen(*a, **kw)


class Terminal(object):
    def __init__(self, command=['/bin/bash'], before_exec_func=None,
                 bell_func=None, style='', width=None, height=None,
                 done_callback=None):

        self.terminal_control = TerminalControl(
            command=command, before_exec_func=before_exec_func,
            bell_func=bell_func, done_callback=done_callback)

        self.terminal_window = _Window(
            terminal_control=self.terminal_control,
            content=self.terminal_control,
            wrap_lines=False)

        # Key bindigns for copy buffer.
        kb = KeyBindings()
        @kb.add('c-c')
        def _(event):
            self.exit_copy_mode()

        self.search_toolbar = SearchToolbar(
            forward_search_prompt='Search down: ',
            backward_search_prompt='Search up: ')

        self.copy_buffer = Buffer(read_only=True)
        self.copy_buffer_control = BufferControl(
            buffer=self.copy_buffer,
            search_buffer_control=self.search_toolbar.control,
            input_processors=[
                _UseStyledTextrocessor(self),
                HighlightSearchProcessor(),
                HighlightIncrementalSearchProcessor(),
            ],
            preview_search=True,  # XXX: not sure why we need twice preview_search.
            key_bindings=kb)

        self.copy_window = Window(content=self.copy_buffer_control, wrap_lines=False)

        self.is_copying = False
        self.styled_copy_lines = []  # List of lists of (style, text) tuples, for each line.

        @Condition
        def is_copying():
            return self.is_copying

        self.container = HSplit([
            # Either show terminal window or copy buffer.
            VSplit([  # XXX: this nested VSplit should not have been necessary,
                        # but the ConditionalContainer which width can become
                        # zero will collapse the other elements.
                ConditionalContainer(self.terminal_window, filter=~is_copying),
                ConditionalContainer(self.copy_window, filter=is_copying),
            ]),
            ConditionalContainer(self.search_toolbar, filter=is_copying),
        ], style=style, width=width, height=height)

    def enter_copy_mode(self):
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

                text.append('\n')
                styled_lines.append(styled_line)
            text.pop()  # Drop last line ending.

        text = ''.join(text)

        self.copy_buffer.set_document(
            Document(text=text, cursor_position=len(text)),
            bypass_readonly=True)

        self.styled_lines = styled_lines

        # Enter copy mode.
        self.is_copying = True
        get_app().layout.focus(self.copy_window)

    def exit_copy_mode(self):
        # Resume process.
        self.terminal_control.process.resume()

        # focus terminal again.
        self.is_copying = False
        get_app().layout.focus(self.terminal_window)

    def __pt_container__(self):
        return self.container

    @property
    def process(self):
        return self.terminal_control.process


class _UseStyledTextrocessor(Processor):
    """
    In order to allow highlighting of the copy region, we use a preprocessed
    list of (style, text) tuples. This processor returns just that list for the
    given pane.

    This processor should go before all others, because it replaces the list of
    (style, text) tuples.
    """
    def __init__(self, terminal):
        self.terminal = terminal

    def apply_transformation(self, transformation_input):
        try:
            line = self.terminal.styled_lines[transformation_input.lineno]
        except IndexError:
            line = []
        return Transformation(line)
