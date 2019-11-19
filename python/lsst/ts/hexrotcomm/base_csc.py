# This file is part of ts_hexapod.
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

__all__ = ["BaseCsc"]

import abc
import asyncio
import sys

from lsst.ts import salobj
from lsst.ts import hexrotcomm
from lsst.ts.idl.enums import Rotator
from . import enums
from . import constants


# Dict of controller state: CSC state.
# The names match but the numeric values do not.
# Note that Rotator and Hexapod values match, so I just picked one.
StateCscState = {
    Rotator.ControllerState.OFFLINE: salobj.State.OFFLINE,
    Rotator.ControllerState.STANDBY: salobj.State.STANDBY,
    Rotator.ControllerState.DISABLED: salobj.State.DISABLED,
    Rotator.ControllerState.ENABLED: salobj.State.ENABLED,
    Rotator.ControllerState.FAULT: salobj.State.FAULT,
}

# Dict of CSC state: controller state.
# The names match but the numeric values do not.
CscStateState = dict((value, key) for key, value in StateCscState.items())


class BaseCsc(salobj.Controller, metaclass=abc.ABCMeta):
    """Base CSC for talking to Moog hexpod or rotator controllers.

    Parameters
    ----------
    name : `str`
        Name of SAL component.
    index : `int` or `None` (optional)
        SAL component index, or 0 or None if the component is not indexed.
        A value is required if the component is indexed.
    sync_pattern : `int`
        Sync pattern sent with commands.
    CommandCode : `enum`
        Command codes.
    ConfigClass : `ctypes.Structure`
        Configuration structure.
    TelemetryClass : `ctypes.Structure`
        Telemetry structure.
    initial_state : `lsst.ts.salobj.State` or `int` (optional)
        The initial state of the CSC. Ignored (other than checking
        that it is a valid value) except in simulation mode,
        because in normal operation the initial state is the current state
        of the controller. This is provided for unit testing.
    simulation_mode : `int` (optional)
        Simulation mode. Allowed values:

        * 0: regular operation.
        * 1: simulation: use a mock low level controller.

    Notes
    -----
    **Error Codes**

    * 1: invalid data read on the telemetry socket

    This CSC is unusual in several respect:

    * It acts as a server (not a client) for a low level controller
      (because that is how the low level controller is written).
    * The low level controller maintains the summary state and detailed state
      (that's why this code inherits from Controller instead of BaseCsc).
    * The simulation mode can only be set at construction time.
    """
    def __init__(self, *,
                 name,
                 index,
                 sync_pattern,
                 CommandCode,
                 ConfigClass,
                 TelemetryClass,
                 initial_state=salobj.State.OFFLINE,
                 simulation_mode=0):
        self._initial_state = salobj.State(initial_state)
        if simulation_mode not in (0, 1):
            raise ValueError(f"simulation_mode = {simulation_mode}; must be 0 or 1")
        self.simulation_mode = simulation_mode
        self.server = None
        self.CommandCode = CommandCode
        self.ConfigClass = ConfigClass
        self.TelemetryClass = TelemetryClass
        self.mock_ctrl = None
        super().__init__(name=name, index=index, do_callbacks=True)

        # Dict of enum.CommandCode: Command
        # with constants set to suitable values.
        self.commands = dict()
        for cmd in CommandCode:
            command = hexrotcomm.Command()
            command.cmd = cmd
            command.sync_pattern = sync_pattern
            self.commands[cmd] = command

        self.heartbeat_interval = 1
        self.heartbeat_task = asyncio.ensure_future(self.heartbeat_loop())

    @property
    def summary_state(self):
        """Return the current summary state as a salobj.State,
        or OFFLINE if unknown.
        """
        if self.server is None or not self.server.connected:
            return salobj.State.OFFLINE
        return StateCscState.get(int(self.server.telemetry.state), salobj.State.OFFLINE)

    async def start(self):
        await super().start()
        simulating = self.simulation_mode != 0
        host = constants.LOCAL_HOST if simulating else None
        self.server = hexrotcomm.CommandTelemetryServer(
            host=host,
            log=self.log,
            ConfigClass=self.ConfigClass,
            TelemetryClass=self.TelemetryClass,
            connect_callback=self.connect_callback,
            config_callback=self.config_callback,
            telemetry_callback=self.telemetry_callback,
            use_random_ports=simulating)
        await self.server.start_task
        if simulating:
            initial_ctrl_state = CscStateState[self._initial_state]
            self.mock_ctrl = self.make_mock_controller(initial_ctrl_state)
            await self.mock_ctrl.connect_task
        else:
            self.evt_summaryState.set_put(summaryState=salobj.State.OFFLINE)

    async def close_tasks(self):
        self.heartbeat_task.cancel()
        if self.mock_ctrl is not None:
            await self.mock_ctrl.close()
        if self.server is not None:
            await self.server.close()

    @abc.abstractmethod
    def make_mock_controller(self, initial_ctrl_state):
        """Construct and return a mock controller.

        Parameters
        ----------
        initial_ctrl_state : `int`
            Initial controller state.
        """
        raise NotImplementedError()

    def assert_commandable(self):
        """Assert that the controller is connected and has CSC commands
        enabled.
        """
        if not self.server.connected:
            raise salobj.ExpectedError("Controller is not connected")
        if not self.evt_commandableByDDS.data.state:
            raise salobj.ExpectedError("Controller has CSC commands disabled; "
                                       "use the EUI to enable CSC commands")

    def assert_summary_state(self, *allowed_states, isbefore):
        """Assert that the current summary state is as specified.

        Also checks that the controller is commandable.

        Used in do_xxx methods to check that a command is allowed.
        """
        self.assert_commandable()
        if self.summary_state not in allowed_states:
            allowed_states_str = ", ".join(repr(state) for state in allowed_states)
            if isbefore:
                msg_prefix = "Rejected: initial"
            else:
                msg_prefix = "Failed: final"
            raise salobj.ExpectedError(
                f"{msg_prefix} state is {self.summary_state!r} instead of {allowed_states_str}")

    async def run_command(self, cmd, **kwargs):
        command = self.commands[cmd]
        for name, value in kwargs.items():
            if hasattr(command, name):
                setattr(command, name, value)
            else:
                raise ValueError(f"Unknown command argument {name}")
        # Note: increment correctly wraps around
        command.counter += 1
        await self.server.put_command(command)

    # Unsupported standard CSC commnands.
    async def do_abort(self, data):
        raise salobj.ExpectedError("Unsupported command")

    async def do_setSimulationMode(self, data):
        raise salobj.ExpectedError("Unsupported command: "
                                   "simulation mode can only be set when starting the CSC.")

    async def do_setValue(self, data):
        raise salobj.ExpectedError("Unsupported command")

    # Standard CSC commnands.
    async def do_clearError(self, data):
        """Reset the FAULT state to OFFLINE.
        """
        self.assert_summary_state(salobj.State.FAULT, isbefore=True)
        # Two sequential commands are needed to clear error
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.CLEAR_ERROR)
        await asyncio.sleep(0.9)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.CLEAR_ERROR)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.OFFLINE, isbefore=False)

    async def do_disable(self, data):
        """Go from ENABLED state to DISABLED.
        """
        self.assert_summary_state(salobj.State.ENABLED, isbefore=True)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.DISABLE)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.DISABLED, isbefore=False)

    async def do_enable(self, data):
        """Go from DISABLED state to ENABLED.
        """
        self.assert_summary_state(salobj.State.DISABLED, isbefore=True)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.ENABLE)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.ENABLED, isbefore=False)

    async def do_enterControl(self, data):
        """Go from OFFLINE state, AVAILABLE offline substate to STANDBY.
        """
        self.assert_summary_state(salobj.State.OFFLINE, isbefore=True)
        if self.server.telemetry.offline_substate != Rotator.OfflineSubstate.AVAILABLE:
            raise salobj.ExpectedError(
                "Use the engineering interface to put the controller into state OFFLINE/AVAILABLE")
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.ENTER_CONTROL)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.STANDBY, isbefore=False)

    async def do_exitControl(self, data):
        """Go from STANDBY state to OFFLINE state, AVAILABLE offline substate.
        """
        self.assert_summary_state(salobj.State.STANDBY, isbefore=True)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.EXIT)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.OFFLINE, isbefore=False)

    async def do_standby(self, data):
        """Go from DISABLED state to STANDBY.

        Note: unlike standard CSCs this command will not take FAULT state
        to DISABLED. Use the clearError command to leave FAULT state.
        """
        if self.summary_state == salobj.State.FAULT:
            raise salobj.ExpectedError(
                "You must use the clearError command or the engineering user interface "
                "to clear a rotator fault.")
        self.assert_summary_state(salobj.State.DISABLED, isbefore=True)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.STANDBY)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.STANDBY, isbefore=False)

    async def do_start(self, data):
        """Go from STANDBY state to DISABLED.

        Notes
        -----
        This ignores the data, unlike the vendor's CSC code, which writes the
        supplied file name into a file on an nfs-mounted partition.
        I hope we won't need to do that, as it seems complicated.
        """
        self.assert_summary_state(salobj.State.STANDBY, isbefore=True)
        await self.run_command(cmd=self.CommandCode.SET_STATE,
                               param1=enums.SetStateParam.START)
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.DISABLED, isbefore=False)

    def assert_enabled_substate(self, substate):
        """Assert the controller is enabled and in the specified substate.
        """
        self.assert_summary_state(salobj.State.ENABLED, isbefore=True)
        if self.server.telemetry.enabled_substate != substate:
            raise salobj.ExpectedError("Low-level controller in substate "
                                       f"{self.server.telemetry.enabled_substate} "
                                       f"instead of {substate!r}")

    def connect_callback(self, server):
        """Called when the server's command or telemetry sockets
        connect or disconnect.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        self.evt_connected.set_put(command=self.server.command_connected,
                                   telemetry=self.server.telemetry_connected)

    @abc.abstractmethod
    def config_callback(self, server):
        """Called when the TCP/IP controller outputs configuration.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def telemetry_callback(self, server):
        """Called when the TCP/IP controller outputs telemetry.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        raise NotImplementedError()

    async def heartbeat_loop(self):
        """Output heartbeat at regular intervals.
        """
        while True:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                self.evt_heartbeat.put()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # don't use the log because it also uses DDS messaging
                print(f"Heartbeat output failed: {e!r}", file=sys.stderr)
