#!/usr/bin/env python
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.widgets import Dialog
from ptterm import Terminal


def main():
    def done():
        application.exit()

    term = Terminal(width=D(preferred=60), height=D(preferred=25), done_callback=done)

    application = Application(
        layout=Layout(
            container=Dialog(title="Terminal demo", body=term, with_background=True),
            focused_element=term,
        ),
        full_screen=True,
        mouse_support=True,
    )
    application.run()


if __name__ == "__main__":
    main()
