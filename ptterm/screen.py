"""
Custom `Screen` class for the `pyte` library.

Changes compared to the original `Screen` class:
    - We store the layout in a prompt_toolkit.layout.screen.Screen instance.
      This allows fast rendering in a prompt_toolkit user control.
    - 256 colour and true color support.
    - CPR support and device attributes.
"""
from collections import defaultdict, namedtuple
from typing import Callable, Dict, List, Optional, Tuple

from pyte import charsets as cs
from pyte import modes as mo
from pyte.screens import Margins

from prompt_toolkit.cache import FastDictCache
from prompt_toolkit.layout.screen import Char, Screen
from prompt_toolkit.output.vt100 import BG_ANSI_COLORS, FG_ANSI_COLORS
from prompt_toolkit.output.vt100 import _256_colors as _256_colors_table
from prompt_toolkit.styles import Attrs

__all__ = ("BetterScreen",)


class CursorPosition:
    " Mutable CursorPosition. "

    def __init__(self, x: int = 0, y: int = 0) -> None:
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return "pymux.CursorPosition(x=%r, y=%r)" % (self.x, self.y)


class _UnicodeInternDict(Dict[str, str]):
    """
    Intern dictionary for interning unicode strings. This should save memory
    and make our cache faster.
    """

    def __missing__(self, value: str) -> str:
        self[value] = value
        return value


_unicode_intern_dict = _UnicodeInternDict()


# Cache for Char objects.
_CHAR_CACHE: FastDictCache[Tuple[str, str], Char] = FastDictCache(
    Char, size=1000 * 1000
)


# Custom Savepoint that also stores the Attrs.
_Savepoint = namedtuple(
    "_Savepoint",
    [
        "cursor_x",
        "cursor_y",
        "g0_charset",
        "g1_charset",
        "charset",
        "origin",
        "wrap",
        "attrs",
        "style_str",
    ],
)


