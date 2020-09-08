#!/usr/bin/env python
import asyncio

import asyncssh

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style
from ptterm import Terminal
from ptterm.backends.asyncssh import AsyncSSHBackend


async def main():
    style = Style(
        [
            ("title", "bg:#000044 #ffffff underline"),
        ]
    )

    async with asyncssh.connect(
        "localhost", port=2222, username="jonathan",
    ) as client_connection:
        backend = AsyncSSHBackend(client_connection)

        kb = KeyBindings()

        @kb.add("c-x")
        def _(event):
            backend.kill()

        def done():
            get_app().exit()

        term = Terminal(
            backend=backend,
            style="class:terminal",
            done_callback=done,
        )

        application = Application(
            layout=Layout(
                container=HSplit(
                    [
                        Window(
                            height=1,
                            style="class:title",
                            content=FormattedTextControl(
                                HTML(
                                    ' AsyncSSH: Press <u fg="#ff8888"><b>Control-X</b></u> to <b>exit</b>.'
                                )
                            ),
                        ),
                        term,
                    ]
                ),
                focused_element=term,
            ),
            style=style,
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
        )
        await application.run_async()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
