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

import asyncio
import ctypes

from lsst.ts import utils
from lsst.ts import salobj
from lsst.ts import tcpip
from . import structs
from .enums import CommandStatusCode

# Time limit waiting for a command status (second).
COMMAND_STATUS_TIMEOUT = 5


class CommandTelemetryClient:
    """TCP/IP Client for a Moog CSC.

    This connects to the low-level controller via two client sockets:
    one to write commands and one to read telemetry and configuration.
    This client will close both sockets if either becomes disconnected.

    This client cannot be reconnected; once closed, you must create a new one.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    ConfigClass : `ctypes.Structure`
        Class for configuration.
    TelemetryClass : `ctypes.Structure`
        Class for telemetry
    host : `str`
        IP address of CSC server.
    command_port : `int`
        Command socket port.
    telemetry_port : `int`
        Telemetry socket port.
    connect_callback : callable
        Function to call when a connection is made or dropped.
        The function receives one argument: this client.
    config_callback : callable
        Function to call when configuration is read.
        The function receives one argument: this client.
    telemetry_callback : callable
        Function to call when telemetry is read.
        The function receives one argument: this client.
    connect_timeout : `float`, optional
        Time limit for a connection to be made (seconds).

    Attributes
    ----------
    log : `logging.Logger`
        A child of the ``log`` constructor argument.
    header : ``Header``
        The most recently read header
        (which may be for config, telemetry, or unrecognized).
    config : ``ConfigClass``
        The most recently read configuration.
        A null-constructed instance before configuration is read.
    configured_task : `asyncio.Future`
        A Future that is set to None when configuration is first read and
        processed, or to the exception if ``config_callback`` raises.
    telemetry : ``TelemetryClass``
        The most recently read telemetry.
        A null-constructed instance before telemetry is read.
    host : `str`
        The ``host`` constructor argument.
    command_port : `int`
        The ``command_port`` constructor argument.
    telemetry_port : `int`
        The ``telemetry_port`` constructor argument.
    connect_callback : callable
        The ``connect_callback`` constructor argument.
    config_callback : callable
        The ``config_callback`` constructor argument.
    telemetry_callback : callable
        The ``telemetry_callback`` constructor argument.
    command_reader : `asyncio.StreamReader` or `None`
        Do not touch. This class watches this reader
        for disconnection of the command stream.
        May be None if the command stream is not connected.
    command_writer : `asynicio.StreamWriter` or `None`
        Stream writer for sending commands to the low-level controller.
        May be None if the command stream is not connected.
    telemetry_reader : `asyncio.StreamReader`
        Stream reader for reading configuration and telemetry messages
        from the low-level controller.
        May be None if the telemetry stream is not connected.
    telemetry_writer : `asynicio.StreamWriter`
        Only used to close the telemetry stream.
        Don't write data to this stream, because the low-level controller
        will ignore it.
        May be None if the telemetry stream is not connected.
    connect_task : `asyncio.Task`
        A task for the `connect` method, which starts automatically
        when this class is instantiated.
        Set done when the client is connected to both streams.
    should_be_connected : `bool`
        Do we expect both streams to be connected?
        If your connect_callback receives notice that one or both streams
        is disconnected and ``should_be_connected`` is true, then the
        the disconnection was unexpected and indicates a problem.
        `connect` sets this true when it finishes successfully,
        and `disconnect` set it false as it begins.

    Notes
    -----
    To start a client::

        client = Client(...)
        await client.connect_task

    To stop the client:

        await client.stop()
    """

    def __init__(
        self,
        *,
        log,
        ConfigClass,
        TelemetryClass,
        host,
        command_port,
        telemetry_port,
        connect_callback,
        config_callback,
        telemetry_callback,
        connect_timeout=10,
    ):
        self.log = log.getChild("BaseMockController")
        self.header = structs.Header()
        self.config = ConfigClass()
        self.telemetry = TelemetryClass()
        self.host = host
        self.command_port = command_port
        self.telemetry_port = telemetry_port
        self.connect_callback = connect_callback
        self.config_callback = config_callback
        self.telemetry_callback = telemetry_callback
        self.connect_timeout = connect_timeout
        # Resource lock for writing a command to the command stream
        # and waiting for acknowledgement.
        self._command_lock = asyncio.Lock()
        self.command_reader = None
        self.command_writer = None  # not written
        self.telemetry_reader = None  # not read
        self.telemetry_writer = None
        self.should_be_connected = False

        # The state of (command_connected, telemetry_connected)
        # the last time connect_callback was called.
        self._was_connected = (False, False)

        # Hold on to the last command, in order to increment the command
        # counter with correct wraparound.
        # An index generator would also work, but this avoids duplicating
        # information about the type of the command counter field.
        # Start from -1 so that the first command has counter=0.
        self._last_command = structs.Command()
        self._last_command.counter -= 1

        # Task set to None when config is first seen and handled by
        # config_callback, or the exception if config_callback raises.
        self.configured_task = asyncio.Future()

        # Task used by next_telemetry to detect when the next telemetry
        # is read.
        self._telemetry_task = asyncio.Future()

        # Task used to wait for a command acknowledgement
        self._read_command_status_task = utils.make_done_future()

        self._read_telemetry_and_config_task = utils.make_done_future()
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
        self.should_be_connected = False
        await self.basic_close()

    async def basic_close(self):
        """Close without clearing self.should_be_connected.

        Always safe to call.
        """
        self._read_command_status_task.cancel()
        self.connect_task.cancel()
        self._read_telemetry_and_config_task.cancel()
        self.configured_task.cancel()
        self._telemetry_task.cancel()
        if self.command_connected:
            try:
                await tcpip.close_stream_writer(self.command_writer)
            except asyncio.CancelledError:
                pass
        if self.telemetry_connected:
            try:
                await tcpip.close_stream_writer(self.telemetry_writer)
            except asyncio.CancelledError:
                pass
        self.call_connect_callback()

    async def connect(self):
        """Connect the sockets and start the background tasks."""
        try:
            await asyncio.wait_for(
                asyncio.gather(self.connect_command(), self.connect_telemetry()),
                timeout=self.connect_timeout,
            )
            self.should_be_connected = True
        except asyncio.CancelledError:
            self.debug("connect cancelled")
            await self.basic_close()
            raise
        except asyncio.TimeoutError:
            self.log.error(
                "Failed to connect to the server: "
                f"timed out after {self.connect_timeout} seconds"
            )
            await self.basic_close()
            raise
        except Exception:
            self.log.exception("Failed to connect to the server")
            await self.basic_close()
            raise

    async def connect_command(self):
        """Connect to the command socket.

        Unlike high-level method `connect` this waits forever for a connection
        and does not call self.basic_close on error.
        """
        if self.command_writer is not None:
            raise RuntimeError("A command connection was already made")
        self.log.debug(
            f"connect_command: connect to host={self.host}, port={self.command_port}"
        )
        self.command_reader, self.command_writer = await asyncio.open_connection(
            host=self.host, port=self.command_port
        )
        self.log.debug("Command socket connected")
        self.call_connect_callback()

    async def connect_telemetry(self):
        """Connect to the telemetry/configuration socket and start
        read_telemetry_and_config loop.

        Unlike high-level method `connect` this waits forever for a connection
        and does not call self.basic_close on error.
        """
        if self.command_writer is not None:
            raise RuntimeError("A telemetry connection was already made")
        self.log.debug(
            f"connect_telemetry: connect to host={self.host}, port={self.telemetry_port}"
        )
        self.telemetry_reader, self.telemetry_writer = await asyncio.open_connection(
            host=self.host, port=self.telemetry_port
        )
        self.log.debug(
            "Telemetry socket connected; starting telemetry and config read loop"
        )
        self._read_telemetry_and_config_task = asyncio.create_task(
            self.read_telemetry_and_config()
        )
        self.call_connect_callback()

    def call_connect_callback(self):
        """Call the connect_callback if connection state has changed."""
        was_connected = (self.command_connected, self.telemetry_connected)
        if was_connected != self._was_connected:
            self._was_connected = was_connected
            try:
                self.connect_callback(self)
            except Exception:
                self.log.exception("connect_callback failed.")
        else:
            self.log.debug(
                "call_connect_callback not calling connect_callback; no change"
            )

    async def read_telemetry_and_config(self):
        """Read telemetry and configuration from the Moog controller."""
        # Compute the maximum number of bytes to read after the header
        # if the header is not recognized, to flush the stream.
        max_config_telemetry_bytes = max(
            ctypes.sizeof(self.telemetry), ctypes.sizeof(self.config)
        )
        try:
            while self.telemetry_connected:
                await tcpip.read_into(self.telemetry_reader, self.header)
                if self.header.frame_id == self.config.FRAME_ID:
                    await tcpip.read_into(self.telemetry_reader, self.config)
                    try:
                        self.config_callback(self)
                        if not self.configured_task.done():
                            self.configured_task.set_result(None)
                    except Exception as e:
                        self.log.exception("config_callback failed.")
                        if not self.configured_task.done():
                            self.configured_task.set_exception(e)
                elif self.header.frame_id == self.telemetry.FRAME_ID:
                    await tcpip.read_into(self.telemetry_reader, self.telemetry)
                    if not self._telemetry_task.done():
                        self._telemetry_task.set_result(None)
                    try:
                        self.telemetry_callback(self)
                    except Exception:
                        self.log.exception("telemetry_callback failed.")
                else:
                    self.log.error(
                        f"Invalid header read: unknown frame_id={self.header.frame_id}; "
                        f"flushing and continuing. Bytes: {bytes(self.header)}"
                    )
                    data = await self.telemetry_reader.read(max_config_telemetry_bytes)
                    self.log.info(f"Flushed {len(data)} bytes")
        except asyncio.CancelledError:
            # No need to close the telemetry socket because whoever
            # cancelled this task should do that.
            raise
        except ConnectionError:
            self.log.exception("Telemetry reader closed.")
        except Exception:
            self.log.exception("Unexpected error reading telemetry stream.")
        await self.basic_close()

    async def next_telemetry(self):
        """Wait for next telemetry."""
        if self._telemetry_task.done():
            self._telemetry_task = asyncio.Future()
        await self._telemetry_task
        return self.telemetry

    async def run_command(self, command, interrupt=False):
        """Run a command and wait for acknowledgement.

        Parameters
        ----------
        command : `Command`
            Command to write. Its counter field will be set.
        interrupt : `bool`, optional
            Interrupt the current command, if any?
            Only use this for the stop command and similar.

        Returns
        -------
        duration : `float`
            The expected duration of the command (seconds).

        Raises
        ------
        RuntimeError
            If the command stream is not connected
        ValueError
            If ``command`` is not an instance of `structs.Command`.
        asyncio.TimeoutError
            If no acknowledgement is seen in time.
        lsst.ts.salobj.ExpectedError
            If the command fails.

        Notes
        -----
        Holds the command lock until the reply for this command is seen
        (or the time limit is exceeded).
        """
        if not self.command_connected:
            raise RuntimeError("Command socket not connected")
        if not isinstance(command, structs.Command):
            raise ValueError(
                f"command={command!r} must be an instance of structs.Command"
            )

        async with self._command_lock:
            # Cancel the task just to be sure; it's hard to see how it
            # could be running at this point.
            self._read_command_status_task.cancel()

            command.counter = self._last_command.counter + 1
            self._last_command = command
            await tcpip.write_from(self.command_writer, command)

            self._read_command_status_task = asyncio.create_task(
                asyncio.wait_for(
                    self.read_command_status(command.counter),
                    timeout=COMMAND_STATUS_TIMEOUT,
                )
            )
            return await self._read_command_status_task

    async def read_command_status(self, counter):
        """Wait for a command status message whose counter matches
        a specified value.

        Returns
        -------
        duration : `float`
            If the command succeeds.

        Raises
        ------
        salobj.ExpectedError
            If the command failed, the command status code is not recognized,
            or the command stream disconnects.
        """
        header = structs.Header()
        command_status = structs.CommandStatus()
        try:
            while True:
                if not self.command_connected:
                    raise salobj.ExpectedError(
                        "Command socket disconnected while running the command"
                    )

                await tcpip.read_into(self.command_reader, header)
                if header.frame_id != structs.CommandStatus.FRAME_ID:
                    self.log.warning(
                        f"Ignoring message with unexpected frame_id: "
                        f"{header.frame_id} != {structs.CommandStatus.FRAME_ID}"
                    )
                    continue

                if not self.command_connected:
                    raise salobj.ExpectedError(
                        "Command socket disconnected while running the command"
                    )
                await tcpip.read_into(self.command_reader, command_status)

                if header.counter != counter:
                    self.log.warning(
                        "Ignoring command status for wrong command; "
                        f"status.counter={header.counter} "
                        f"!= expected value {counter}"
                    )
                    continue
                break
        except ConnectionError:
            self.log.warning("Command stream disconnected; closing client")
            await self.basic_close()
            raise salobj.ExpectedError(
                "Command socket disconnected while running the command"
            )

        if command_status.status == CommandStatusCode.ACK:
            return command_status.duration
        elif command_status.status == CommandStatusCode.NO_ACK:
            reason = command_status.reason.decode()
            raise salobj.ExpectedError(reason)
        else:
            raise salobj.ExpectedError(
                f"Unknown command status {command_status.status}; "
                "low-level command assumed to have failed"
            )