class BetterScreen:
    """
    Custom screen class. Most of the methods are called from a vt100 Pyte
    stream.

    The data buffer is stored in a :class:`prompt_toolkit.layout.screen.Screen`
    class, because this way, we can send it to the renderer without any
    transformation.
    """

    swap_variables = [
        "mode",
        "margins",
        "charset",
        "g0_charset",
        "g1_charset",
        "tabstops",
        "data_buffer",
        "pt_cursor_position",
        "max_y",
    ]

    def __init__(
        self,
        lines: int,
        columns: int,
        write_process_input: Callable[[str], None],
        bell_func: Optional[Callable[[], None]] = None,
        get_history_limit: Optional[Callable[[], int]] = None,
    ):
        bell_func = bell_func or (lambda: None)
        get_history_limit = get_history_limit or (lambda: 2000)

        self._history_cleanup_counter = 0

        self.savepoints: List[_Savepoint] = []
        self.lines = lines
        self.columns = columns
        self.write_process_input = write_process_input
        self.bell_func = bell_func
        self.get_history_limit = get_history_limit
        self.reset()

    @property
    def in_application_mode(self) -> bool:
        """
        True when we are in application mode. This means that the process is
        expecting some other key sequences as input. (Like for the arrows.)
        """
        # Not in cursor mode.
        return (1 << 5) in self.mode

    @property
    def mouse_support_enabled(self) -> bool:
        " True when mouse support has been enabled by the application. "
        return (1000 << 5) in self.mode

    @property
    def urxvt_mouse_support_enabled(self) -> bool:
        return (1015 << 5) in self.mode

    @property
    def sgr_mouse_support_enabled(self) -> bool:
        " Xterm Sgr mouse support. "
        return (1006 << 5) in self.mode

    @property
    def bracketed_paste_enabled(self) -> bool:
        return (2004 << 5) in self.mode

    @property
    def has_reverse_video(self) -> bool:
        " The whole screen is set to reverse video. "
        return mo.DECSCNM in self.mode

    def reset(self) -> None:
        """Resets the terminal to its initial state.

        * Scroll margins are reset to screen boundaries.
        * Cursor is moved to home location -- ``(0, 0)`` and its
          attributes are set to defaults (see :attr:`default_char`).
        * Screen is cleared -- each character is reset to
          :attr:`default_char`.
        * Tabstops are reset to "every eight columns".

        .. note::

           Neither VT220 nor VT102 manuals mentioned that terminal modes
           and tabstops should be reset as well, thanks to
           :manpage:`xterm` -- we now know that.
        """
        self._reset_screen()

        self.title = ""
        self.icon_name = ""

        # Reset modes.
        self.mode = set(
            [
                mo.DECAWM,  # Autowrap mode. (default: disabled).
                mo.DECTCEM,  # Text cursor enable mode. (default enabled).
            ]
        )

        # According to VT220 manual and ``linux/drivers/tty/vt.c``
        # the default G0 charset is latin-1, but for reasons unknown
        # latin-1 breaks ascii-graphics; so G0 defaults to cp437.

        # XXX: The comment above comes from the original Pyte implementation,
        #      it seems for us that LAT1_MAP should indeed be the default, if
        #      not a French version of Vim would incorrectly show some
        #      characters.
        self.charset = 0
        # self.g0_charset = cs.IBMPC_MAP
        self.g0_charset = cs.LAT1_MAP
        self.g1_charset = cs.VT100_MAP

        # From ``man terminfo`` -- "... hardware tabs are initially
        # set every `n` spaces when the terminal is powered up. Since
        # we aim to support VT102 / VT220 and linux -- we use n = 8.

        # (We choose to create tab stops until x=1000, because we keep the
        # tab stops when the screen increases in size. The OS X 'ls' command
        # relies on the stops to be there.)
        self.tabstops = set(range(8, 1000, 8))

        # The original Screen instance, when going to the alternate screen.
        self._original_screen: Optional[Screen] = None

    def _reset_screen(self) -> None:
        """Reset the Screen content. (also called when switching from/to
        alternate buffer."""
        self.pt_screen = Screen(
            default_char=Char(" ", "")
        )  # TODO: Stop using this Screen class!

        self.pt_screen.show_cursor = True

        self.data_buffer = self.pt_screen.data_buffer
        self.pt_cursor_position = CursorPosition(0, 0)
        # List of line indexes that were wrapped.
        self.wrapped_lines: List[int] = []

        self._attrs = Attrs(
            color=None,
            bgcolor=None,
            bold=False,
            underline=False,
            italic=False,
            blink=False,
            reverse=False,
            hidden=False,
        )
        self._style_str = ""

        self.margins = None

        self.max_y = 0  # Max 'y' position to which is written.

    def resize(
        self, lines: Optional[int] = None, columns: Optional[int] = None
    ) -> None:
        # Save the dimensions.
        lines = lines if lines is not None else self.lines
        columns = columns if columns is not None else self.columns

        if self.lines != lines or self.columns != columns:
            self.lines = lines
            self.columns = columns

            self._reset_offset_and_margins()

            # If the height was reduced, and there are lines below
            # `cursor_position_y+lines`. Remove them by setting 'max_y'.
            # (If we don't do this. Clearing the screen, followed by reducing
            # the height will keep the cursor at the top, hiding some content.)
            self.max_y = min(self.max_y, self.pt_cursor_position.y + lines - 1)

            self._reflow()

    @property
    def line_offset(self) -> int:
        " Return the index of the first visible line. "
        cpos_y = self.pt_cursor_position.y

        # NOTE: the +1 is required because `max_y` starts counting at 0 for the
        #       first line, while `self.lines` is the number of lines, starting
        #       at 1 for one line. The offset refers to the index of the first
        #       visible line.
        #       For instance, if we have: max_y=14 and lines=15. Then all lines
        #       from 0..14 have been used. This means 15 lines are used, and
        #       the first index should be 0.
        return max(0, min(cpos_y, self.max_y - self.lines + 1))

    def set_margins(
        self, top: Optional[int] = None, bottom: Optional[int] = None
    ) -> None:
        """Selects top and bottom margins for the scrolling region.
        Margins determine which screen lines move during scrolling
        (see :meth:`index` and :meth:`reverse_index`). Characters added
        outside the scrolling region do not cause the screen to scroll.
        :param int top: the smallest line number that is scrolled.
        :param int bottom: the biggest line number that is scrolled.
        """
        if top is None and bottom is None:
            return

        margins = self.margins or Margins(0, self.lines - 1)

        top = margins.top if top is None else top - 1
        bottom = margins.bottom if bottom is None else bottom - 1

        # Arguments are 1-based, while :attr:`margins` are zero based --
        # so we have to decrement them by one. We also make sure that
        # both of them is bounded by [0, lines - 1].
        top = max(0, min(top, self.lines - 1))
        bottom = max(0, min(bottom, self.lines - 1))

        # Even though VT102 and VT220 require DECSTBM to ignore regions
        # of width less than 2, some programs (like aptitude for example)
        # rely on it. Practicality beats purity.
        if bottom - top >= 1:
            self.margins = Margins(top, bottom)

            # The cursor moves to the home position when the top and
            # bottom margins of the scrolling region (DECSTBM) changes.
            self.cursor_position()

    def _reset_offset_and_margins(self) -> None:
        """
        Recalculate offset and move cursor (make sure that the bottom is
        visible.)
        """
        self.margins = None

    def set_charset(self, code, mode) -> None:
        """Set active ``G0`` or ``G1`` charset.

        :param str code: character set code, should be a character
                         from ``"B0UK"`` -- otherwise ignored.
        :param str mode: if ``"("`` ``G0`` charset is set, if
                         ``")"`` -- we operate on ``G1``.

        .. warning:: User-defined charsets are currently not supported.
        """
        if code in cs.MAPS:
            charset_map = cs.MAPS[code]
            if mode == "(":
                self.g0_charset = charset_map
            elif mode == ")":
                self.g1_charset = charset_map

    def set_mode(self, *modes, **kwargs) -> None:
        # Private mode codes are shifted, to be distingiushed from non
        # private ones.
        if kwargs.get("private"):
            modes = tuple(mode << 5 for mode in modes)

        self.mode.update(modes)

        # When DECOLM mode is set, the screen is erased and the cursor
        # moves to the home position.
        if mo.DECCOLM in modes:
            self.resize(columns=132)
            self.erase_in_display(2)
            self.cursor_position()

        # According to `vttest`, DECOM should also home the cursor, see
        # vttest/main.c:303.
        if mo.DECOM in modes:
            self.cursor_position()

        # Make the cursor visible.
        if mo.DECTCEM in modes:
            self.pt_screen.show_cursor = True

        # On "\e[?1049h", enter alternate screen mode.
        # Backup the current state,
        if (1049 << 5) in modes:
            self._original_screen = self.pt_screen
            self._original_screen_vars = dict(
                (v, getattr(self, v)) for v in self.swap_variables
            )
            self._reset_screen()
            self._reset_offset_and_margins()

    def reset_mode(self, *modes_args, **kwargs) -> None:
        """Resets (disables) a given list of modes.

        :param list modes: modes to reset -- hopefully, each mode is a
                           constant from :mod:`pyte.modes`.
        """
        modes = list(modes_args)

        # Private mode codes are shifted, to be distingiushed from non
        # private ones.
        if kwargs.get("private"):
            modes = [mode << 5 for mode in modes]

        self.mode.difference_update(modes)

        # Lines below follow the logic in :meth:`set_mode`.
        if mo.DECCOLM in modes:
            self.resize(columns=80)
            self.erase_in_display(2)
            self.cursor_position()

        if mo.DECOM in modes:
            self.cursor_position()

        # Hide the cursor.
        if mo.DECTCEM in modes:
            self.pt_screen.show_cursor = False

        # On "\e[?1049l", restore from alternate screen mode.
        if (1049 << 5) in modes and self._original_screen:
            for k, v in self._original_screen_vars.items():
                setattr(self, k, v)
            self.pt_screen = self._original_screen

            self._original_screen = None
            self._original_screen_vars = {}
            self._reset_offset_and_margins()

    @property
    def in_alternate_screen(self) -> bool:
        return bool(self._original_screen)

    def shift_in(self) -> None:
        " Activates ``G0`` character set. "
        self.charset = 0

    def shift_out(self) -> None:
        " Activates ``G1`` character set. "
        self.charset = 1

    def draw(self, chars: str) -> None:
        """
        Draw characters.
        `chars` is supposed to *not* contain any special characters.
        No newlines or control codes.
        """
        # Aliases for variables that are used more than once in this function.
        # Local lookups are always faster.
        # (This draw function is called for every printable character that a
        # process outputs; it should be as performant as possible.)
        pt_screen = self.pt_screen
        data_buffer = pt_screen.data_buffer
        cursor_position = self.pt_cursor_position
        cursor_position_x = cursor_position.x
        cursor_position_y = cursor_position.y

        in_irm = mo.IRM in self.mode
        char_cache = _CHAR_CACHE
        columns = self.columns

        # Translating a given character.
        if self.charset:
            chars = chars.translate(self.g1_charset)
        else:
            chars = chars.translate(self.g0_charset)

        style = self._style_str

        for char in chars:
            # Create 'Char' instance.
            pt_char = char_cache[char, style]
            char_width = pt_char.width

            # If this was the last column in a line and auto wrap mode is
            # enabled, move the cursor to the beginning of the next line,
            # otherwise replace characters already displayed with newly
            # entered.
            if cursor_position_x >= columns:
                if mo.DECAWM in self.mode:
                    self.carriage_return()
                    self.linefeed()
                    cursor_position = self.pt_cursor_position
                    cursor_position_x = cursor_position.x
                    cursor_position_y = cursor_position.y

                    self.wrapped_lines.append(cursor_position_y)
                else:
                    cursor_position_x -= max(0, char_width)

            # If Insert mode is set, new characters move old characters to
            # the right, otherwise terminal is in Replace mode and new
            # characters replace old characters at cursor position.
            if in_irm:
                self.insert_characters(max(0, char_width))

            row = data_buffer[cursor_position_y]
            if char_width == 1:
                row[cursor_position_x] = pt_char
            elif char_width > 1:  # 2
                # Double width character. Put an empty string in the second
                # cell, because this is different from every character and
                # causes the render engine to clear this character, when
                # overwritten.
                row[cursor_position_x] = pt_char
                row[cursor_position_x + 1] = char_cache["", style]
            elif char_width == 0:
                # This is probably a part of a decomposed unicode character.
                # Merge into the previous cell.
                # See: https://en.wikipedia.org/wiki/Unicode_equivalence
                prev_char = row[cursor_position_x - 1]
                row[cursor_position_x - 1] = char_cache[
                    prev_char.char + pt_char.char, prev_char.style
                ]
            else:  # char_width < 0
                # (Should not happen.)
                char_width = 0

            # .. note:: We can't use :meth:`cursor_forward()`, because that
            #           way, we'll never know when to linefeed.
            cursor_position_x += char_width

        # Update max_y. (Don't use 'max()' for comparing only two values, that
        # is less efficient.)
        if cursor_position_y > self.max_y:
            self.max_y = cursor_position_y

        cursor_position.x = cursor_position_x

    def carriage_return(self) -> None:
        " Move the cursor to the beginning of the current line. "
        self.pt_cursor_position.x = 0

    def index(self) -> None:
        """Move the cursor down one line in the same column. If the
        cursor is at the last line, create a new line at the bottom.
        """
        margins = self.margins

        # When scrolling over the full screen height -> keep history.
        if margins is None:
            # Simply move the cursor one position down.
            cursor_position = self.pt_cursor_position
            cursor_position.y += 1
            self.max_y = max(self.max_y, cursor_position.y)

            # Cleanup the history, but only every 100 calls.
            self._history_cleanup_counter += 1
            if self._history_cleanup_counter == 100:
                self._remove_old_lines_from_history()
                self._history_cleanup_counter = 0
        else:
            # Move cursor down, but scroll in the scrolling region.
            top, bottom = self.margins
            line_offset = self.line_offset

            if self.pt_cursor_position.y - line_offset == bottom:
                data_buffer = self.data_buffer

                for line in range(top, bottom):
                    data_buffer[line + line_offset] = data_buffer[
                        line + line_offset + 1
                    ]
                    data_buffer.pop(line + line_offset + 1, None)
            else:
                self.cursor_down()

    def _remove_old_lines_from_history(self) -> None:
        """
        Remove top from the scroll buffer. (Outside bounds of history limit.)
        """
        remove_above = max(0, self.pt_cursor_position.y - self.get_history_limit())
        data_buffer = self.pt_screen.data_buffer
        for line in list(data_buffer):
            if line < remove_above:
                data_buffer.pop(line, None)

    def clear_history(self) -> None:
        """
        Delete all history from the scroll buffer.
        """
        for line in list(self.data_buffer):
            if line < self.line_offset:
                self.data_buffer.pop(line, None)

    def reverse_index(self) -> None:
        margins = self.margins or Margins(0, self.lines - 1)
        top, bottom = margins
        line_offset = self.line_offset

        # When scrolling over the full screen -> keep history.
        if self.pt_cursor_position.y - line_offset == top:
            for i in range(bottom - 1, top - 1, -1):
                self.data_buffer[i + line_offset + 1] = self.data_buffer[
                    i + line_offset
                ]
                self.data_buffer.pop(i + line_offset, None)
        else:
            self.cursor_up()

    def linefeed(self) -> None:
        """Performs an index and, if :data:`~pyte.modes.LNM` is set, a
        carriage return.
        """
        self.index()

        if mo.LNM in self.mode:
            self.carriage_return()

    def next_line(self) -> None:
        """When `EscE` has been received. Go to the next line, even when LNM has
        not been set."""
        self.index()
        self.carriage_return()
        self.ensure_bounds()

    def tab(self) -> None:
        """Move to the next tab space, or the end of the screen if there
        aren't anymore left.
        """
        for stop in sorted(self.tabstops):
            if self.pt_cursor_position.x < stop:
                column = stop
                break
        else:
            column = self.columns - 1

        self.pt_cursor_position.x = column

    def backspace(self) -> None:
        """Move cursor to the left one or keep it in it's position if
        it's at the beginning of the line already.
        """
        self.cursor_back()

    def save_cursor(self) -> None:
        """Push the current cursor position onto the stack."""
        self.savepoints.append(
            _Savepoint(
                self.pt_cursor_position.x,
                self.pt_cursor_position.y,
                self.g0_charset,
                self.g1_charset,
                self.charset,
                mo.DECOM in self.mode,
                mo.DECAWM in self.mode,
                self._attrs,
                self._style_str,
            )
        )

    def restore_cursor(self) -> None:
        """Set the current cursor position to whatever cursor is on top
        of the stack.
        """
        if self.savepoints:
            savepoint = self.savepoints.pop()

            self.g0_charset = savepoint.g0_charset
            self.g1_charset = savepoint.g1_charset
            self.charset = savepoint.charset
            self._attrs = savepoint.attrs
            self._style_str = savepoint.style_str

            if savepoint.origin:
                self.set_mode(mo.DECOM)
            if savepoint.wrap:
                self.set_mode(mo.DECAWM)

            self.pt_cursor_position.x = savepoint.cursor_x
            self.pt_cursor_position.y = savepoint.cursor_y
            self.ensure_bounds(use_margins=True)
        else:
            # If nothing was saved, the cursor moves to home position;
            # origin mode is reset. :todo: DECAWM?
            self.reset_mode(mo.DECOM)
            self.cursor_position()

    def insert_lines(self, count: Optional[int] = None) -> None:
        """Inserts the indicated # of lines at line with cursor. Lines
        displayed **at** and below the cursor move down. Lines moved
        past the bottom margin are lost.

        :param count: number of lines to delete.
        """
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)

        data_buffer = self.data_buffer
        line_offset = self.line_offset
        pt_cursor_position = self.pt_cursor_position

        # If cursor is outside scrolling margins it -- do nothing.
        if top <= pt_cursor_position.y - self.line_offset <= bottom:
            for line in range(bottom, pt_cursor_position.y - line_offset, -1):
                if line - count < top:
                    data_buffer.pop(line + line_offset, None)
                else:
                    data_buffer[line + line_offset] = data_buffer[
                        line + line_offset - count
                    ]
                    data_buffer.pop(line + line_offset - count, None)

            self.carriage_return()

    def delete_lines(self, count: Optional[int] = None) -> None:
        """Deletes the indicated # of lines, starting at line with
        cursor. As lines are deleted, lines displayed below cursor
        move up. Lines added to bottom of screen have spaces with same
        character attributes as last line moved up.

        :param int count: number of lines to delete.
        """
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)
        line_offset = self.line_offset
        pt_cursor_position = self.pt_cursor_position

        # If cursor is outside scrolling margins it -- do nothin'.
        if top <= pt_cursor_position.y - line_offset <= bottom:
            data_buffer = self.data_buffer

            # Iterate from the cursor Y position until
            # the end of the visible input.
            for line in range(pt_cursor_position.y - line_offset, bottom + 1):
                # When 'x' lines further are out of the margins,
                # replace by an empty line,
                # Otherwise copy the line from there.
                if line + count > bottom:
                    data_buffer.pop(line + line_offset, None)
                else:
                    data_buffer[line + line_offset] = self.data_buffer[
                        line + count + line_offset
                    ]

    def insert_characters(self, count: Optional[int] = None) -> None:
        """Inserts the indicated # of blank characters at the cursor
        position. The cursor does not move and remains at the beginning
        of the inserted blank characters. Data on the line is shifted
        forward.

        :param int count: number of characters to insert.
        """
        count = count or 1

        line = self.data_buffer[self.pt_cursor_position.y]

        if line:
            max_columns = max(line.keys())

            for i in range(max_columns, self.pt_cursor_position.x - 1, -1):
                line[i + count] = line[i]
                del line[i]

    def delete_characters(self, count: Optional[int] = None) -> None:
        count = count or 1

        line = self.data_buffer[self.pt_cursor_position.y]
        if line:
            max_columns = max(line.keys())

            for i in range(self.pt_cursor_position.x, max_columns + 1):
                line[i] = line[i + count]
                del line[i + count]

    def cursor_position(
        self, line: Optional[int] = None, column: Optional[int] = None
    ) -> None:
        """Set the cursor to a specific `line` and `column`.

        Cursor is allowed to move out of the scrolling region only when
        :data:`~pyte.modes.DECOM` is reset, otherwise -- the position
        doesn't change.

        :param int line: line number to move the cursor to.
        :param int column: column number to move the cursor to.
        """
        column = (column or 1) - 1
        line = (line or 1) - 1

        # If origin mode (DECOM) is set, line number are relative to
        # the top scrolling margin.
        margins = self.margins

        if margins is not None and mo.DECOM in self.mode:
            line += margins.top

            # Cursor is not allowed to move out of the scrolling region.
            if not (margins.top <= line <= margins.bottom):
                return

        self.pt_cursor_position.x = column
        self.pt_cursor_position.y = line + self.line_offset
        self.ensure_bounds()

    def cursor_to_column(self, column: Optional[int] = None) -> None:
        """Moves cursor to a specific column in the current line.

        :param int column: column number to move the cursor to.
        """
        self.pt_cursor_position.x = (column or 1) - 1
        self.ensure_bounds()

    def cursor_to_line(self, line: Optional[int] = None) -> None:
        """Moves cursor to a specific line in the current column.

        :param int line: line number to move the cursor to.
        """
        self.pt_cursor_position.y = (line or 1) - 1 + self.line_offset

        # If origin mode (DECOM) is set, line number are relative to
        # the top scrolling margin.
        margins = self.margins

        if mo.DECOM in self.mode and margins is not None:
            self.pt_cursor_position.y += margins.top

            # FIXME: should we also restrict the cursor to the scrolling
            # region?

        self.ensure_bounds()

    def bell(self, *args) -> None:
        " Bell "
        self.bell_func()

    def cursor_down(self, count: Optional[int] = None) -> None:
        """Moves cursor down the indicated # of lines in same column.
        Cursor stops at bottom margin.

        :param int count: number of lines to skip.
        """
        cursor_position = self.pt_cursor_position
        margins = self.margins or Margins(0, self.lines - 1)

        # Ensure bounds.
        # (Following code is faster than calling `self.ensure_bounds`.)
        _, bottom = margins
        cursor_position.y = min(
            cursor_position.y + (count or 1), bottom + self.line_offset + 1
        )

        self.max_y = max(self.max_y, cursor_position.y)

    def cursor_down1(self, count: Optional[int] = None) -> None:
        """Moves cursor down the indicated # of lines to column 1.
        Cursor stops at bottom margin.

        :param int count: number of lines to skip.
        """
        self.cursor_down(count)
        self.carriage_return()

    def cursor_up(self, count: Optional[int] = None) -> None:
        """Moves cursor up the indicated # of lines in same column.
        Cursor stops at top margin.

        :param int count: number of lines to skip.
        """
        self.pt_cursor_position.y -= count or 1
        self.ensure_bounds(use_margins=True)

    def cursor_up1(self, count: Optional[int] = None) -> None:
        """Moves cursor up the indicated # of lines to column 1. Cursor
        stops at bottom margin.

        :param int count: number of lines to skip.
        """
        self.cursor_up(count)
        self.carriage_return()

    def cursor_back(self, count: Optional[int] = None) -> None:
        """Moves cursor left the indicated # of columns. Cursor stops
        at left margin.

        :param int count: number of columns to skip.
        """
        self.pt_cursor_position.x = max(0, self.pt_cursor_position.x - (count or 1))
        self.ensure_bounds()

    def cursor_forward(self, count: Optional[int] = None) -> None:
        """Moves cursor right the indicated # of columns. Cursor stops
        at right margin.

        :param int count: number of columns to skip.
        """
        self.pt_cursor_position.x += count or 1
        self.ensure_bounds()

    def erase_characters(self, count: Optional[int] = None) -> None:
        """Erases the indicated # of characters, starting with the
        character at cursor position. Character attributes are set
        cursor attributes. The cursor remains in the same position.

        :param int count: number of characters to erase.

        .. warning::

           Even though *ALL* of the VTXXX manuals state that character
           attributes **should be reset to defaults**, ``libvte``,
           ``xterm`` and ``ROTE`` completely ignore this. Same applies
           too all ``erase_*()`` and ``delete_*()`` methods.
        """
        count = count or 1
        cursor_position = self.pt_cursor_position
        row = self.data_buffer[cursor_position.y]

        for column in range(
            cursor_position.x, min(cursor_position.x + count, self.columns)
        ):
            row[column] = Char(style=row[column].style)

    def erase_in_line(self, type_of: int = 0, private: bool = False) -> None:
        """Erases a line in a specific way.

        :param int type_of: defines the way the line should be erased in:

            * ``0`` -- Erases from cursor to end of line, including cursor
              position.
            * ``1`` -- Erases from beginning of line to cursor,
              including cursor position.
            * ``2`` -- Erases complete line.
        :param bool private: when ``True`` character attributes aren left
                             unchanged **not implemented**.
        """
        data_buffer = self.data_buffer
        pt_cursor_position = self.pt_cursor_position

        if type_of == 2:
            # Delete line completely.
            data_buffer.pop(pt_cursor_position.y, None)
        else:
            line = data_buffer[pt_cursor_position.y]

            def should_we_delete(column):  # TODO: check for off-by-one errors!
                if type_of == 0:
                    return column >= pt_cursor_position.x
                if type_of == 1:
                    return column <= pt_cursor_position.x

            for column in list(line.keys()):
                if should_we_delete(column):
                    line.pop(column, None)

    def erase_in_display(self, type_of: int = 0, private: bool = False) -> None:
        """Erases display in a specific way.

        :param int type_of: defines the way the line should be erased in:

            * ``0`` -- Erases from cursor to end of screen, including
              cursor position.
            * ``1`` -- Erases from beginning of screen to cursor,
              including cursor position.
            * ``2`` -- Erases complete display. All lines are erased
              and changed to single-width. Cursor does not move.
            * ``3`` -- Erase saved lines. (Xterm) Clears the history.
        :param bool private: when ``True`` character attributes aren left
                             unchanged **not implemented**.
        """
        line_offset = self.line_offset
        pt_cursor_position = self.pt_cursor_position
        try:
            max_line = max(self.pt_screen.data_buffer)
        except ValueError:
            # max() called on empty sequence.
            # Screen is empty. Nothing to erase.
            return

        if type_of == 3:
            # Clear data buffer.
            for y in list(self.data_buffer):
                self.data_buffer.pop(y, None)

            # Reset line_offset.
            pt_cursor_position.y = 0
            self.max_y = 0
        else:
            try:
                interval = (
                    # a) erase from cursor to the end of the display, including
                    # the cursor,
                    range(pt_cursor_position.y + 1, max_line + 1),
                    # b) erase from the beginning of the display to the cursor,
                    # including it,
                    range(line_offset, pt_cursor_position.y),
                    # c) erase the whole display.
                    range(line_offset, max_line + 1),
                )[type_of]
            except IndexError:
                return

            data_buffer = self.data_buffer
            for line in interval:
                data_buffer[line] = defaultdict(lambda: Char(" "))

            # In case of 0 or 1 we have to erase the line with the cursor.
            if type_of in [0, 1]:
                self.erase_in_line(type_of)

    def set_tab_stop(self) -> None:
        " Set a horizontal tab stop at cursor position. "
        self.tabstops.add(self.pt_cursor_position.x)

    def clear_tab_stop(self, type_of: Optional[int] = None) -> None:
        """Clears a horizontal tab stop in a specific way, depending
        on the ``type_of`` value:
        * ``0`` or nothing -- Clears a horizontal tab stop at cursor
          position.
        * ``3`` -- Clears all horizontal tab stops.
        """
        if not type_of:
            # Clears a horizontal tab stop at cursor position, if it's
            # present, or silently fails if otherwise.
            self.tabstops.discard(self.pt_cursor_position.x)
        elif type_of == 3:
            self.tabstops = set()  # Clears all horizontal tab stops.

    def ensure_bounds(self, use_margins: Optional[bool] = None) -> None:
        """Ensure that current cursor position is within screen bounds.

        :param bool use_margins: when ``True`` or when
                                 :data:`~pyte.modes.DECOM` is set,
                                 cursor is bounded by top and and bottom
                                 margins, instead of ``[0; lines - 1]``.
        """
        margins = self.margins
        if margins and (use_margins or mo.DECOM in self.mode):
            top, bottom = margins
        else:
            top, bottom = 0, self.lines - 1

        cursor_position = self.pt_cursor_position
        line_offset = self.line_offset

        cursor_position.x = min(max(0, cursor_position.x), self.columns - 1)
        cursor_position.y = min(
            max(top + line_offset, cursor_position.y), bottom + line_offset + 1
        )

    def alignment_display(self) -> None:
        for y in range(0, self.lines):
            line = self.data_buffer[y + self.line_offset]
            for x in range(0, self.columns):
                line[x] = Char("E")

    # Mapping of the ANSI color codes to their names.
    _fg_colors = dict((v, "#" + k) for k, v in FG_ANSI_COLORS.items())
    _bg_colors = dict((v, "#" + k) for k, v in BG_ANSI_COLORS.items())

    # Mapping of the escape codes for 256colors to their '#ffffff' value.
    _256_colors = {}

    for i, (r, g, b) in enumerate(_256_colors_table.colors):
        _256_colors[1024 + i] = "#%02x%02x%02x" % (r, g, b)

    def select_graphic_rendition(self, *attrs_tuple: int) -> None:
        """ Support 256 colours """
        replace: Dict[str, object] = {}

        if not attrs_tuple:
            attrs = [0]
        else:
            attrs = list(attrs_tuple[::-1])

        while attrs:
            attr = attrs.pop()

            if attr in self._fg_colors:
                replace["color"] = self._fg_colors[attr]
            elif attr in self._bg_colors:
                replace["bgcolor"] = self._bg_colors[attr]
            elif attr == 1:
                replace["bold"] = True
            elif attr == 3:
                replace["italic"] = True
            elif attr == 4:
                replace["underline"] = True
            elif attr == 5:
                replace["blink"] = True
            elif attr == 6:
                replace["blink"] = True  # Fast blink.
            elif attr == 7:
                replace["reverse"] = True
            elif attr == 8:
                replace["hidden"] = True
            elif attr == 22:
                replace["bold"] = False
            elif attr == 23:
                replace["italic"] = False
            elif attr == 24:
                replace["underline"] = False
            elif attr == 25:
                replace["blink"] = False
            elif attr == 27:
                replace["reverse"] = False
            elif not attr:
                replace = {}
                self._attrs = Attrs(
                    color=None,
                    bgcolor=None,
                    bold=False,
                    underline=False,
                    italic=False,
                    blink=False,
                    reverse=False,
                    hidden=False,
                )

            elif attr in (38, 48):
                n = attrs.pop()

                # 256 colors.
                if n == 5:
                    if attr == 38:
                        m = attrs.pop()
                        replace["color"] = self._256_colors.get(1024 + m)
                    elif attr == 48:
                        m = attrs.pop()
                        replace["bgcolor"] = self._256_colors.get(1024 + m)

                # True colors.
                if n == 2:
                    try:
                        color_str = "#%02x%02x%02x" % (
                            attrs.pop(),
                            attrs.pop(),
                            attrs.pop(),
                        )
                    except IndexError:
                        pass
                    else:
                        if attr == 38:
                            replace["color"] = color_str
                        elif attr == 48:
                            replace["bgcolor"] = color_str

        attrs_obj = self._attrs._replace(**replace)  # type:ignore

        # Build style string.
        style_str = ""
        if attrs_obj.color:
            style_str += "%s " % attrs_obj.color
        if attrs_obj.bgcolor:
            style_str += "bg:%s " % attrs_obj.bgcolor
        if attrs_obj.bold:
            style_str += "bold "
        if attrs_obj.italic:
            style_str += "italic "
        if attrs_obj.underline:
            style_str += "underline "
        if attrs_obj.blink:
            style_str += "blink "
        if attrs_obj.reverse:
            style_str += "reverse "
        if attrs_obj.hidden:
            style_str += "hidden "

        self._style_str = _unicode_intern_dict[style_str]
        self._attrs = attrs_obj

    def report_device_status(self, data: int) -> None:
        """
        Report cursor position.
        """
        if data == 6:
            y = self.pt_cursor_position.y - self.line_offset + 1
            x = self.pt_cursor_position.x + 1

            response = "\x1b[%i;%iR" % (y, x)
            self.write_process_input(response)

    def report_device_attributes(self, *args, **kwargs) -> None:
        response = "\x1b[>84;0;0c"
        self.write_process_input(response)

    def set_icon_name(self, param: str) -> None:
        self.icon_name = param

    def set_title(self, param: str) -> None:
        self.title = param

    def define_charset(self, *a, **kw):
        pass

    def charset_default(self, *a, **kw):
        " Not implemented. "

    def charset_utf8(self, *a, **kw):
        " Not implemented. "

    def debug(self, *args, **kwargs):
        pass

    def _reflow(self) -> None:
        """
        Reflow the screen using the given width.
        """
        width = self.columns

        data_buffer = self.pt_screen.data_buffer
        new_data_buffer = Screen(default_char=Char(" ", "")).data_buffer
        cursor_position = self.pt_cursor_position
        cy, cx = (cursor_position.y, cursor_position.x)

        cursor_character = data_buffer[cursor_position.y][cursor_position.x].char

        # Ensure that the cursor position is present.
        # (and avoid calling min() on empty collection.)
        data_buffer[cursor_position.y][cursor_position.y]

        # Unwrap all the lines.
        offset = min(data_buffer)
        line: List[Char] = []
        all_lines: List[List[Char]] = [line]

        for row_index in range(min(data_buffer), max(data_buffer) + 1):
            row = data_buffer[row_index]

            row[0]  # Avoid calling max() on empty collection.
            for column_index in range(0, max(row) + 1):
                if cy == row_index and cx == column_index:
                    cy = len(all_lines) - 1
                    cx = len(line)

                line.append(row[column_index])

            # Create new line if the next line was not a wrapped line.
            if row_index + 1 not in self.wrapped_lines:
                line = []
                all_lines.append(line)

        # Remove trailing whitespace (unless it contains the cursor).
        # Also make sure that lines consist of at lesat one character,
        # otherwise we can't calculate `max_y` correctly. (This is important
        # for the `clear` command.)
        for row_index, line in enumerate(all_lines):
            # We do this only if no special styling given.
            while len(line) > 1 and line[-1].char.isspace() and not line[-1].style:
                if row_index == cy and len(line) - 1 == cx:
                    break
                line.pop()

        # Wrap lines again according to the screen width.
        new_row_index = offset
        new_column_index = 0
        new_wrapped_lines = []

        for row_index, line in enumerate(all_lines):
            for column_index, char in enumerate(line):
                # Check for space on the current line.
                if new_column_index + char.width > width:
                    new_row_index += 1
                    new_column_index = 0
                    new_wrapped_lines.append(new_row_index)

                if cy == row_index and cx == column_index:
                    cy = new_row_index
                    cx = new_column_index

                # Add character to new buffer.
                new_data_buffer[new_row_index][new_column_index] = char
                new_column_index += char.width

            new_row_index += 1
            new_column_index = 0

        # TODO: when the window gets smaller, and the cursor
        # is at the top of the screen, remove lines at the bottom.
        for row_index in range(min(data_buffer), max(data_buffer) + 1):
            if row_index > cy + self.lines:
                del data_buffer[row_index]

        self.pt_screen.data_buffer = new_data_buffer
        self.data_buffer = new_data_buffer
        self.wrapped_lines = new_wrapped_lines

        cursor_position.y, cursor_position.x = cy, cx
        self.pt_cursor_position = cursor_position

        # If everything goes well, the cursor should
        # still be on the same character.
        if (
            cursor_character
            != new_data_buffer[cursor_position.y][cursor_position.x].char
        ):
            # FIXME:
            raise Exception(
                "Reflow failed: %r %r"
                % (
                    cursor_character,
                    new_data_buffer[cursor_position.y][cursor_position.x].char,
                )
            )

        self.max_y = max(self.data_buffer)

        self.max_y = min(self.max_y, cursor_position.y + self.lines - 1)
