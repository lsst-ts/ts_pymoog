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
import logging
import unittest

import asynctest

from lsst.ts.idl.enums import Rotator
from lsst.ts import hexrotcomm

# Standard timeout for TCP/IP messages (sec).
STD_TIMEOUT = 0.01

# Time to wait for a reconnection attempt (sec).
RECONNECT_TIMEOUT = hexrotcomm.SimpleMockController.connect_retry_interval*3 + STD_TIMEOUT


class CommandTelemetryServerTestCase(asynctest.TestCase):
    """Test CommandTelemetryServer by connecting it to a SimpleMockController.
    """
    async def setUp(self):
        # Queue of (command_connected, telemetry_connected) filled by
        # the self.connect_callback
        self.connect_queue = asyncio.Queue()

        # Queue of config filled by the self.config_callback
        self.config_queue = asyncio.Queue()

        # List of telemetry, filled by the server's telemetry_callback.
        # This is a list (not a queue like connect_queue and config_queue)
        # because we almost always want the latest value
        self.telemetry_list = []

        # A future that is set done by self.telemetry_callback.
        # Replace it with a new Future to wait for new telemetry.
        self.callbacks_raise = False

        log = logging.getLogger()
        log.setLevel(logging.INFO)
        log.addHandler(logging.StreamHandler())
        self.server = hexrotcomm.CommandTelemetryServer(host=hexrotcomm.LOCAL_HOST,
                                                        log=log,
                                                        ConfigClass=hexrotcomm.SimpleConfig,
                                                        TelemetryClass=hexrotcomm.SimpleTelemetry,
                                                        connect_callback=self.connect_callback,
                                                        config_callback=self.config_callback,
                                                        telemetry_callback=self.telemetry_callback,
                                                        use_random_ports=True)
        await self.server.start_task
        # Mock controller; None until make_controller is called
        self.mock_ctrl = None
        # List of asyncio stream writers to close in tearDown
        self.writers = []

    async def tearDown(self):
        if self.mock_ctrl is not None:
            await self.mock_ctrl.close()
        for writer in self.writers:
            writer.close()
        await self.server.close()

    async def make_controller(self, check_connected=True):
        """Make a simple controller and wait for it to connect.

        Sets attribute self.mock_ctrl to the controller.
        """
        if check_connected:
            await self.assert_next_connected(command=False, telemetry=False)
        self.mock_ctrl = hexrotcomm.SimpleMockController(log=self.server.log,
                                                         command_port=self.server.command_port,
                                                         telemetry_port=self.server.telemetry_port,
                                                         initial_state=Rotator.ControllerState.ENABLED)
        await self.mock_ctrl.connect_task
        await self.server.wait_connected()
        if check_connected:
            await self.assert_next_connected(command=True, telemetry=True, skip=1)
        self.initial_min_position = self.mock_ctrl.config.min_position
        self.initial_max_position = self.mock_ctrl.config.max_position
        self.initial_max_velocity = self.mock_ctrl.config.max_velocity
        self.initial_cmd_position = self.mock_ctrl.telemetry.cmd_position

    def connect_callback(self, server):
        print(f"connect_callback: command_connected={server.command_connected}, "
              f"telemetry_connected={server.telemetry_connected}")
        self.connect_queue.put_nowait((server.command_connected, server.telemetry_connected))
        if self.callbacks_raise:
            raise RuntimeError("connect_callback raising because self.callbacks_raise is true")

    def config_callback(self, server):
        self.config_queue.put_nowait(server.config)
        if self.callbacks_raise:
            raise RuntimeError("config_callback raising because self.callbacks_raise is true")

    def telemetry_callback(self, server):
        self.telemetry_list.append(server.telemetry)
        if self.callbacks_raise:
            raise RuntimeError("telemetry_callback raising because self.callbacks_raise is true")

    async def open_command_socket(self):
        """Make a controller command socket. Return reader, writer.

        Keeps track of the writer and closes it in tearDown if necessary.
        """
        connect_coro = asyncio.open_connection(host=hexrotcomm.LOCAL_HOST,
                                               port=self.server.command_port)
        reader, writer = await asyncio.wait_for(connect_coro, timeout=STD_TIMEOUT)
        self.writers.append(writer)
        return reader, writer

    async def open_telemetry_socket(self):
        """Make a controller telemetry socket. Return reader, writer.

        Keeps track of the writer and closes it in tearDown if necessary.
        """
        connect_coro = asyncio.open_connection(host=hexrotcomm.LOCAL_HOST,
                                               port=self.server.telemetry_port)
        reader, writer = await asyncio.wait_for(connect_coro, timeout=STD_TIMEOUT)
        self.writers.append(writer)
        return reader, writer

    async def next_config(self):
        """Wait for next config."""
        return await asyncio.wait_for(self.config_queue.get(), timeout=STD_TIMEOUT)

    async def assert_next_connected(self, command, telemetry, skip=0, timeout=STD_TIMEOUT):
        """Assert results of next connect_callback.

        Parameters
        ----------
        command : `bool`
            Is the command socket connected?
        telemetry : `bool`
            Should the telemetry socket be connected?
        skip : `int` (optional)
            Number of callbacks to skip. Useful when connecting
            or disconnecting both sockets since you don't know
            which will open or close first.
        timeout : `float`
            Time to wait for connect_callback (seconds).
        """
        self.assertGreaterEqual(skip, 0)
        for n in range(skip+1):
            next_command, next_telemetry = await asyncio.wait_for(self.connect_queue.get(),
                                                                  timeout=timeout)
        self.assertEqual(command, next_command)
        self.assertEqual(telemetry, next_telemetry)

    async def assert_connected(self, command, telemetry):
        """Assert that server command and/or telemetry sockets are connected.
        """
        self.assertEqual(self.server.command_connected, command)
        self.assertEqual(self.server.telemetry_connected, telemetry)
        self.assertEqual(self.server.connected, command and telemetry)

    async def test_initial_conditions(self):
        await self.assert_connected(command=False, telemetry=False)
        self.assertEqual(len(self.telemetry_list), 0)
        await self.make_controller()
        self.assertTrue(self.mock_ctrl.connected)
        self.assertTrue(self.mock_ctrl.command_connected)
        self.assertTrue(self.mock_ctrl.telemetry_connected)
        config = await self.next_config()
        self.assertEqual(config.min_position, self.initial_min_position)
        self.assertEqual(config.max_position, self.initial_max_position)
        telemetry = await self.server.next_telemetry()
        await self.assert_connected(command=True, telemetry=True)
        self.assertEqual(telemetry.cmd_position, self.initial_cmd_position)
        self.assertGreaterEqual(telemetry.curr_position, self.initial_cmd_position)

        # extra calls to server.start should fail
        with self.assertRaises(RuntimeError):
            await self.server.start()

    async def test_controller_reconnect(self):
        await self.make_controller()

        await self.server.command_server.close_client()
        await self.assert_next_connected(command=False, telemetry=True)
        await self.assert_next_connected(command=True, telemetry=True, timeout=RECONNECT_TIMEOUT)

        await self.server.telemetry_server.close_client()
        await self.assert_next_connected(command=True, telemetry=False)
        await self.assert_next_connected(command=True, telemetry=True, timeout=RECONNECT_TIMEOUT)

    async def test_move_command(self):
        await self.make_controller()
        config = await self.next_config()
        self.assertEqual(config.min_position, self.initial_min_position)
        self.assertEqual(config.max_position, self.initial_max_position)
        telemetry = await self.server.next_telemetry()
        self.assertEqual(telemetry.cmd_position, self.initial_cmd_position)

        expected_counter = 0
        for good_position in (
            self.initial_min_position,
            self.initial_max_position,
            (self.initial_min_position + self.initial_max_position) / 2,
        ):
            expected_counter += 1
            with self.subTest(good_position=good_position):
                await self.check_move(cmd_position=good_position,
                                      expected_counter=expected_counter)
        last_good_position = good_position

        for bad_position in (
            self.initial_min_position - 0.001,
            self.initial_max_position + 0.001,
        ):
            expected_counter += 1
            with self.subTest(bad_position=bad_position):
                await self.check_move(cmd_position=bad_position,
                                      expected_counter=expected_counter,
                                      expected_position=last_good_position)

        # set position commands should not trigger configuration output.
        with self.assertRaises(asyncio.TimeoutError):
            await self.next_config()

    async def test_bad_frame_id(self):
        """Test that sending a header with an unknown frame ID causes
        the server to close the telemetry connection.
        """
        await self.assert_next_connected(command=False, telemetry=False)
        reader, writer = await self.open_telemetry_socket()
        await self.assert_next_connected(command=False, telemetry=True)
        bad_frame_id = hexrotcomm.SimpleTelemetry().FRAME_ID + hexrotcomm.SimpleConfig().FRAME_ID
        header = hexrotcomm.Header()
        header.frame_id = bad_frame_id
        await hexrotcomm.write_from(writer, header)
        await self.assert_next_connected(command=False, telemetry=False)

    async def test_put_command_errors(self):
        """Test expected failures in CommandTelemetryServer.put_command.
        """
        command = hexrotcomm.Command()
        command.code = hexrotcomm.SimpleCommandCode.MOVE
        command.param1 = 0

        # Should fail if not connected
        await self.assert_connected(command=False, telemetry=False)
        with self.assertRaises(RuntimeError):
            await self.server.put_command(command)

        await self.make_controller()

        # Should fail if not an instance of Command
        with self.assertRaises(ValueError):
            await self.server.put_command("not a command")

        # Should fail if server is not started
        self.server.start_task = asyncio.Future()
        with self.assertRaises(RuntimeError):
            await self.server.put_command(command)

    async def test_connect_disconnect(self):
        """Test server behavior when the controller disconnects and
        reconnects telemetry and command sockets.
        """
        await self.assert_next_connected(command=False, telemetry=False)

        # Connect and disconnect the command socket
        command_reader, command_writer = await self.open_command_socket()
        await self.assert_next_connected(command=True, telemetry=False)

        await self.check_extra_connection(self.open_command_socket)
        command_writer.close()
        await self.assert_next_connected(command=False, telemetry=False)

        # Connect and disconnect the telemetry socket
        telemetry_reader, telemetry_writer = await self.open_telemetry_socket()
        await self.assert_next_connected(command=False, telemetry=True)

        await self.check_extra_connection(self.open_telemetry_socket)

        telemetry_writer.close()
        await self.assert_next_connected(command=False, telemetry=False)

        # Connect both, then disconnect each
        command_reader, command_writer = await self.open_command_socket()
        await self.assert_next_connected(command=True, telemetry=False)
        telemetry_reader, telemetry_writer = await self.open_telemetry_socket()
        await self.assert_next_connected(command=True, telemetry=True)

        await self.check_extra_connection(self.open_command_socket)
        await self.check_extra_connection(self.open_telemetry_socket)

        command_writer.close()
        await self.assert_next_connected(command=False, telemetry=True)
        telemetry_writer.close()
        await self.assert_next_connected(command=False, telemetry=False)

    async def test_failed_callbacks(self):
        """Check that the server gracefully handles callback functions
        that raise an exception.
        """
        self.callbacks_raise = True
        await self.make_controller()
        await self.server.next_telemetry()
        config = await self.next_config()
        self.assertEqual(config.min_position, self.initial_min_position)
        self.assertEqual(config.max_position, self.initial_max_position)
        self.assertGreaterEqual(len(self.telemetry_list), 1)
        await self.assert_connected(command=True, telemetry=True)

    async def check_extra_connection(self, connect_coroutine):
        """Check that the server rejects more than one connection.

        Parameters
        ----------
        connect_coroutine : awaitable
            A coroutine opens a connection to the server command
            or telemetry port.
            It must take no arguments and must return an async stream
            reader and writer.
        """
        rejected_reader, rejected_writer = await connect_coroutine()
        await asyncio.wait_for(rejected_reader.read(1000), STD_TIMEOUT)
        self.assertTrue(rejected_reader.at_eof())
        rejected_writer.close()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.connect_queue.get(), timeout=STD_TIMEOUT)

    async def check_move(self, cmd_position, expected_counter, expected_position=None):
        """Command a position and check the result.

        If the commanded position is in bounds then the telemetry
        should update to match. If not, then the command should be
        ignored and the reported position will not change.

        Parameters
        ----------
        cmd_position : `float`
            Commanded position.
        expected_position : `float` (optional)
            Position expected from telemetry. If None then use ``cmd_position``

        """
        if expected_position is None:
            expected_position = cmd_position

        command = hexrotcomm.Command()
        command.code = hexrotcomm.SimpleCommandCode.MOVE
        command.param1 = cmd_position
        await self.server.put_command(command)
        self.assertEqual(command.counter, expected_counter)

        telemetry = await self.server.next_telemetry()
        self.assertEqual(telemetry.cmd_position, expected_position)


if __name__ == "__main__":
    unittest.main()
