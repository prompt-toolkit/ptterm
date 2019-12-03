#!/usr/bin/env python
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from ptterm import Terminal


def main():
    style = Style(
        [("terminal focused", "bg:#aaaaaa"), ("title", "bg:#000044 #ffffff underline"),]
    )

    term1 = Terminal()

    text_area = TextArea(
        text="Press Control-W to switch focus.\n"
        "Then you can edit this text area.\n"
        "Press Control-X to exit"
    )

    kb = KeyBindings()

    @kb.add("c-w")
    def _(event):
        switch_focus()

    @kb.add("c-x", eager=True)
    def _(event):
        event.app.exit()

    def switch_focus():
        " Change focus when Control-W is pressed."
        if application.layout.has_focus(term1):
            application.layout.focus(text_area)
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
                            " Press Control-W to switch focus."
                        ),
                    ),
                    VSplit([term1, Window(style="bg:#aaaaff", width=1), text_area,]),
                ]
            ),
            focused_element=term1,
        ),
        style=style,
        key_bindings=merge_key_bindings([load_key_bindings(), kb,]),
        full_screen=True,
        mouse_support=True,
    )
    application.run()


if __name__ == "__main__":
    main()
