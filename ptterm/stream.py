"""
Improvements on Pyte.
"""
from pyte.escape import NEL
from pyte.streams import Stream

__all__ = ("BetterStream",)


class BetterStream(Stream):
    """
    Extension to the Pyte `Stream` class that also handles "Esc]<num>...BEL"
    sequences. This is used by xterm to set the terminal title.
    """

    escape = Stream.escape.copy()
    escape.update(
        {
            # Call next_line instead of line_feed. We always want to go to the left
            # margin if we receive this, unlike \n, which goes one row down.
            # (Except when LNM has been set.)
            NEL: "next_line",
        }
    )

    def __init__(self, screen) -> None:
        super().__init__()
        self.listener = screen
        self._validate_screen()

    def _validate_screen(self) -> None:
        """
        Check whether our Screen class has all the required callbacks.
        (We want to verify this statically, before feeding content to the
        screen.)
        """
        for d in [self.basic, self.escape, self.sharp, self.csi]:
            for name in d.values():
                assert hasattr(self.listener, name), "Screen is missing %r" % name

        for d in ("define_charset", "set_icon_name", "set_title", "draw", "debug"):
            assert hasattr(self.listener, name), "Screen is missing %r" % name
