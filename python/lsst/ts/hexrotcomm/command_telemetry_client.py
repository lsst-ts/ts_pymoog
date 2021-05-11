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

__all__ = ["CommandTelemetryClient"]

import abc
import asyncio
import math

from lsst.ts import salobj
from . import constants
from . import structs
from . import utils


class CommandTelemetryClient:
    """Client for a mock Moog TCP/IP controller.

    The controller uses two TCP/IP _client_ sockets,
    one to read commands and the other to write telemetry.
    Both sockets must be connected for the controller to operate;
    if either becomes disconnected the controller will stop moving,
    close any open sockets and try to reconnect.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    config : `ctypes.Structure`
        Configuration data. May be modified.
    telemetry : `ctypes.Structure`
        Telemetry data. Modified by `update_telemetry`.
    command_port : `int`
        Command socket port.
    telemetry_port : `int`
        Telemetry socket port.
    host : `str` (optional)
        IP address of CSC server.

    Notes
    -----
    To start a client::

        client = Client(...)
        await client.connect_task

    To stop the client:

        await client.stop()
    """

    telemetry_interval = 0.1
    """Interval between telemetry messages (sec)."""

    connect_retry_interval = 0.1
    """Interval between connection retries (sec)."""

    def __init__(
        self,
        *,
        log,
        config,
        telemetry,
        command_port,
        telemetry_port,
        host=constants.LOCAL_HOST,
    ):
        self.log = log.getChild("BaseMockController")
        self.config = config
        self.telemetry = telemetry
        self.command_port = command_port
        self.telemetry_port = telemetry_port
        self.host = host
        self.headers = dict()
        # A dictionary of frame ID: header for telemetry and config data
        # Keeping separate headers for telemetry and config allows
        # updating just the relevant fields, rather than creating a new
        # header insteance for each telemetry and config message.
        for frame_id in (config.FRAME_ID, telemetry.FRAME_ID):
            header = structs.Header()
            header.frame_id = frame_id
            self.headers[frame_id] = header
        self.command_reader = None
        self.command_writer = None  # not written
        self.telemetry_reader = None  # not read
        self.telemetry_writer = None
        self.command_loop_task = asyncio.Future()
        self.command_loop_task.set_result(None)
        self.telemetry_loop_task = asyncio.Future()
        self.telemetry_loop_task.set_result(None)
        self.connect_task = asyncio.create_task(self.connect())

    @property
    def connected(self):
        """Return True if command and telemetry sockets are connected."""
        return self.command_connected and self.telemetry_connected

    @property
    def command_connected(self):
        """Return True if the command socket is connected."""
        return not (
            self.command_writer is None
            or self.command_writer.is_closing()
            or self.command_reader.at_eof()
        )

    @property
    def telemetry_connected(self):
        """Return True if the telemetry socket is connected."""
        return not (
            self.telemetry_writer is None
            or self.telemetry_writer.is_closing()
            or self.telemetry_reader.at_eof()
        )

    async def close(self):
        """Kill command and telemetry tasks and close the connections.

        Always safe to call.
        """
        self.log.debug("close()")
        self.connect_task.cancel()
        await self._basic_close()
        self.command_reader = None
        self.command_writer = None
        self.telemetry_reader = None
        self.telemetry_writer = None

    async def _basic_close(self):
        """Cancel command and telemetry loops and close the connections.

        Always safe to call.
        """
        self.command_loop_task.cancel()
        self.telemetry_loop_task.cancel()
        if self.command_writer is not None:
            command_writer = self.command_writer
            self.command_writer = None
            await utils.close_stream_writer(command_writer)
        if self.telemetry_writer is not None:
            telemetry_writer = self.telemetry_writer
            self.telemetry_writer = None
            await utils.close_stream_writer(telemetry_writer)

    async def connect(self):
        """Connect the sockets.

        Notes
        -----
        This will wait forever for a connection.
        """
        self.log.info("connect")
        # Close any existing connection, so we only have one connection.
        await self._basic_close()
        self.log.debug("connect: making connections")
        coroutines = []
        if self.command_reader is None:
            coroutines.append(self.connect_command())
        if self.telemetry_writer is None:
            coroutines.append(self.connect_telemetry())
        if coroutines:
            await asyncio.gather(*coroutines)

        self.log.debug("connect: starting command and telemetry loops")
        self.command_loop_task = asyncio.create_task(self.command_loop())
        self.telemetry_loop_task = asyncio.create_task(self.telemetry_loop())

    async def connect_command(self):
        """Connect or reconnect to the command socket.

        Notes
        -----
        This will wait forever for a connection.
        """
        if self.command_writer is not None:
            writer = self.command_writer
            self.command_writer = None
            await utils.close_stream_writer(writer)
        while True:
            try:
                self.log.debug(
                    f"connect_command: connect to host={self.host}, port={self.command_port}"
                )
                (
                    self.command_reader,
                    self.command_writer,
                ) = await asyncio.open_connection(
                    host=self.host, port=self.command_port
                )
                return
            except Exception as e:
                self.log.warning(f"connect_command failed with {e}; retrying")
                await asyncio.sleep(self.connect_retry_interval)

    async def connect_telemetry(self):
        """Connect or reconnect to the telemetry/configuration socket.

        Notes
        -----
        This will wait forever for a connection.
        """
        if self.telemetry_writer is not None:
            writer = self.telemetry_writer
            self.telemetry_writer = None
            await utils.close_stream_writer(writer)
        while True:
            try:
                self.log.debug(
                    f"connect_telemetry: connect to host={self.host}, port={self.telemetry_port}"
                )
                (
                    self.telemetry_reader,
                    self.telemetry_writer,
                ) = await asyncio.open_connection(
                    host=self.host, port=self.telemetry_port
                )
                return
            except Exception as e:
                self.log.warning(f"connect_telemetry failed with {e}; retrying")
                await asyncio.sleep(self.connect_retry_interval)

    async def command_loop(self):
        """Read and execute commands."""
        self.log.info("command_loop begins")
        while self.command_reader is not None:
            try:
                command = structs.Command()
                await utils.read_into(self.command_reader, command)
                await self.run_command(command)
            except asyncio.CancelledError:
                raise
            except ConnectionError:
                self.log.error("Command socket closed; reconnecting")
                asyncio.ensure_future(self.connect_command())
                raise
            except Exception:
                self.log.exception("command_loop failed; reconnecting")
                # disconnect and try again
                asyncio.ensure_future(self.connect_command())
                raise

    async def telemetry_loop(self):
        """Write configuration once, then telemetry at regular intervals."""
        self.log.info("telemetry_loop begins")
        try:
            await self.write_config()
            while self.telemetry_connected:
                header, curr_tai = self.update_and_get_header(self.telemetry.FRAME_ID)
                await self.update_telemetry(curr_tai=curr_tai)
                await utils.write_from(self.telemetry_writer, header, self.telemetry)
                await asyncio.sleep(self.telemetry_interval)
            self.log.warning("Telemetry socket disconnected; reconnecting")
            asyncio.ensure_future(self.connect_telemetry())
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("telemetry_loop failed; reconnecting")
            asyncio.ensure_future(self.connect_telemetry())

    async def write_config(self):
        """Write the current configuration."""
        assert self.telemetry_writer is not None
        header, curr_tai = self.update_and_get_header(self.config.FRAME_ID)
        await utils.write_from(self.telemetry_writer, header, self.config)

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
        curr_tai = salobj.current_tai()
        tai_frac, tai_sec = math.modf(curr_tai)
        header.tai_sec = int(tai_sec)
        header.tai_nsec = int(tai_frac * 1e9)
        return header, curr_tai

    @abc.abstractmethod
    async def run_command(self, command):
        """Run a command.

        Parameters
        ----------
        command : `Command`
            Command to run.
        """
        raise NotImplementedError()
