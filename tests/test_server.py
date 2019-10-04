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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
import logging
import time
import unittest

import asynctest

from lsst.ts import pymoog

STD_TIMEOUT = 0.1  # standard timeout for TCP/IP messages (sec)


class ServerTestCase(asynctest.TestCase):
    """Test a Server by connecting it to a SimpleMockController.
    """
    async def setUp(self):
        self.initial_position = 73
        self.initial_min_position = -102
        self.initial_max_position = 101
        self.config_list = []
        self.telemetry_list = []
        self.config_future = asyncio.Future()
        self.telemetry_future = asyncio.Future()

        log = logging.getLogger()
        log.setLevel(logging.DEBUG)
        log.addHandler(logging.StreamHandler())
        self.server = pymoog.Server(host=pymoog.LOCAL_HOST,
                                    log=log,
                                    ConfigClass=pymoog.SimpleConfig,
                                    TelemetryClass=pymoog.SimpleTelemetry,
                                    config_callback=self.config_callback,
                                    telemetry_callback=self.telemetry_callback)

        config = pymoog.SimpleConfig()
        config.min_position = self.initial_min_position
        config.max_position = self.initial_max_position
        telemetry = pymoog.SimpleTelemetry()
        telemetry.position = self.initial_position
        self.mock_ctrl = pymoog.SimpleMockController(log=log, config=config, telemetry=telemetry)
        await asyncio.gather(self.server.start_task, self.mock_ctrl.connect_task)

    async def tearDown(self):
        await asyncio.gather(self.mock_ctrl.close(), self.server.close())

    def config_callback(self, server):
        print("config_callback")
        self.config_list.append(server.config)
        if not self.config_future.done():
            self.config_future.set_result(None)

    def telemetry_callback(self, server):
        self.telemetry_list.append(server.telemetry)
        if not self.telemetry_future.done():
            self.telemetry_future.set_result(None)

    async def next_config(self):
        """Wait for next telemetry."""
        self.config_future = asyncio.Future()
        await asyncio.wait_for(self.config_future, timeout=STD_TIMEOUT)
        return self.config_list[-1]

    async def next_telemetry(self):
        """Wait for next telemetry."""
        self.telemetry_future = asyncio.Future()
        timeout = self.mock_ctrl.telemetry_interval + STD_TIMEOUT
        await asyncio.wait_for(self.telemetry_future, timeout=timeout)
        return self.telemetry_list[-1]

    async def test_initial_conditions(self):
        telemetry = await self.next_telemetry()
        self.assertEqual(len(self.config_list), 1)
        config = self.config_list[0]
        self.assertEqual(config.min_position, self.initial_min_position)
        self.assertEqual(config.max_position, self.initial_max_position)
        self.assertEqual(telemetry.position, self.initial_position)
        self.assertGreaterEqual(time.time(), telemetry.time)

    async def test_set_position_command(self):
        await self.next_telemetry()
        self.assertEqual(len(self.config_list), 1)

        for good_position in (
            self.initial_min_position,
            self.initial_max_position,
            (self.initial_min_position + self.initial_max_position) / 2,
        ):
            await self.check_set_position(cmd_position=good_position)
        last_good_position = good_position

        for bad_position in (
            self.initial_min_position - 0.001,
            self.initial_max_position + 0.001,
        ):
            await self.check_set_position(cmd_position=bad_position,
                                          desired_position=last_good_position)

    async def test_config_min_max_position_command(self):
        await self.next_telemetry()
        self.assertEqual(len(self.config_list), 1)

        # Check valid limits (min < max).
        for good_min_position, good_max_position in (
            (-56.001, -56),
            (200, 200.001),
            (-105, 63),
        ):
            await self.check_config_min_max_position_command(min_position=good_min_position,
                                                             max_position=good_max_position)

        # Check invalid limits (min >= max).
        for bad_min_position, bad_max_position in (
            (-56, -56),
            (-56, -56.001),
            (200, 200),
            (200.001, 200),
            (47, -3.14),
        ):
            await self.check_config_min_max_position_command(min_position=bad_min_position,
                                                             max_position=bad_max_position)

    async def check_set_position(self, cmd_position, desired_position=None):
        """Command a position and check the result.

        If the commanded position is in bounds then the telemetry
        should update to match. If not, then the command should be
        ignored and the reported position will not change.

        Parameters
        ----------
        cmd_position : `float`
            Commanded position.
        desired_position : `float` (optional)
            Desired position. If None then use ``cmd_position``

        """
        initial_num_config = len(self.config_list)
        if desired_position is None:
            desired_position = cmd_position

        command = pymoog.Command()
        command.cmd = pymoog.SimpleCommandType.SET_POSITION
        command.param1 = cmd_position
        await self.server.put_command(command)

        for i in range(2):
            # ask multiple times to avoid a race condition
            telemetry = await self.next_telemetry()
        self.assertEqual(telemetry.position, desired_position)
        self.assertGreaterEqual(time.time(), telemetry.time)

        # The command should not trigger a configuration output
        self.assertEqual(len(self.config_list), initial_num_config)

    async def check_config_min_max_position_command(self, min_position, max_position):
        config_task = asyncio.create_task(self.next_config())

        command = pymoog.Command()
        command.cmd = pymoog.SimpleCommandType.CONFIG_MIN_MAX_POSITION
        command.param1 = min_position
        command.param2 = max_position
        await self.server.put_command(command)

        if min_position < max_position:
            config = await config_task
            self.assertEqual(config.min_position, min_position)
            self.assertEqual(config.max_position, max_position)
        else:
            with self.assertRaises(asyncio.TimeoutError):
                await config_task


if __name__ == "__main__":
    unittest.main()
