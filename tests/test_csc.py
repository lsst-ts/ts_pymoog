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

import unittest

import asynctest

from lsst.ts import salobj
from lsst.ts import hexrotcomm
from lsst.ts.idl.enums import Rotator

STD_TIMEOUT = 5  # timeout for command ack


class TestSimpleCsc(hexrotcomm.BaseCscTestCase, asynctest.TestCase):
    def basic_make_csc(self, initial_state=salobj.State.OFFLINE, simulation_mode=1):
        return hexrotcomm.SimpleCsc(initial_state=initial_state, simulation_mode=simulation_mode)

    async def test_configure_velocity(self):
        """Test the configureVelocity command.
        """
        await self.make_csc(initial_state=salobj.State.ENABLED)
        data = await self.remote.evt_settingsApplied.next(flush=False, timeout=STD_TIMEOUT)
        initial_limit = data.velocityLimit
        new_limit = initial_limit - 0.1
        await self.remote.cmd_configureVelocity.set_start(vlimit=new_limit, timeout=STD_TIMEOUT)
        data = await self.remote.evt_settingsApplied.next(flush=False, timeout=STD_TIMEOUT)
        self.assertAlmostEqual(data.velocityLimit, new_limit)

        for bad_vlimit in (0, -1):
            with self.subTest(bad_vlimit=bad_vlimit):
                with salobj.assertRaisesAckError(ack=salobj.SalRetCode.CMD_FAILED):
                    await self.remote.cmd_configureVelocity.set_start(vlimit=bad_vlimit, timeout=STD_TIMEOUT)

    async def test_positionSet(self):
        """Test the positionSet command.
        """
        destination = 2  # a small move so the test runs quickly
        await self.make_csc(initial_state=salobj.State.ENABLED)
        await self.assert_next_controller_state(controllerState=Rotator.ControllerState.ENABLED)
        data = await self.remote.tel_Application.next(flush=True, timeout=STD_TIMEOUT)
        self.assertAlmostEqual(data.Demand, 0)
        await self.remote.cmd_positionSet.set_start(angle=destination, timeout=STD_TIMEOUT)
        data = await self.remote.tel_Application.next(flush=True, timeout=STD_TIMEOUT)
        self.assertAlmostEqual(data.Demand, destination)

    async def test_standard_state_transitions(self):
        await self.check_standard_state_transitions(enabled_commands=("configureVelocity", "positionSet"))


if __name__ == "__main__":
    unittest.main()
