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

__all__ = ["BaseMockController"]

import abc
import asyncio
import math

import astropy.time

from . import constants
from . import structs
from . import utils


class BaseMockController(metaclass=abc.ABCMeta):
    """Base class for a mock Moog TCP/IP controller.

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

    Notes
    -----
    To start a mock controller:

        ctrl = MockController(...)
        await ctrl.connect_task

    To stop the server:

        await ctrl.stop()
    """
    telemetry_interval = 0.1
    """Interval between telemetry messages (sec)."""

    connect_timeout = 5
    """Time limit to make a connection (sec)."""

    connect_retry_interval = 0.5
    """Interval between connection retries (sec)."""

    def __init__(self, log, config, telemetry):
        self.log = log.getChild("BaseMockController")
        # A dictionary of frame ID: header for telemetry and config data
        # Keeping separate headers for telemetry and config allows
        # updating just the relevant fields, rather than creating a new
        # header insteance for each telemetry and config message.
        self.headers = dict()
        for frame_id in (config.FRAME_ID, telemetry.FRAME_ID):
            header = structs.Header()
            header.frame_id = frame_id
            self.headers[frame_id] = header
        self.config = config
        self.telemetry = telemetry
        self.cmd_reader = None
        self.cmd_writer = None  # not written
        self.tel_reader = None  # not read
        self.tel_writer = None
        self.cmd_loop_task = asyncio.Future()
        self.cmd_loop_task.set_result(None)
        self.tel_loop_task = asyncio.Future()
        self.tel_loop_task.set_result(None)
        self.connect_task = asyncio.create_task(self.connect())

    @property
    def connected(self):
        """Are both sockets connected?
        """
        return self.cmd_reader is not None and self.tel_writer is not None

    @abc.abstractmethod
    async def run_command(self, command):
        """Run a command.

        Parameters
        ----------
        command : `Command`
            Command to execute.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def update_telemetry(self):
        """Update self.telemetry with current values.
        """
        raise NotImplementedError()

    async def close(self, kill_connect=True):
        """Kill command and telemetry tasks and close the connections.

        Always safe to call.
        """
        self.log.debug(f"close(kill_connect={kill_connect})")
        self.cmd_loop_task.cancel()
        self.tel_loop_task.cancel()
        if kill_connect:
            self.connect_task.cancel()
        if self.cmd_writer is not None:
            self.cmd_writer.close()
        if self.tel_writer is not None:
            self.tel_writer.close()
        self.cmd_reader = None
        self.cmd_writer = None
        self.tel_reader = None
        self.tel_writer = None

    async def connect(self):
        """Connect ore reconnect the sockets.

        Notes
        -----
        This will wait forever for a connection
        """
        self.log.info("connect")
        await self.close(kill_connect=False)
        self.log.debug("connect: making connections")
        while True:
            coroutines = []
            if self.cmd_reader is None:
                coroutines.append(self.connect_cmd())
            if self.tel_writer is None:
                coroutines.append(self.connect_tel())
            if coroutines:
                try:
                    await asyncio.gather(*coroutines)
                    break
                except Exception:
                    await asyncio.sleep(self.connect_retry_interval)
                    self.log.debug("connect: retry connection")

        self.log.debug("connect: starting command and telemetry loops")
        self.cmd_loop_task = asyncio.create_task(self.cmd_loop())
        self.tel_loop_task = asyncio.create_task(self.tel_loop())

    async def connect_cmd(self):
        """Connect to the command socket.
        """
        connect_coro = asyncio.open_connection(host=constants.LOCAL_HOST,
                                               port=constants.CMD_SERVER_PORT)
        self.cmd_reader, self.cmd_writer = await asyncio.wait_for(connect_coro,
                                                                  timeout=self.connect_timeout)

    async def connect_tel(self):
        """Connect to the telemetry/configuration socket.
        """
        connect_coro = asyncio.open_connection(host=constants.LOCAL_HOST,
                                               port=constants.TEL_SERVER_PORT)
        self.tel_reader, self.tel_writer = await asyncio.wait_for(connect_coro,
                                                                  timeout=self.connect_timeout)

    async def cmd_loop(self):
        """Read and execute commands.
        """
        self.log.info("cmd_loop begins")
        while self.cmd_reader is not None:
            try:
                command = structs.Command()
                await utils.read_into(self.cmd_reader, command)
                await self.run_command(command)
            except asyncio.CancelledError:
                pass
            except Exception:
                self.log.exception("cmd_loop failed; reconnecting")
                # disconnect and try again
                asyncio.ensure_future(self.connect())
                return

    async def tel_loop(self):
        """Write configuration once, then telemetry at regular intervals.
        """
        self.log.info("tel_loop begins")
        try:
            await self.write_config()
            while self.tel_writer is not None:
                header = self.update_and_get_header(self.telemetry.FRAME_ID)
                await self.update_telemetry()
                await utils.write_from(self.tel_writer, header, self.telemetry)
                await asyncio.sleep(self.telemetry_interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("tel_loop failed; reconnecting")
            asyncio.ensure_future(self.connect())

    async def write_config(self):
        """Write the current configuration.
        """
        assert self.tel_writer is not None
        header = self.update_and_get_header(self.config.FRAME_ID)
        await utils.write_from(self.tel_writer, header, self.config)

    def update_and_get_header(self, frame_id):
        """Update the config or telemetry header and return it.

        Call this prior to writing telemetry or configuration.

        Parameters
        ----------
        frame_id : `int`
            Frame ID of header to write.
        """
        header = self.headers[frame_id]
        curr_time = astropy.time.Time.now()
        mjd_frac, mjd_days = math.modf(curr_time.utc.mjd)
        header.mjd = int(mjd_days)
        header.mjd_frac = mjd_frac
        unix_frac, unix_sec = math.modf(curr_time.utc.unix)
        header.tv_sec = int(unix_sec)
        header.tv_nsec = int(unix_frac * 1e9)
        return header
