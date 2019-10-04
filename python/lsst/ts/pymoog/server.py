# This file is part of ts_pymoog.
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

__all__ = ["Server"]

import asyncio

from . import constants
from . import structs
from . import utils


class Server:
    """Serve command and telemetry ports for the cRIO to connect to.

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
    config_callback : callable
        Function to call when configuration is read.
        The function receives one argument: this server.
    telemetry_callback : callable
        Function to call when telemetry is read.
        The function receives one argument: this server.
    """
    connect_timeout = 10
    """Time limit for Moog controller to connect to this server (sec)."""

    disconnect_timeout = 5
    """Time limit to close the server and telemetry sockets (sec)."""

    def __init__(self, host, log, ConfigClass, TelemetryClass, config_callback, telemetry_callback):
        self.host = host
        self.log = log.getChild("Server")
        self.config = ConfigClass()
        self.telemetry = TelemetryClass()
        self.config_callback = config_callback
        self.telemetry_callback = telemetry_callback

        self.command_writer_task = asyncio.Future()
        self.telemetry_reader_task = asyncio.Future()
        self._read_tel_running = False
        self.command_writer = None
        self.command_server = None
        self.telemetry_server = None
        self.start_task = asyncio.create_task(self.start())

    async def set_command_writer(self, reader, writer):
        """Send commands to the Moog controller.
        """
        if self.command_writer is not None:
            raise RuntimeError("Cannot write commands to more than one client")
        self.command_writer = writer
        self.command_writer_task.set_result(None)

    async def read_telemetry_and_config(self, reader, writer):
        """Read telemetry and configuration from the Moog controller.
        """
        if self._read_tel_running:
            raise RuntimeError("Cannot have two read_telemetry_and_config loops running")
        self._read_tel_running = True
        self.telemetry_reader_task.set_result(None)
        while True:
            header = structs.Header()
            await utils.read_into(reader, header)
            if header.frame_id == self.config.FRAME_ID:
                await utils.read_into(reader, self.config)
                try:
                    self.config_callback(self)
                except Exception:
                    self.log.exception("config_callback failed")
            elif header.frame_id == self.telemetry.FRAME_ID:
                await utils.read_into(reader, self.telemetry)
                try:
                    self.telemetry_callback(self)
                except Exception:
                    self.log.exception("telemetry_callback failed")
            else:
                raise RuntimeError(f"Invalid data read on the telemetry socket; frame_id={header.frame_id}")

    async def put_command(self, cmd):
        """Write a command to the controller.

        Parameters
        ----------
        cmd : `Command`
            Command to write.
        """
        if not self.start_task.done():
            raise RuntimeError("Server not ready.")
        if not isinstance(cmd, structs.Command):
            raise ValueError(f"cmd={cmd!r} must be an instance of structs.Command")
        await utils.write_from(self.command_writer, cmd)

    async def start(self):
        """Start command and telemetry TCP/IP servers and wait for
        the controller to connect.
        """
        # Start the command and telemetry servers.
        self.command_server = await asyncio.start_server(self.set_command_writer, host=self.host,
                                                         port=constants.CMD_SERVER_PORT)
        self.telemetry_server = await asyncio.start_server(self.read_telemetry_and_config, host=self.host,
                                                           port=constants.TEL_SERVER_PORT)
        # Wait for the controller to connect to the command and telemetry
        # servers.
        await asyncio.wait_for(asyncio.gather(self.command_writer_task, self.telemetry_reader_task),
                               timeout=self.connect_timeout)

    async def close(self):
        """Stop the command and telemetry servers.

        Raises
        ------
        asyncio.TimeoutError
            If server.wait_closed() takes longer than disconnect_timeout.
        """
        self.command_writer = None
        servers_to_close = []
        if self.command_server is not None:
            servers_to_close.append(self.command_server)
            self.command_server = None
        if self.telemetry_server is not None:
            servers_to_close.append(self.telemetry_server)
            self.telemetry_server = None

        for server in servers_to_close:
            server.close()
        await asyncio.wait_for(asyncio.gather(*[server.wait_closed() for server in servers_to_close]),
                               timeout=self.disconnect_timeout)
