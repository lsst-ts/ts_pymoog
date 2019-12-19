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

from lsst.ts.idl.enums import Rotator
from . import constants
from . import enums
from . import command_telemetry_client


class BaseMockController(command_telemetry_client.CommandTelemetryClient, metaclass=abc.ABCMeta):
    """Base class for a mock Moog TCP/IP controller with states.

    The controller uses two TCP/IP _client_ sockets,
    one to read commands and the other to write telemetry.
    Both sockets must be connected for the controller to operate;
    if either becomes disconnected the controller will stop moving,
    close any open sockets and try to reconnect.

    Parameters
    ----------
    log : `logging.Logger`
        Logger.
    extra_commands : dict of command key: method
        Device-specific commands, as a dict of command key (as returned by
        `get_command_key`): method to call for that command.
        Note: BaseMockController already supports the standard state
        transition commands, including CLEAR_ERROR.
    CommandCode : `enum`
        Command codes.
    config : `ctypes.Structure`
        Configuration data. May be modified.
    telemetry : `ctypes.Structure`
        Telemetry data. Modified by `update_telemetry`.
    host : `str` (optional)
        IP address of CSC server.
    command_port : `int` (optional)
        Command socket port.  This argument is intended for unit tests;
        use the default value for normal operation.
    telemetry_port : `int` (optional)
        Telemetry socket port. This argument is intended for unit tests;
        use the default value for normal operation.
    initial_state : `lsst.ts.idl.enums.Rotator.ControllerState` (optional)
        Initial state of mock controller.

    Notes
    -----
    To start a mock controller:

        ctrl = MockController(...)
        await ctrl.connect_task

    To stop the server:

        await ctrl.stop()
    """
    connect_retry_interval = 0.1
    """Interval between connection retries (sec)."""

    def __init__(self,
                 log,
                 CommandCode,
                 extra_commands,
                 config,
                 telemetry,
                 host=constants.LOCAL_HOST,
                 command_port=constants.COMMAND_PORT,
                 telemetry_port=constants.TELEMETRY_PORT,
                 initial_state=Rotator.ControllerState.OFFLINE):
        self.CommandCode = CommandCode

        # Dict of command key: command
        self.command_table = {
            (CommandCode.SET_STATE, enums.SetStateParam.START): self.do_start,
            (CommandCode.SET_STATE, enums.SetStateParam.ENABLE): self.do_enable,
            (CommandCode.SET_STATE, enums.SetStateParam.STANDBY): self.do_standby,
            (CommandCode.SET_STATE, enums.SetStateParam.DISABLE): self.do_disable,
            (CommandCode.SET_STATE, enums.SetStateParam.EXIT): self.do_exit,
            (CommandCode.SET_STATE, enums.SetStateParam.CLEAR_ERROR): self.do_clear_error,
            (CommandCode.SET_STATE, enums.SetStateParam.ENTER_CONTROL): self.do_enter_control,
        }
        self.command_table.update(extra_commands)

        super().__init__(
            log=log,
            config=config,
            telemetry=telemetry,
            host=host,
            command_port=command_port,
            telemetry_port=telemetry_port)

        self.set_state(initial_state)

    @property
    def state(self):
        return self.telemetry.state

    @property
    def offline_substate(self):
        return self.telemetry.offline_substate

    @property
    def enabled_substate(self):
        return self.telemetry.enabled_substate

    def assert_stationary(self):
        self.assert_state(Rotator.ControllerState.ENABLED,
                          enabled_substate=Rotator.EnabledSubstate.STATIONARY)

    def get_command_key(self, command):
        """Return the key to command_table."""
        if command.cmd in (self.CommandCode.SET_STATE,
                           self.CommandCode.SET_ENABLED_SUBSTATE):
            return (command.cmd, int(command.param1))
        return command.cmd

    def assert_state(self, state, offline_substate=None, enabled_substate=None):
        if self.state != state:
            raise RuntimeError(f"state={self.state!r}; must be {state!r} for this command.")
        if offline_substate is not None and self.offline_substate != offline_substate:
            raise RuntimeError(f"offline_substate={self.offline_substate!r}; "
                               f"must be {offline_substate!r} for this command.")
        if enabled_substate is not None and self.enabled_substate != enabled_substate:
            raise RuntimeError(f"enabled_substate={self.enabled_substate!r}; "
                               f"must be {enabled_substate!r} for this command.")

    async def do_enter_control(self, command):
        self.assert_state(Rotator.ControllerState.OFFLINE,
                          offline_substate=Rotator.OfflineSubstate.AVAILABLE)
        self.set_state(Rotator.ControllerState.STANDBY)

    async def do_start(self, command):
        self.assert_state(Rotator.ControllerState.STANDBY)
        self.set_state(Rotator.ControllerState.DISABLED)

    async def do_enable(self, command):
        self.assert_state(Rotator.ControllerState.DISABLED)
        self.set_state(Rotator.ControllerState.ENABLED)

    async def do_disable(self, command):
        self.assert_state(Rotator.ControllerState.ENABLED)
        self.set_state(Rotator.ControllerState.DISABLED)

    async def do_standby(self, command):
        self.assert_state(Rotator.ControllerState.DISABLED)
        self.set_state(Rotator.ControllerState.STANDBY)

    async def do_exit(self, command):
        self.assert_state(Rotator.ControllerState.STANDBY)
        self.set_state(Rotator.ControllerState.OFFLINE)

    async def do_clear_error(self, command):
        # Allow initial state FAULT and OFFLINE because the real controller
        # requires two sequential CLEAR_COMMAND commands. For the mock
        # controller the first command will (probably) transition from FAULT
        # to OFFLINE, but the second must be accepted without complaint.
        if self.state not in (Rotator.ControllerState.FAULT, Rotator.ControllerState.OFFLINE):
            raise RuntimeError(f"state={self.state!r}; must be FAULT or OFFLINE for this command.")
        self.set_state(Rotator.ControllerState.OFFLINE)

    async def run_command(self, command):
        self.log.debug(f"run_command: "
                       f"sync_pattern={hex(command.sync_pattern)}; "
                       f"counter={command.counter}; "
                       f"command={self.CommandCode(command.cmd)!r}; "
                       f"param1={command.param1}; "
                       f"param2={command.param2}; "
                       f"param3={command.param3}; "
                       f"param4={command.param4}; "
                       f"param5={command.param5}; "
                       f"param6={command.param6}")
        key = self.get_command_key(command)
        cmd_method = self.command_table.get(key, None)
        if cmd_method is None:
            self.log.error(f"Unrecognized command cmd={command.cmd}; param1={command.param1}")
            return
        try:
            await cmd_method(command)
        except Exception as e:
            self.log.error(f"Command cmd={command.cmd}; param1={command.param1} failed: {e}")
        await self.end_run_command(command=command, cmd_method=cmd_method)

    @abc.abstractmethod
    async def end_run_command(self, command, cmd_method):
        """Called when run_command is done.

        Can be used to clear the set position.
        """
        raise NotImplementedError()

    def set_state(self, state):
        """Set the current state and substates.

        Parameters
        ----------
        state : `lsst.ts.idl.enums.Rotator.ControllerState` or `int`
            New state.

        Notes
        -----
        Sets the substates as follows:

        * `lsst.ts.idl.enums.Rotator.OfflineSubstate.AVAILABLE`
          if state == `lsst.ts.idl.enums.Rotator.ControllerState.OFFLINE`
        * `lsst.ts.idl.enums.Rotator.EnabledSubstate.STATIONARY`
          if state == `lsst.ts.idl.enums.Rotator.ControllerState.ENABLED`

        The real controller goes to substate
        `lsst.ts.idl.enums.Rotator.OfflineSubstate.PUBLISH_ONLY` when going
        offline, but requires the engineering user interface (EUI) to get out
        of that state, and we don't have an EUI for the mock controller!
        """
        self.telemetry.state = Rotator.ControllerState(state)
        self.telemetry.offline_substate = Rotator.OfflineSubstate.AVAILABLE \
            if self.telemetry.state == Rotator.ControllerState.OFFLINE else 0
        self.telemetry.enabled_substate = Rotator.EnabledSubstate.STATIONARY \
            if self.telemetry.state == Rotator.ControllerState.ENABLED else 0
        self.log.debug(f"set_state: state={Rotator.ControllerState(self.telemetry.state)!r}; "
                       f"offline_substate={Rotator.OfflineSubstate(self.telemetry.offline_substate)}; "
                       f"enabled_substate={Rotator.EnabledSubstate(self.telemetry.enabled_substate)}")

    @abc.abstractmethod
    async def update_telemetry(self):
        """Update self.client.telemetry.
        """
        raise NotImplementedError()
