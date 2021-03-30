# This file is part of ts_hexrotcomm.
#
# Developed for the Rubin Observatory Telescope and Site System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__all__ = ["OneClientServer"]

import asyncio

from . import utils


class OneClientServer:
    """A TCP/IP socket server that serves a single client.

    If additional clients try to connect they are rejected
    (the socket writer is closed).

    Parameters
    ----------
    name : `str`
        Name used for error messages. Typically "Commands" or "Telemetry".
    host : `str` or `None`
        IP address for this server.
        If `None` then bind to all network interfaces.
    port : `int`
        IP port for this server. If 0 then use a random port.
    log : `logging.Logger`
        Logger.
    connect_callback : callable or `None`
        Synchronous function to call when a connection is made or dropped.
        It receives one argument: this `OneClientServer`.
    """

    def __init__(self, name, host, port, log, connect_callback):
        self.name = name
        self.host = host
        self.port = port
        self.log = log.getChild(f"OneClientServer({name})")
        self.connect_callback = connect_callback
        # Was the client connected last time `call_connected_callback`
        # called? Used to prevent multiple calls to ``connect_callback``
        # for the same connected state.
        self._last_connected = False

        # TCP/IP socket server, or None until start_task is done.
        self.server = None
        # Client socket writer, or None if a client not connected.
        self.writer = None
        # Client socket reader, or None if a client not connected.
        self.reader = None
        # Task that is set done when a client connects to this server.
        self.connected_task = asyncio.Future()
        # Task that is set done when the TCP/IP server is started.
        self.start_task = asyncio.create_task(self.start())
        # Task that is set done when the TCP/IP server is closed,
        # making this object unusable.
        self.done_task = asyncio.Future()

    @property
    def connected(self):
        """Return True if a client is connected to this socket."""
        return not (
            self.writer is None or self.writer.is_closing() or self.reader.at_eof()
        )

    async def set_reader_writer(self, reader, writer):
        """Set self.reader and self.writer.

        Called when a client connects to this server.

        Parameters
        ----------
        reader : `asyncio.SocketReader`
            Socket reader.
        writer : `asyncio.SocketWriter`
            Socket writer.
        """
        if self.connected:
            self.log.error("Rejecting connection; a socket is already connected.")
            await utils.close_stream_writer(writer)
            return
        self.reader = reader
        self.writer = writer
        if not self.connected_task.done():
            self.connected_task.set_result(None)
        self.call_connect_callback()

    async def start(self):
        """Start TCP/IP server."""
        if self.server is not None:
            raise RuntimeError("Cannot call start more than once.")
        self.log.debug("Starting server")
        self.server = await asyncio.start_server(
            self.set_reader_writer, host=self.host, port=self.port
        )
        if self.port == 0:
            self.port = self.server.sockets[0].getsockname()[1]
        self.log.info(f"Server running: host={self.host}; port={self.port}")

    def call_connect_callback(self):
        """Call self.connect_callback if the connection state has changed."""
        connected = self.connected
        self.log.debug(
            f"call_connect_callback: connected={connected}; "
            f"last_connected={self._last_connected}"
        )
        if self._last_connected != connected:
            if self.connect_callback is not None:
                try:
                    self.log.info("Calling connect_callback")
                    self.connect_callback(self)
                except Exception:
                    self.log.exception("connect_callback failed.")
            self._last_connected = connected

    async def close_client(self):
        """Close the connected client socket, if any."""
        try:
            self.log.info("Closing the client socket.")
            if self.writer is None:
                return

            writer = self.writer
            self.writer = None
            await utils.close_stream_writer(writer)
            self.connected_task = asyncio.Future()
        except Exception:
            self.log.exception("close_client failed; continuing")
        finally:
            self.call_connect_callback()

    async def close(self):
        """Close socket server and client socket and set the done_task done.

        Always safe to call.
        """
        try:
            self.log.info("Closing the server.")
            if self.server is not None:
                self.server.close()
            await self.close_client()
        except Exception:
            self.log.exception("close failed; continuing")
        finally:
            if self.done_task.done():
                self.done_task.set_result(None)
