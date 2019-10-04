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

__all__ = ["SimpleCommandType", "SimpleConfig", "SimpleTelemetry", "SimpleMockController"]

import ctypes
import enum
import time

from . import base_mock_controller


class SimpleCommandType(enum.IntEnum):
    SET_POSITION = 1
    CONFIG_MIN_MAX_POSITION = 2


class SimpleConfig(ctypes.Structure):
    """Configuration of SimpleMockController.
    """
    _pack_ = 1
    _fields_ = [
        ("min_position", ctypes.c_double),
        ("max_position", ctypes.c_double),
    ]
    FRAME_ID = 0x19


class SimpleTelemetry(ctypes.Structure):
    """Telemetry from SimpleMockController.
    """
    _pack_ = 1
    _fields_ = [
        ("position", ctypes.c_double),
        ("time", ctypes.c_double),
    ]
    FRAME_ID = 0x5


class SimpleMockController(base_mock_controller.BaseMockController):
    """Simple mock controller for unit testing BaseMockController.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    config : `SimpleConfig`
        Initial configuration. Updated by the ``SET_POSITION`` command.
    telemetry : `SimpleTelemetry`
        Initial telemetry. Updated at regular intervals.

    Notes
    -----
    The ``SET_POSITION`` command is rejected if the new position is
    not within the configured limits.

    The ``CONFIG_MIN_MAX_POSITION`` command is rejected if
    ``min_position <= max_position``. The new limits do not affect
    the current position, even if it is out of range.
    """
    async def run_command(self, command):
        """Run a command.

        Parameters
        ----------
        command : `Command`
            Command to execute.
        """
        self.log.debug(r"run_command; cmd=%s", command.cmd)
        if command.cmd == SimpleCommandType.SET_POSITION:
            position = command.param1
            if self.config.min_position <= position <= self.config.max_position:
                print(f"Set telemetry.position={position}")
                self.telemetry.position = position
            else:
                self.log.error("Commanded position out of range; ignoring the command.")
        elif command.cmd == SimpleCommandType.CONFIG_MIN_MAX_POSITION:
            min_position = command.param1
            max_position = command.param2
            if min_position < max_position:
                self.config.min_position = min_position
                self.config.max_position = max_position
                await self.write_config()
            else:
                self.log.error("Commanded configuration not valid; ignoring the command.")
        else:
            self.log.error(f"Unknown command {command.cmd}; ignoring the command.")

    async def update_telemetry(self):
        self.telemetry.time = time.time()
