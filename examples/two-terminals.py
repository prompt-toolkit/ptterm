#!/usr/bin/env python
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style
from ptterm import Terminal


def main():
    style = Style(
        [
            ("terminal not-focused", "#888888"),
            ("title", "bg:#000044 #ffffff underline"),
        ]
    )

    done_count = [0]  # nonlocal.

    def done():
        done_count[0] += 1
        if done_count[0] == 2:
            application.exit()
        else:
            switch_focus()

    term1 = Terminal(
        width=D(preferred=80),
        height=D(preferred=40),
        style="class:terminal",
        done_callback=done,
    )

    term2 = Terminal(
        width=D(preferred=80),
        height=D(preferred=40),
        style="class:terminal",
        done_callback=done,
    )

    kb = KeyBindings()

    @kb.add("c-w")
    def _(event):
        switch_focus()

    def switch_focus():
        " Change focus when Control-W is pressed."
        if application.layout.has_focus(term1):
            application.layout.focus(term2)
        else:
            application.layout.focus(term1)

    application = Application(
        layout=Layout(
            container=HSplit(
                [
                    Window(
                        height=1,
                        style="class:title",
                        content=FormattedTextControl(
                            HTML(
                                ' Press <u fg="#ff8888"><b>Control-W</b></u> to <b>switch focus</b>.'
                            )
                        ),
                    ),
                    VSplit([term1, Window(style="bg:#aaaaff", width=1), term2,]),
                ]
            ),
            focused_element=term1,
        ),
        style=style,
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
    )
    application.run()


if __name__ == "__main__":
    main()
