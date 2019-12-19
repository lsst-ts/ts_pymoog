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

__all__ = ["SIMPLE_SYNC_PATTERN", "SimpleCommandCode", "SimpleConfig", "SimpleTelemetry",
           "SimpleMockController"]

import ctypes
import enum

from lsst.ts.idl.enums import Rotator
from . import constants
from . import base_mock_controller


SIMPLE_SYNC_PATTERN = 0x1234


class SimpleCommandCode(enum.IntEnum):
    SET_STATE = 1
    SET_ENABLED_SUBSTATE = enum.auto()
    MOVE = enum.auto()


class SimpleConfig(ctypes.Structure):
    """Configuration of SimpleMockController.
    """
    _pack_ = 1
    _fields_ = [
        ("min_position", ctypes.c_double),
        ("max_position", ctypes.c_double),
        ("max_velocity", ctypes.c_double),
    ]
    FRAME_ID = 0x19


class SimpleTelemetry(ctypes.Structure):
    """Telemetry from SimpleMockController.
    """
    _pack_ = 1
    _fields_ = [
        ("application_status", ctypes.c_uint),
        ("state", ctypes.c_double),
        ("enabled_substate", ctypes.c_double),
        ("offline_substate", ctypes.c_double),
        ("curr_position", ctypes.c_double),
        ("cmd_position", ctypes.c_double),
    ]
    FRAME_ID = 0x5


class SimpleMockController(base_mock_controller.BaseMockController):
    """Simple mock controller for unit testing BaseMockController.

    The MOVE command sets cmd_position and curr_position,
    then the controller slowly increments curr_position.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    host : `str` (optional)
        IP address of server.
    command_port : `int` (optional)
        Command socket port.  This argument is intended for unit tests;
        use the default value for normal operation.
    telemetry_port : `int` (optional)
        Telemetry socket port. This argument is intended for unit tests;
        use the default value for normal operation.

    Notes
    -----
    The ``MOVE`` command is rejected if the new position is
    not within the configured limits.
    """
    def __init__(self,
                 log,
                 host=constants.LOCAL_HOST,
                 command_port=constants.COMMAND_PORT,
                 telemetry_port=constants.TELEMETRY_PORT,
                 initial_state=Rotator.ControllerState.OFFLINE):
        config = SimpleConfig()
        config.min_position = -25
        config.max_position = 25
        config.max_velocity = 47
        telemetry = SimpleTelemetry()
        extra_commands = {
            SimpleCommandCode.MOVE: self.do_position_set,
        }
        super().__init__(
            log=log,
            CommandCode=SimpleCommandCode,
            extra_commands=extra_commands,
            config=config,
            telemetry=telemetry,
            host=host,
            command_port=command_port,
            telemetry_port=telemetry_port,
            initial_state=initial_state,
        )

    async def do_config_velocity(self, command):
        self.assert_state(Rotator.ControllerState.ENABLED)
        max_velocity = command.param1
        if max_velocity > 0:
            self.config.max_velocity = max_velocity
            await self.write_config()
        else:
            self.log.error(f"Commanded max velocity {max_velocity} <= 0; ignoring the command.")

    async def do_position_set(self, command):
        self.assert_state(Rotator.ControllerState.ENABLED)
        position = command.param1
        if self.config.min_position <= position <= self.config.max_position:
            self.telemetry.cmd_position = position
            self.telemetry.curr_position = position
        else:
            self.log.error(f"Commanded position {position} out of range "
                           f"[{self.config.min_position}, {self.config.max_position}]; ignoring the command.")

    async def update_telemetry(self):
        self.telemetry.application_status = Rotator.ApplicationStatus.DDS_COMMAND_SOURCE
        self.telemetry.curr_position += 0.001

    async def end_run_command(self, **kwargs):
        pass
