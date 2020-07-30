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

from lsst.ts import salobj
from lsst.ts.idl.enums import Rotator
from . import enums
from . import constants
from . import structs
from . import command_telemetry_server


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


class BaseCsc(salobj.ConfigurableCsc, metaclass=abc.ABCMeta):
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
        Command codes supported by the low-level controller.
        Must include an item ``SET_STATE``, the only command code
        used by this class.
    ConfigClass : `ctypes.Structure`
        Configuration structure.
    TelemetryClass : `ctypes.Structure`
        Telemetry structure.
    schema_path : `str` or `pathlib.Path`
        Path to a schema file used to validate configuration files
        The recommended path is ``<package_root>/"schema"/f"{name}.yaml"``
        for example:

            schema_path = pathlib.Path(__file__).resolve().parents[4] \
                / "schema" / f"{name}.yaml"
    config_dir : `str`, optional
        Directory of configuration files, or None for the standard
        configuration directory (obtained from `_get_default_config_dir`).
        This is provided for unit testing.
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

    * It acts as a server (not a client) for a low level controller,
      because that is how the low level controller is written.
    * The low level controller maintains the summary state.
      As a result this code has to override some methods of its base class,
      and it cannot allow TCP/IP communication parameters to be part of
      the configuration specified in the ``start`` command.
    """

    def __init__(
        self,
        *,
        name,
        index,
        sync_pattern,
        CommandCode,
        ConfigClass,
        TelemetryClass,
        schema_path,
        config_dir=None,
        initial_state=salobj.State.OFFLINE,
        simulation_mode=0,
    ):
        # Check the value of initial_state, then ignore it if not simulating
        initial_state = salobj.State(initial_state)
        if simulation_mode not in (0, 1):
            raise ValueError(f"simulation_mode = {simulation_mode}; must be 0 or 1")
        if simulation_mode == 0:
            # Normal mode: start in initial state OFFLINE,
            # then when connected to the low-level controller,
            # report the state of the low-level controller.
            initial_state = salobj.State.OFFLINE
        self.server = None
        self.CommandCode = CommandCode
        self.ConfigClass = ConfigClass
        self.TelemetryClass = TelemetryClass
        self.sync_pattern = sync_pattern
        self.mock_ctrl = None
        self._command_lock = asyncio.Lock()
        super().__init__(
            name=name,
            index=index,
            schema_path=schema_path,
            config_dir=config_dir,
            initial_state=initial_state,
            simulation_mode=simulation_mode,
        )
        # start needs to know the simulation mode before
        # super().start() sets it.
        self.evt_simulationMode.set(mode=simulation_mode)

    @staticmethod
    def get_config_pkg():
        return "ts_config_ocs"

    @property
    def summary_state(self):
        """Return the current summary state as a salobj.State,
        or OFFLINE if unknown.
        """
        if self.server is not None and self.server.connected:
            return StateCscState.get(
                int(self.server.telemetry.state), salobj.State.OFFLINE
            )
        elif not self.start_task.done():
            # Starting up; return the initial summary state
            return super().summary_state
        # Disconnected, return OFFLINE
        return salobj.State.OFFLINE

    async def start(self):
        await super().start()
        simulating = self.simulation_mode != 0
        host = constants.LOCAL_HOST if simulating else None
        self.server = command_telemetry_server.CommandTelemetryServer(
            host=host,
            log=self.log,
            ConfigClass=self.ConfigClass,
            TelemetryClass=self.TelemetryClass,
            connect_callback=self.connect_callback,
            config_callback=self.config_callback,
            telemetry_callback=self.telemetry_callback,
            use_random_ports=simulating,
        )
        await self.server.start_task
        if simulating:
            initial_ctrl_state = CscStateState[self.summary_state]
            self.mock_ctrl = self.make_mock_controller(initial_ctrl_state)
            await self.mock_ctrl.connect_task

    async def close_tasks(self):
        await super().close_tasks()
        if self.mock_ctrl is not None:
            await self.mock_ctrl.close()
        if self.server is not None:
            await self.server.close()

    async def configure(self, config):
        pass

    async def implement_simulation_mode(self, simulation_mode):
        # Test the value of simulation_mode in the constructor, instead.
        pass

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
            raise salobj.ExpectedError(
                "Controller has CSC commands disabled; "
                "use the EUI to enable CSC commands"
            )

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
                f"{msg_prefix} state is {self.summary_state!r} instead of {allowed_states_str}"
            )

    def make_command(
        self, code, param1=0, param2=0, param3=0, param4=0, param5=0, param6=0
    ):
        """Make a command from the command identifier and keyword arguments.

        Used to make commands for `run_multiple_commands`.

        Parameters
        ----------
        code : ``CommandCode``
            Command to run.
        param1, param2, param3, param4, param5, param6: `double`
            Command parameters. The meaning of these parameters
            depends on the command code.

        Returns
        -------
        command : `Command`
            The command. Note that the ``counter`` field is 0;
            it is set by `CommandTelemetryServer.put_command`.
        """
        command = structs.Command()
        command.code = self.CommandCode(code)
        command.sync_pattern = self.sync_pattern
        command.param1 = param1
        command.param2 = param2
        command.param3 = param3
        command.param4 = param4
        command.param5 = param5
        command.param6 = param6
        return command

    async def run_command(
        self, code, param1=0, param2=0, param3=0, param4=0, param5=0, param6=0
    ):
        """Run one command.

        Parameters
        ----------
        code : ``CommandCode``
            Command to run.
        param1, param2, param3, param4, param5, param6: `double`
            Command parameters. The meaning of these parameters
            depends on the command code.
        """
        async with self._command_lock:
            command = self.make_command(
                code,
                param1=param1,
                param2=param2,
                param3=param3,
                param4=param4,
                param5=param5,
                param6=param6,
            )
            await self.server.put_command(command)

    async def run_multiple_commands(self, *commands, delay=None):
        """Run multiple commands, without allowing other commands to run
        between them.

        Parameters
        ----------
        commands : `List` [`Command`]
            Commands to run, as constructed by `make_command`.
        delay : `float` (optional)
            Delay between commands (sec); or no delay if `None`.
            Only intended for unit testing.
        """
        async with self._command_lock:
            for command in commands:
                await self.server.put_command(command)
                if delay is not None:
                    await asyncio.sleep(delay)

    # Standard CSC commands.
    async def do_clearError(self, data):
        """Reset the FAULT state to STANDBY.
        """
        self.assert_summary_state(salobj.State.FAULT, isbefore=True)
        # Two sequential commands are needed to clear error
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.CLEAR_ERROR
        )
        await asyncio.sleep(0.9)
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.CLEAR_ERROR
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.STANDBY, isbefore=False)

    async def do_disable(self, data):
        """Go from ENABLED state to DISABLED.
        """
        self.assert_summary_state(salobj.State.ENABLED, isbefore=True)
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.DISABLE
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.DISABLED, isbefore=False)

    async def do_enable(self, data):
        """Go from DISABLED state to ENABLED.
        """
        self.assert_summary_state(salobj.State.DISABLED, isbefore=True)
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.ENABLE
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.ENABLED, isbefore=False)

    async def do_enterControl(self, data):
        """Go from OFFLINE state, AVAILABLE offline substate to STANDBY.
        """
        self.assert_summary_state(salobj.State.OFFLINE, isbefore=True)
        if self.server.telemetry.offline_substate != Rotator.OfflineSubstate.AVAILABLE:
            raise salobj.ExpectedError(
                "Use the engineering interface to put the controller into state OFFLINE/AVAILABLE"
            )
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.ENTER_CONTROL
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.STANDBY, isbefore=False)

    async def do_exitControl(self, data):
        """Go from STANDBY state to OFFLINE state, AVAILABLE offline substate.
        """
        self.assert_summary_state(salobj.State.STANDBY, isbefore=True)
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.EXIT
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.OFFLINE, isbefore=False)

    async def do_standby(self, data):
        """Go from DISABLED state to STANDBY.

        Note: use the clearError command to go from FAULT to STANDBY.
        """
        if self.summary_state == salobj.State.FAULT:
            raise salobj.ExpectedError(
                "You must use the clearError command or the engineering user interface "
                "to clear a rotator fault."
            )
        self.assert_summary_state(salobj.State.DISABLED, isbefore=True)
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.STANDBY
        )
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
        await self.run_command(
            code=self.CommandCode.SET_STATE, param1=enums.SetStateParam.START
        )
        await self.server.next_telemetry()
        self.assert_summary_state(salobj.State.DISABLED, isbefore=False)

    def assert_enabled_substate(self, substate):
        """Assert the controller is enabled and in the specified substate.
        """
        self.assert_summary_state(salobj.State.ENABLED, isbefore=True)
        if self.server.telemetry.enabled_substate != substate:
            raise salobj.ExpectedError(
                "Low-level controller in substate "
                f"{self.server.telemetry.enabled_substate} "
                f"instead of {substate!r}"
            )

    def connect_callback(self, server):
        """Called when the server's command or telemetry sockets
        connect or disconnect.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        self.evt_connected.set_put(
            command=self.server.command_connected,
            telemetry=self.server.telemetry_connected,
        )

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
