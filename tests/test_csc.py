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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import asyncio
import pathlib
import unittest

import asynctest

from lsst.ts import salobj
from lsst.ts import hexrotcomm
from lsst.ts.idl.enums import MTRotator

STD_TIMEOUT = 5  # timeout for command ack

LOCAL_CONFIG_DIR = pathlib.Path(__file__).parent / "data" / "config"


class TestSimpleCsc(hexrotcomm.BaseCscTestCase, asynctest.TestCase):
    def basic_make_csc(
        self, config_dir=None, initial_state=salobj.State.OFFLINE, simulation_mode=1
    ):
        return hexrotcomm.SimpleCsc(
            initial_state=initial_state,
            simulation_mode=simulation_mode,
            config_dir=config_dir,
        )

    async def test_constructor_errors(self):
        for bad_initial_state in (0, max(salobj.State) + 1):
            with self.assertRaises(ValueError):
                hexrotcomm.SimpleCsc(initial_state=bad_initial_state, simulation_mode=1)

        for bad_simulation_mode in (-1, 2):
            with self.assertRaises(ValueError):
                hexrotcomm.SimpleCsc(
                    initial_state=bad_initial_state,
                    simulation_mode=bad_simulation_mode,
                )

        with self.assertRaises(ValueError):
            hexrotcomm.SimpleCsc(
                initial_state=salobj.State.OFFLINE,
                simulation_mode=1,
                config_dir="no_such_directory",
            )

        for bad_initial_state in salobj.State:
            if bad_initial_state == salobj.State.OFFLINE:
                continue
            with self.assertRaises(ValueError):
                hexrotcomm.SimpleCsc(
                    initial_state=bad_initial_state, simulation_mode=0,
                )

    async def test_invalid_config(self):
        async with self.make_csc(
            initial_state=salobj.State.STANDBY,
            simulation_mode=1,
            config_dir=LOCAL_CONFIG_DIR,
        ):

            # Try a config file that has invalid data.
            # The command should fail and the summary state remain in STANDBY.
            with salobj.assertRaisesAckError():
                await self.remote.cmd_start.set_start(
                    settingsToApply="invalid.yaml", timeout=STD_TIMEOUT
                )
            self.assertEqual(self.csc.summary_state, salobj.State.STANDBY)

            # Now try a valid config file
            await self.remote.cmd_start.set_start(
                settingsToApply="valid.yaml", timeout=STD_TIMEOUT
            )
            self.assertEqual(self.csc.summary_state, salobj.State.DISABLED)

    async def move_sequentially(self, *positions, delay=None):
        """Move sequentially to different positions, in order to test
        `BaseCsc.run_multiple_commands`.

        Warning: assumes that the CSC is enabled and the positions
        are in bounds.

        Parameters
        ----------
        positions : `List` [`double`]
            Positions to move to, in order (deg).
        delay : `float` (optional)
            Delay between commands (sec); or no delay if `None`.
            Only intended for unit testing.
        """
        commands = []
        for position in positions:
            command = self.csc.make_command(
                code=hexrotcomm.SimpleCommandCode.MOVE, param1=position
            )
            commands.append(command)
        await self.csc.run_multiple_commands(*commands, delay=delay)

    async def test_move(self):
        """Test the move command.
        """
        destination = 2  # a small move so the test runs quickly
        async with self.make_csc(initial_state=salobj.State.ENABLED, simulation_mode=1):
            await self.assert_next_summary_state(salobj.State.ENABLED)
            await self.assert_next_sample(
                topic=self.remote.evt_controllerState,
                controllerState=MTRotator.ControllerState.ENABLED,
            )
            data = await self.remote.tel_application.next(
                flush=True, timeout=STD_TIMEOUT
            )
            self.assertAlmostEqual(data.demand, 0)
            await self.remote.cmd_move.set_start(
                position=destination, timeout=STD_TIMEOUT
            )
            data = await self.remote.tel_application.next(
                flush=True, timeout=STD_TIMEOUT
            )
            self.assertAlmostEqual(data.demand, destination)

    async def test_run_multiple_commands(self):
        """Test BaseCsc.run_multiple_commands.
        """
        target_positions = (1, 2, 3)  # Small moves so the test runs quickly
        async with self.make_csc(initial_state=salobj.State.ENABLED, simulation_mode=1):
            await self.assert_next_sample(
                topic=self.remote.evt_controllerState,
                controllerState=MTRotator.ControllerState.ENABLED,
            )
            telemetry_delay = self.csc.mock_ctrl.telemetry_interval * 3

            # Record demand positions from the `application` telemetry topic.
            demand_positions = []

            def application_callback(data):
                if data.demand not in demand_positions:
                    demand_positions.append(data.demand)

            self.remote.tel_application.callback = application_callback

            # Wait for initial telemetry.
            await asyncio.sleep(telemetry_delay)

            # Start moving to the specified positions
            task1 = asyncio.ensure_future(
                self.move_sequentially(*target_positions, delay=telemetry_delay)
            )
            # Give this task a chance to start running
            await asyncio.sleep(0.01)

            # Try to move to yet another position; this should be delayed
            # until the first set of moves is finished.
            other_move = self.csc.cmd_move.DataType()
            other_move.position = 1 + max(*target_positions)
            await self.csc.do_move(other_move)

            # task1 should have finished before the do_move command.
            self.assertTrue(task1.done())

            # Wait for final telemetry.
            await asyncio.sleep(telemetry_delay)

            expected_positions = [0] + list(target_positions) + [other_move.position]
            self.assertEqual(expected_positions, demand_positions)

    async def test_standard_state_transitions(self):
        async with self.make_csc(initial_state=salobj.State.STANDBY, simulation_mode=1):
            await self.check_standard_state_transitions(enabled_commands=("move",))

            # Check that the fault method is not implemented
            with self.assertRaises(NotImplementedError):
                self.csc.fault(code=1, report="this should raise NotImplementedError")


if __name__ == "__main__":
    unittest.main()
