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

__all__ = ["SimpleCsc"]

from lsst.ts import salobj
from lsst.ts import hexrotcomm
from lsst.ts.idl.enums import Rotator
from . import simple_mock_controller


class SimpleCsc(hexrotcomm.BaseCsc):
    """Simple CSC to talk to SimpleMockController.

    This is based on the Rotator CSC but only supports a small subset
    off commands, events and telemetry. See Notes for details.
    The positionSet command sets the cmd_position and curr_position
    telemetry fields, then the controller slowly increments curr_position.

    Parameters
    ----------
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

    Supported commands:

    * All standard state transition commands and clearError
    * configureVelocity
    * positionSet

    Supported events:

    * controllerState
    * connected
    * summaryState
    * settingsApplied

    Supported telemetry:

    * Application
    """

    def __init__(self, initial_state=salobj.State.OFFLINE, simulation_mode=0):
        self._initial_state = salobj.State(initial_state)
        if simulation_mode not in (0, 1):
            raise ValueError(f"simulation_mode = {simulation_mode}; must be 0 or 1")
        self.simulation_mode = simulation_mode
        self.server = None
        self.mock_ctrl = None
        # Set this to 2 when trackStart is called, then decrement
        # when telemetry is received. If > 0 or enabled_substate is
        # SLEWING_OR_TRACKING then allow the track command.
        # This solves the problem of allowing the track command
        # immediately after the trackStart, before telemetry is received.
        self._tracking_started_telemetry_counter = 0
        self._prev_flags_tracking_success = False
        self._prev_flags_tracking_lost = False

        super().__init__(name="Rotator",
                         index=0,
                         sync_pattern=hexrotcomm.SIMPLE_SYNC_PATTERN,
                         CommandCode=simple_mock_controller.SimpleCommandCode,
                         ConfigClass=simple_mock_controller.SimpleConfig,
                         TelemetryClass=simple_mock_controller.SimpleTelemetry,
                         initial_state=initial_state,
                         simulation_mode=simulation_mode)

    async def do_configureVelocity(self, data):
        """Specify the velocity limit.
        """
        self.assert_enabled_substate(Rotator.EnabledSubstate.STATIONARY)
        if data.vlimit <= 0:
            raise salobj.ExpectedError(f"vlimit={data.vlimit} must be > 0")
        await self.run_command(cmd=simple_mock_controller.SimpleCommandCode.CONFIG_VEL,
                               param1=data.vlimit)

    async def do_positionSet(self, data):
        """Specify a position.
        """
        self.assert_enabled_substate(Rotator.EnabledSubstate.STATIONARY)
        if not self.server.config.min_position <= data.angle <= self.server.config.max_position:
            raise salobj.ExpectedError(f"angle {data.angle} not in range "
                                       f"[{self.server.config.min_position}, "
                                       f"{self.server.config.max_position}]")
        await self.run_command(cmd=simple_mock_controller.SimpleCommandCode.POSITION_SET,
                               param1=data.angle)

    async def do_configureAcceleration(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_move(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_moveConstantVelocity(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_stop(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_test(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_track(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_trackStart(self, data):
        raise salobj.ExpectedError("Not implemented")

    async def do_velocitySet(self, data):
        raise salobj.ExpectedError("Not implemented")

    def config_callback(self, server):
        """Called when the TCP/IP controller outputs configuration.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        self.evt_settingsApplied.set_put(
            positionAngleUpperLimit=server.config.max_position,
            velocityLimit=server.config.max_velocity,
            accelerationLimit=0,
            positionAngleLowerLimit=server.config.min_position,
            followingErrorThreshold=0,
            trackingSuccessPositionThreshold=0,
            trackingLostTimeout=0,
        )
        self.evt_commandableByDDS.set_put(state=True)

    def telemetry_callback(self, server):
        """Called when the TCP/IP controller outputs telemetry.

        Parameters
        ----------
        server : `CommandTelemetryServer`
            TCP/IP server.
        """
        self.evt_summaryState.set_put(summaryState=self.summary_state)
        # Strangely telemetry.state, offline_substate and enabled_substate
        # are all floats from the controller. But they should only have
        # integer value, so I output them as integers.
        self.evt_controllerState.set_put(controllerState=int(server.telemetry.state),
                                         offlineSubstate=int(server.telemetry.offline_substate),
                                         enabledSubstate=int(server.telemetry.enabled_substate))
        self.evt_commandableByDDS.set_put(
            state=bool(server.telemetry.application_status & Rotator.ApplicationStatus.DDS_COMMAND_SOURCE),
        )

        self.tel_Application.set_put(
            Demand=server.telemetry.cmd_position,
            Position=server.telemetry.curr_position,
            Error=server.telemetry.curr_position - server.telemetry.cmd_position,
        )

    def make_mock_controller(self, initial_ctrl_state):
        return simple_mock_controller.SimpleMockController(
            log=self.log,
            host=self.server.host,
            initial_state=initial_ctrl_state,
            command_port=self.server.command_port,
            telemetry_port=self.server.telemetry_port)
