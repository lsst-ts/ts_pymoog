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
import inspect
import ctypes

from lsst.ts import utils
from lsst.ts import salobj
from lsst.ts import tcpip
from . import structs
from . import enums

# Time limit waiting for a command status (second).
COMMAND_STATUS_TIMEOUT = 5


class CommandTelemetryClient:
    """TCP/IP Client for a Moog CSC.

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
    port : `int`
        Server port.
    connect_callback : coroutine
        Coroutine to call when a connection is made or dropped.
        The function receives one argument: this client.
    config_callback : coroutine
        Coroutine to call when configuration is read.
        The function receives one argument: this client.
    telemetry_callback : coroutine
        Coroutine to call when telemetry is read.
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
    port : `int`
        The ``port`` constructor argument.
    connect_callback : coroutine
        The ``connect_callback`` constructor argument.
    config_callback : coroutine
        The ``config_callback`` constructor argument.
    telemetry_callback : coroutine
        The ``telemetry_callback`` constructor argument.
    reader : `asyncio.StreamReader` or `None`
        Stream reader for reading data from the low-level controller.
        May be None if not connected.
    writer : `asynicio.StreamWriter` or `None`
        Stream writer for sending commands to the low-level controller.
        May be None if not connected.
    connect_task : `asyncio.Task`
        A task for the `connect` method, which starts automatically
        when this class is instantiated.
        Set done when the client is connected.
    should_be_connected : `bool`
        Do we expect to be connected?
        If your connect_callback receives notice that the stream
        is disconnected and ``should_be_connected`` is true, then the
        the disconnection was unexpected and indicates a problem.
        `connect` sets this true when it finishes successfully,
        and `disconnect` set it false as it begins.

    Raises
    ------
    TypeError
        If any of the callbacks is not a coroutine.

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
        port,
        connect_callback,
        config_callback,
        telemetry_callback,
        connect_timeout=10,
    ):
        for arg_name in ("connect_callback", "config_callback", "telemetry_callback"):
            arg_value = locals()[arg_name]
            if not inspect.iscoroutinefunction(arg_value):
                raise TypeError(f"{arg_name} must be a coroutine")

        self.log = log.getChild("BaseMockController")
        self.header = structs.Header()
        self.config = ConfigClass()
        self.telemetry = TelemetryClass()
        self.host = host
        self.port = port
        self.connect_callback = connect_callback
        self.config_callback = config_callback
        self.telemetry_callback = telemetry_callback
        self.connect_timeout = connect_timeout
        # Resource lock for writing a command and waiting for acknowledgement.
        self._command_lock = asyncio.Lock()
        self.reader = None
        self.writer = None
        self.should_be_connected = False

        # The state of connected
        # the last time connect_callback was called.
        self._was_connected = False

        # Hold on to the last command, in order to increment the command
        # counter with correct wraparound.
        # An index generator would also work, but this avoids duplicating
        # information about the type of the command counter field.
        # Start from -1 so that the first command has counter=0.
        self._last_command = structs.Command()
        self._last_command.commander = structs.Command.COMMANDER
        self._last_command.counter -= 1

        # Task set to None when config is first seen and handled by
        # config_callback, or the exception if config_callback raises.
        self.configured_task = asyncio.Future()

        # Task used by next_telemetry to detect when the next telemetry
        # is read.
        self._telemetry_task = asyncio.Future()

        # Task used to wait for a command acknowledgement
        self._read_command_status_task = utils.make_done_future()

        self._read_loop_task = utils.make_done_future()
        self.connect_task = asyncio.create_task(self.connect())

    @property
    def connected(self):
        """Return True if the command socket is connected."""
        return not (
            self.writer is None
            or self.reader is None
            or self.writer.is_closing()
            or self.reader.at_eof()
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
        self._read_loop_task.cancel()
        self.configured_task.cancel()
        self._telemetry_task.cancel()
        if self.connected:
            try:
                await tcpip.close_stream_writer(self.writer)
            except asyncio.CancelledError:
                pass
        asyncio.create_task(self.call_connect_callback())

    async def connect(self):
        """Connect to the command socket.

        Unlike high-level method `connect` this waits forever for a connection
        and does not call self.basic_close on error.
        """
        if self.writer is not None:
            raise RuntimeError("A connection was already made")
        try:
            self.log.debug(f"connect: connecting to host={self.host}, port={self.port}")
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host=self.host, port=self.port),
                timeout=self.connect_timeout,
            )
            self.should_be_connected = True
            self.log.debug("connect: connected")
            self._read_loop_task = asyncio.create_task(self.read_loop())
            await self.call_connect_callback()
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

    async def call_connect_callback(self):
        """Call the connect_callback if connection state has changed."""
        was_connected = self.connected
        if was_connected != self._was_connected:
            self._was_connected = was_connected
            try:
                await self.connect_callback(self)
            except Exception:
                self.log.exception("connect_callback failed.")
        else:
            self.log.debug(
                "call_connect_callback not calling connect_callback; no change"
            )

    async def read_loop(self):
        """Read from the Moog controller."""
        try:
            # Number of bytes to flush if a header is not recognized.
            # The size of the largest non-header struct.
            max_flush_bytes = max(
                ctypes.sizeof(self.telemetry),
                ctypes.sizeof(self.config),
                ctypes.sizeof(structs.CommandStatus),
            )

            while self.connected:
                await tcpip.read_into(self.reader, self.header)
                if not self.connected:
                    break
                if self.header.frame_id == enums.FrameId.COMMAND_STATUS:
                    command_status = structs.CommandStatus()
                    await tcpip.read_into(self.reader, command_status)
                    if self._read_command_status_task.done():
                        continue
                    if self.header.counter == self._last_command.counter:
                        self._read_command_status_task.set_result(command_status)
                    else:
                        self.log.warning(
                            "Ignoring command status for wrong command; "
                            f"read counter={self.header.counter} "
                            f"!= expected value {self._last_command.counter}"
                        )
                elif self.header.frame_id == enums.FrameId.CONFIG:
                    await tcpip.read_into(self.reader, self.config)
                    try:
                        await self.config_callback(self)
                        if not self.configured_task.done():
                            self.configured_task.set_result(None)
                    except Exception as e:
                        self.log.exception("config_callback failed.")
                        if not self.configured_task.done():
                            self.configured_task.set_exception(e)
                elif self.header.frame_id == enums.FrameId.TELEMETRY:
                    await tcpip.read_into(self.reader, self.telemetry)
                    if not self._telemetry_task.done():
                        self._telemetry_task.set_result(None)
                    try:
                        await self.telemetry_callback(self)
                    except Exception:
                        self.log.exception("telemetry_callback failed.")
                else:
                    self.log.error(
                        f"Invalid header read: unknown frame_id={self.header.frame_id}; "
                        f"flushing and continuing. Bytes: {bytes(self.header)}"
                    )
                    data = await self.reader.read(max_flush_bytes)
                    self.log.info(f"Flushed {len(data)} bytes")
        except asyncio.CancelledError:
            # No need to close the connection, because the code that cancelled
            # this task is expected to do that.
            raise
        except ConnectionError:
            self.log.exception("Reader unexpectedly closed.")
        except Exception:
            self.log.exception("Unexpected error in read loop.")
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
            If not connected.
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
        if not self.connected:
            raise RuntimeError("Not connected")
        if not isinstance(command, structs.Command):
            raise ValueError(
                f"command={command!r} must be an instance of structs.Command"
            )

        async with self._command_lock:
            # Cancel the task just to be sure; it's hard to see how it
            # could be running at this point.
            self._read_command_status_task.cancel()
            self._read_command_status_task = asyncio.Future()

            command.commander = command.COMMANDER
            command.counter = self._last_command.counter + 1
            self._last_command = command
            await tcpip.write_from(self.writer, command)

            command_status = await asyncio.wait_for(
                self._read_command_status_task,
                timeout=COMMAND_STATUS_TIMEOUT,
            )
            if command_status.status == enums.CommandStatusCode.ACK:
                return command_status.duration
            elif command_status.status == enums.CommandStatusCode.NO_ACK:
                reason = command_status.reason.decode()
                raise salobj.ExpectedError(reason)
            else:
                raise salobj.ExpectedError(
                    f"Unknown command status {command_status.status}; "
                    "low-level command assumed to have failed"
                )
