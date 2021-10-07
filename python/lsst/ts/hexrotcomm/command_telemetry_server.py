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

__all__ = ["CommandTelemetryServer"]

import abc
import asyncio
import math

from lsst.ts import utils
from lsst.ts import tcpip
from . import structs


class CommandTelemetryServer(abc.ABC):
    """TCP/IP server for a mock Moog low-level controller.

    Moog low-level controllers use two TCP/IP server sockets:
    one to read commands and the other to write telemetry.
    Both sockets must be connected for the mock controller to operate;
    if either becomes disconnected the controller will stop moving,
    close any open client sockets, and wait for the CSC to reconnect.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    port : `int`
        Port for telemetry and configuration;
        if nonzero then the command port will be one larger.
        Specify 0 to choose random values for both ports; this is recommended
        for unit tests, to avoid collision with other unit tests.
        Do not specify 0 with host=None (see Raises section).
    config : `ctypes.Structure`
        Configuration data. May be modified.
    telemetry : `ctypes.Structure`
        Telemetry data. Modified by `update_telemetry`.
    host : `str` or `None`, optional
        IP address for this server. Typically "127.0.0.1" for an IPV4 server
        and "::" for an IPV6 server. If `None` then bind to all network
        interfaces and probably run both IPV4 and IPV6 servers.
        Do not specify `None` with port=0 (see Raises section).

    Attributes
    ----------
    log : `logging.Logger`
        A child of the ``log`` constructor argument.
    start_task : `asyncio.Task`
        A task for the `start` method, which starts automatically
        when this class is instantiated.
        Set done when both servers are successfully running.
    done_task : `asyncio.Future`
        A future that is set done when `close` finishes.
    headers : `dict` [`int`: ``Header``]
        A dict of frame ID: the most recently sent header for that frame ID
        (message type).
    config : ``ConfigClass``
        The most recently read configuration.
        Starts out as the ``config`` constructor argument.
    telemetry : ``TelemetryClass``
        The most recently read telemetry.
        Starts out as the ``telemetry`` constructor argument.
    command_server : `lsst.ts.tcpip.OneClientServer`
        TCP/IP server for the command stream.
    telemetry_server : `lsst.ts.tcpip.OneClientServer`
        TCP/IP server for the telemetry stream.
    command_loop_task : `asyncio.Future`
        Task for `command_loop`.
        Not meant for public use, except possibly in unit tests.
    telemetry_loop_task : `asyncio.Future`
        Task for `telemetry_loop`.
        Not meant for public use, except possibly in unit tests.

    Raises
    ------
    ValueError
        If host=None and port=0. This is because host=None runs IPV4 and IPV6
        servers, and port=0 will assign two different random ports for each
        stream (command and telemetry), one for IPV4, the other for IPV6.
        This, in turn, makes it impossible to determine the command_port and
        telemetry_port properties. It is possible to support this use case,
        but it's more trouble than it's worth.

    Notes
    -----
    Designed to be the parent class for `BaseMockController`.
    """

    # Interval between telemetry messages (seconds)
    telemetry_interval = 0.1

    def __init__(
        self,
        log,
        port,
        config,
        telemetry,
        host=tcpip.LOCAL_HOST,
    ):
        if host is None and port == 0:
            raise ValueError(
                "You may not specify host=None and port=0; that makes it impossible "
                "to determine the command_port and telemetry_port properties."
            )
        self.log = log.getChild("CommandTelemetryServer")
        self.header = structs.Header()
        self.config = config
        self.telemetry = telemetry
        # A dictionary of frame ID: header for telemetry and config data
        # Keeping separate headers for telemetry and config allows
        # updating just the relevant fields, rather than creating a new
        # header insteance for each telemetry and config message.
        self.headers = dict()
        for frame_id in (self.config.FRAME_ID, self.telemetry.FRAME_ID):
            header = structs.Header()
            header.frame_id = frame_id
            self.headers[frame_id] = header

        self.command_loop_task = utils.make_done_future()
        self.telemetry_loop_task = utils.make_done_future()
        self.start_task = asyncio.create_task(self.start())
        self.done_task = asyncio.Future()
        self._monitor_telemetry_reader_task = utils.make_done_future()
        self._basic_close_client_task = utils.make_done_future()

        self.command_server = tcpip.OneClientServer(
            name="Command",
            host=host,
            port=0 if port == 0 else port + 1,
            log=log,
            connect_callback=self.command_connect_callback,
        )
        self.telemetry_server = tcpip.OneClientServer(
            name="Telemetry",
            host=host,
            port=port,
            log=log,
            connect_callback=self.telemetry_connect_callback,
        )

    @property
    def connected(self):
        """Return True if command and telemetry sockets are connected."""
        return self.command_connected and self.telemetry_connected

    @property
    def command_connected(self):
        """Return True if the command socket is connected."""
        return self.command_server.connected

    @property
    def telemetry_connected(self):
        """Return True if the telemetry socket is connected."""
        return self.telemetry_server.connected

    @property
    def command_port(self):
        """Return the command port.

        May be 0 if the server has not started, or if it is serving
        both IPV4 and IPV6 sockets (e.g. with host=None).
        """
        return self.command_server.port

    @property
    def telemetry_port(self):
        """Return the telemetry port; may be 0 if not started.

        May be 0 if the server has not started, or if it is serving
        both IPV4 and IPV6 sockets (e.g. with host=None).
        """
        return self.telemetry_server.port

    def command_connect_callback(self, command_server):
        """Called when the command server connection state changes."""
        self.command_loop_task.cancel()
        if self.command_connected:
            self.command_loop_task = asyncio.create_task(self.command_loop())

    def telemetry_connect_callback(self, telemetry_server):
        """Called when the telemetry server connection state changes."""
        self.telemetry_loop_task.cancel()
        self._monitor_telemetry_reader_task.cancel()
        if self.telemetry_connected:
            self.telemetry_loop_task = asyncio.create_task(self.telemetry_loop())
            self._monitor_telemetry_reader_task = asyncio.create_task(
                self.monitor_telemetry_reader()
            )

    async def wait_connected(self):
        """Wait for command and telemetry sockets to be connected."""
        return asyncio.gather(
            self.command_server.connected_task, self.telemetry_server.connected_task
        )

    async def start(self):
        """Start command and telemetry TCP/IP servers.

        Raises
        ------
        RuntimeError
            If called more than once.
        Exceptions raised by asyncio.start_server
            Unfortunately, I have not found documentation for those.
        """
        if self.start_task.done():
            raise RuntimeError("Cannot call start more than once.")
        await asyncio.gather(
            self.command_server.start_task, self.telemetry_server.start_task
        )
        self.log.info(
            "CommandTelemetryServer started; "
            f"telemetry_port={self.telemetry_port}; "
            f"command_port={self.command_port}"
        )

    async def command_loop(self):
        """Read and execute commands."""
        self.log.info("command_loop begins")
        while self.command_server.connected:
            try:
                command = structs.Command()
                await tcpip.read_into(self.command_server.reader, command)
                await self.run_command(command)
            except asyncio.CancelledError:
                raise
            except ConnectionError:
                self.log.error("Command socket closed")
                asyncio.create_task(self.close_client())
            except Exception:
                self.log.exception("command_loop failed")
                asyncio.create_task(self.close_client())

    async def telemetry_loop(self):
        """Write configuration once, then telemetry at regular intervals."""
        self.log.info("telemetry_loop begins")
        try:
            if self.telemetry_connected:
                await self.write_config()
            while self.telemetry_connected:
                header, curr_tai = self.update_and_get_header(self.telemetry.FRAME_ID)
                await self.update_telemetry(curr_tai=curr_tai)
                await tcpip.write_from(
                    self.telemetry_server.writer, header, self.telemetry
                )
                await asyncio.sleep(self.telemetry_interval)
            self.log.info("Telemetry socket disconnected")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.exception("telemetry_loop failed")

    async def monitor_telemetry_reader(self):
        """Monitor the telemetry reader; if it closes then close the writer."""
        # We do not expect to read any data, but we may as well accept it
        # if some comes in.
        try:
            while self.telemetry_server.connected:
                await self.telemetry_server.reader.read(1000)
                if not self.telemetry_server.connected:
                    self.log.debug(
                        "monitor_telemetry_reader: reader disconnected; closing client sockets"
                    )
                    asyncio.create_task(self.close_client())
                    return
                if self.telemetry_server.reader.at_eof():
                    self.log.info("Telemetry reader at eof; closing client sockets")
                    asyncio.create_task(self.close_client())
                    return
                else:
                    self.log.warning(
                        "Unexpected data read from the telemetry socket; continuing to monitor."
                    )
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.log.debug("monitor_telemetry_reader cancelled")
        except ConnectionError:
            self.log.info("Telemetry reader disconnected; closing client sockets")
            asyncio.create_task(self.close_client())
        except Exception:
            self.log.exception("monitor_telemetry_reader failed")
            asyncio.create_task(self.close_client())

    def update_and_get_header(self, frame_id):
        """Update the config or telemetry header and return it and the time.

        Call this prior to writing telemetry or configuration.

        Parameters
        ----------
        frame_id : `int`
            Frame ID of header to write.

        Returns
        -------
        header : `structs.Header`
            The header.
        curr_tai : `float`
            Current time in header timestamp (TAI, unix seconds).
        """
        header = self.headers[frame_id]
        curr_tai = utils.current_tai()
        tai_frac, tai_sec = math.modf(curr_tai)
        header.tai_sec = int(tai_sec)
        header.tai_nsec = int(tai_frac * 1e9)
        return header, curr_tai

    async def write_config(self):
        """Write the current configuration."""
        assert self.telemetry_server.writer is not None
        header, curr_tai = self.update_and_get_header(self.config.FRAME_ID)
        await tcpip.write_from(self.telemetry_server.writer, header, self.config)

    async def close_client(self):
        """Close the client sockets."""
        if self._basic_close_client_task.done():
            self.log.debug("close_client: call _basic_close_client")
            self._basic_close_client_task = asyncio.create_task(
                self._basic_close_client()
            )
            await self._basic_close_client_task
        else:
            self.log.debug(
                "close_client: _basic_close_client already running; wait for it to finish"
            )
            await self._basic_close_client_task

    async def _basic_close_client(self):
        try:
            self.command_loop_task.cancel()
            self.telemetry_loop_task.cancel()
            self._monitor_telemetry_reader_task.cancel()
            await self.command_server.close_client()
            await self.telemetry_server.close_client()
        except asyncio.CancelledError:
            self.log.warning("_basic_close_client cancelled.")
        except Exception:
            self.log.exception("_basic_close_client failed.")

    async def close(self):
        """Close everything."""
        self.log.debug("close")
        try:
            await self.close_client()
            await self.command_server.close()
            await self.telemetry_server.close()
        except Exception:
            self.log.exception("close failed; setting done_task done anyway.")
        finally:
            if not self.done_task.done():
                self.done_task.set_result(None)

    @abc.abstractmethod
    async def run_command(self, command):
        """Run a command.

        Parameters
        ----------
        command : `Command`
            Command to run.
        """
        raise NotImplementedError()
