#!/usr/bin/env python
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout
from ptterm import Terminal


def main():
    def done():
        application.exit()

    application = Application(
        layout=Layout(container=Terminal(done_callback=done)), full_screen=True,
    )
    application.run()


if __name__ == "__main__":
    main()
