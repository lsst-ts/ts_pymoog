# This file is part of ts_hexrotcomm.
#
# Developed for the LSST Data Management System.
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

import asyncio

from . import constants
from . import structs
from . import utils
from . import one_client_server


class CommandTelemetryServer:
    """Serve command and telemetry ports for a low level controller
    to connect to.

    Parameters
    ----------
    host : `str`
        IP address for this server.
    log : `logging.Logger`
        Logger.
    ConfigClass : `ctypes.Structure`
        Class for configuration.
    TelemetryClass : `ctypes.Structure`
        Class for telemetry
    connect_callback : callable
        Function to call when a connection is made or dropped.
        The function receives one argument: this server.
    config_callback : callable
        Function to call when configuration is read.
        The function receives one argument: this server.
    telemetry_callback : callable
        Function to call when telemetry is read.
        The function receives one argument: this server.
    use_random_ports : `bool` (optional)
        If True then use random free ports for command and telemetry,
        else use the standard ports. Use True for unit tests
        to allow running multiple tests in parallel.
    """
    connect_timeout = 10
    """Time limit for Moog controller to connect to this server (sec)."""

    disconnect_timeout = 5
    """Time limit to close the server and telemetry sockets (sec)."""

    def __init__(self, host, log, ConfigClass, TelemetryClass,
                 connect_callback, config_callback, telemetry_callback,
                 use_random_ports=False):
        self.host = host
        self.log = log.getChild("CommandTelemetryServer")
        self.header = structs.Header()
        self.config = ConfigClass()
        self.telemetry = TelemetryClass()
        self.connect_callback = connect_callback
        self.config_callback = config_callback
        self.telemetry_callback = telemetry_callback
        self.use_random_ports = use_random_ports
        self.command_server = one_client_server.OneClientServer(
            name="Command",
            host=host,
            port=0 if self.use_random_ports else constants.COMMAND_PORT,
            log=log,
            connect_callback=self.command_connect_callback)
        self.telemetry_server = one_client_server.OneClientServer(
            name="Telemetry",
            host=host,
            port=0 if self.use_random_ports else constants.TELEMETRY_PORT,
            log=log,
            connect_callback=self.telemetry_connect_callback)

        self.read_telemetry_and_config_task = asyncio.Future()
        self.read_telemetry_and_config_task.set_result(None)
        self.monitor_command_reader_task = asyncio.Future()
        self.monitor_command_reader_task.set_result(None)
        self._telemetry_task = asyncio.Future()
        self.start_task = asyncio.create_task(self.start())
        self.done_task = asyncio.Future()
        # Call the connect callback shortly after construction.
        # That allows an assignment such as this:
        # ``server = CommandTelemetryServer(connect_callback=afunc)``
        # to finish (``server`` is created) before ``afunc`` is called.
        asyncio.create_task(self.async_call_connect_callback())

    @property
    def connected(self):
        """Return True if command and telemetry sockets are connected.
        """
        return self.command_connected and self.telemetry_connected

    @property
    def command_connected(self):
        """Return True if the command socket is connected.
        """
        return self.command_server.connected

    @property
    def telemetry_connected(self):
        """Return True if the telemetry socket is connected.
        """
        return self.telemetry_server.connected

    @property
    def command_port(self):
        """Return the command port; may be 0 if not started."""
        return self.command_server.port

    @property
    def telemetry_port(self):
        """Return the telemetry port; may be 0 if not started."""
        return self.telemetry_server.port

    async def next_telemetry(self, skip=2):
        """Wait for next telemetry.

        Parameters
        ----------
        skip : `int` (optional)
            Number of telemetry items to skip.
            1 is ideal to wait for the result of a command,
            because it avoids a race condition between sending
            the command and seeing the result.
        """
        if skip < 0:
            raise ValueError(f"skip={skip} must be >= 0")
        for n in range(skip):
            self._telemetry_task = asyncio.Future()
            await self._telemetry_task
        return self.telemetry

    def command_connect_callback(self, command_server):
        """Called when the command server connection state changes.
        """
        self.monitor_command_reader_task.cancel()
        if self.command_connected:
            self.monitor_command_reader_task = asyncio.create_task(self.monitor_command_reader())
        self.call_connect_callback()

    def telemetry_connect_callback(self, telemetry_server):
        """Called when the telemetry server connection state changes.
        """
        self.read_telemetry_and_config_task.cancel()
        if self.telemetry_connected:
            self.read_telemetry_and_config_task = asyncio.create_task(self.read_telemetry_and_config())
        self.call_connect_callback()

    async def read_telemetry_and_config(self):
        """Read telemetry and configuration from the Moog controller.
        """
        while self.telemetry_connected:
            try:
                await utils.read_into(self.telemetry_server.reader, self.header)
                if self.header.frame_id == self.config.FRAME_ID:
                    await utils.read_into(self.telemetry_server.reader, self.config)
                    try:
                        self.config_callback(self)
                    except Exception:
                        self.log.exception("config_callback failed.")
                elif self.header.frame_id == self.telemetry.FRAME_ID:
                    await utils.read_into(self.telemetry_server.reader, self.telemetry)
                    if not self._telemetry_task.done():
                        self._telemetry_task.set_result(None)
                    try:
                        self.telemetry_callback(self)
                    except Exception:
                        self.log.exception("telemetry_callback failed.")
                else:
                    self.log.error(f"Invalid telemetry read: unknown frame_id={self.header.frame_id}; "
                                   "closing the writer.")
                    break
            except asyncio.CancelledError:
                # No need to close the telemetry socket because whoever
                # cancelled this task should do that.
                raise
            except ConnectionError:
                self.log.exception("Telemetry reader closed.")
                break
            except Exception:
                self.log.exception("Unexpected error reading telemetry.")
                break
        await self.telemetry_server.close_client()

    async def put_command(self, cmd):
        """Write a command to the controller.

        Parameters
        ----------
        cmd : `Command`
            Command to write.
        """
        if not self.start_task.done():
            raise RuntimeError("CommandTelemetryServer not ready.")
        if not self.command_connected:
            raise RuntimeError("No command writer")
        if not isinstance(cmd, structs.Command):
            raise ValueError(f"cmd={cmd!r} must be an instance of structs.Command")
        await utils.write_from(self.command_server.writer, cmd)

    async def monitor_command_reader(self):
        """Monitor the command reader; if it closes then close the writer.
        """
        # We do not expect to read any data, but we may as well accept it
        # if some comes in.
        while True:
            await self.command_server.reader.read(1000)
            if self.command_server.reader.at_eof():
                # reader closed; close the writer
                await self.command_server.close_client()
                return
            else:
                self.log.warning("Unexpected data read from the command socket.")
                await asyncio.sleep(0.01)

    async def wait_connected(self):
        """Wait for command and telemetry sockets to be connected."""
        return asyncio.gather(self.command_server.connected_task, self.telemetry_server.connected_task)

    async def start(self):
        """Start command and telemetry TCP/IP servers.
        """
        if self.start_task.done():
            raise RuntimeError("Cannot call start more than once.")
        await asyncio.gather(self.command_server.start_task, self.telemetry_server.start_task)

    def call_connect_callback(self):
        """Call the connect_callback if connection state has changed.
        """
        try:
            self.connect_callback(self)
        except Exception:
            self.log.exception("connect_callback failed.")

    async def async_call_connect_callback(self):
        self.call_connect_callback()

    async def close(self):
        """Close everything."""
        self.monitor_command_reader_task.cancel()
        self.read_telemetry_and_config_task.cancel()
        await self.command_server.close()
        await self.telemetry_server.close()
        self.call_connect_callback()
        if not self.done_task.done():
            self.done_task.set_result(None)
