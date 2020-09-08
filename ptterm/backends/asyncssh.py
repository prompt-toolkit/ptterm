from asyncio import Future, get_event_loop
from typing import Callable, List, Optional

from asyncssh import SSHClientChannel, SSHClientConnection, SSHClientSession

from .base import Backend

__all__ = ["AsyncSSHBackend"]


class AsyncSSHBackend(Backend):
    """
    Display asyncssh client session.
    """

    def __init__(
        self,
        ssh_client_connection: "SSHClientConnection",
        command: Optional[str] = None,
    ) -> None:
        self.ssh_client_connection = ssh_client_connection
        self.command = command

        self._channel: Optional[SSHClientChannel] = None
        self._session: Optional[SSHClientSession] = None

        self._reader_connected = False
        self._input_ready_callbacks: List[Callable[[], None]] = []
        self._receive_buffer: List[str] = []
        self.ready_f: Future[None] = Future()

        self.loop = get_event_loop()

    def start(self) -> None:
        class Session(SSHClientSession):
            def connection_made(_, chan):
                pass

            def connection_lost(_, exc):
                self.ready_f.set_result(None)

            def session_started(_):
                pass

            def data_received(_, data, datatype):
                send_signal = len(self._receive_buffer) == 0
                self._receive_buffer.append(data)

                if send_signal:
                    for cb in self._input_ready_callbacks:
                        cb()

            def exit_signal_received(self, signal, core_dumped, msg, lang):
                pass

        async def run() -> None:
            (
                self._channel,
                self._session,
            ) = await self.ssh_client_connection.create_session(
                session_factory=lambda: Session(),
                command=self.command,
                request_pty=True,
                term_type="xterm",
                term_size=(24, 80),
                encoding="utf-8",
            )

        self.loop.create_task(run())

    def add_input_ready_callback(self, callback: Callable[[], None]) -> None:
        if not self._reader_connected:
            self._input_ready_callbacks.append(callback)

    def connect_reader(self) -> None:
        if self._channel:
            self._channel.resume_reading()

    @property
    def closed(self) -> bool:
        return False  # TODO
        # return self._reader.closed

    def disconnect_reader(self) -> None:
        if self._channel is not None and self._reader_connected:
            self._channel.pause_reading()
            self._reader_connected = False

    def read_text(self, amount: int = 4096) -> str:
        result = "".join(self._receive_buffer)
        self._receive_buffer = []
        return result

    def write_text(self, text: str) -> None:
        if self._channel:
            try:
                self._channel.write(text)
            except BrokenPipeError:
                return

    def write_bytes(self, data: bytes) -> None:
        raise NotImplementedError

    def set_size(self, width: int, height: int) -> None:
        """
        Set terminal size.
        """
        if self._channel:
            self._channel.change_terminal_size(width, height)

    def kill(self) -> None:
        " Terminate process. "
        if self._channel:
            # self._channel.kill()
            self._channel.terminate()

    def send_signal(self, signal: int) -> None:
        " Send signal to running process. "
        if self._channel:
            self._channel.send_signal(signal)

    def get_name(self) -> str:
        " Return the process name. "
        if self._channel:
            command = self._channel.get_command()
            return f"asyncssh: {command}"
        return ""

    def get_cwd(self) -> Optional[str]:
        if self._channel:
            return self._channel.getcwd()
        return None
