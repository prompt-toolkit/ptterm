ptterm
======

*A terminal emulator widget for prompt_toolkit applications*

Features
--------

- Cross platform: Windows + Linux support.


Applications using ptterm
-------------------------

- `pymux <https://github.com/jonathanslenders/pymux>`_: A terminal multiplexer,
  written in Python.


Example
-------

Inserting the terminal into a prompt_toolkit application is as easy as
importing a `Terminal` and inserting it into the layout. You can pass a
`done_callback` to get notified when the terminal process is done.


.. code:: python

    #!/usr/bin/env python
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout import Layout
    from ptterm import Terminal


    def main():
        def done():
            application.exit()

        application = Application(
            layout=Layout(
                container=Terminal(done_callback=done)
            ),
            full_screen=True,
        )
        application.run()


    if __name__ == '__main__':
        main()


Thanks
------

- Thanks to `pyte <https://github.com/selectel/pyte>`_: for implementing a
  vt100 emulator.
- Thanks to `winpty <https://github.com/rprichard/winpty`_: for providing a PTY
  like interface for the Windows console.
- Thank to `yawinpty` for creating a Winpty wrapper.
