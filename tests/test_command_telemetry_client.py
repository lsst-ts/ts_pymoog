# This file is part of ts_hexrotcomm.
#
# Developed for the Rubin Observatory Telescope and Site System.
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

import pytest

from lsst.ts import tcpip
from lsst.ts.idl.enums.MTRotator import ControllerState
from lsst.ts import hexrotcomm

# Standard timeout (seconds)
STD_TIMEOUT = 5

logging.basicConfig()


class CommandTelemetryClientTestCase(unittest.IsolatedAsyncioTestCase):
    """Test CommandTelemetryClient and SimpleMockController.

    SimpleMockController is a simple subclass of abstract base class
    CommandTelemetryServer, so this tests CommandTelemetryServer as well.
    """

    async def asyncSetUp(self):
        # Queue of (command_connected, telemetry_connected) filled by
        # the self.connect_callback
        self.connect_queue = asyncio.Queue()

        # Queue of config filled by the self.config_callback
        self.config_queue = asyncio.Queue()

        # List of telemetry, filled by the server's telemetry_callback.
        # This is a list (not a queue like connect_queue and config_queue)
        # because we almost always want the latest value
        self.telemetry_list = []

        # If True then connected_callback, command_callback and
        # telemetry_callback all raise RuntimeError *after* adding
        # their argument to the appropriate queue.
        self.callbacks_raise = False

        log = logging.getLogger()
        log.setLevel(logging.INFO)
        self.client = None  # in case making mock_ctrl raises
        self.mock_ctrl = hexrotcomm.SimpleMockController(
            log=log,
            port=0,
            initial_state=ControllerState.ENABLED,
        )
        await self.mock_ctrl.start_task
        print(
            f"mock controller started; "
            f"command_port={self.mock_ctrl.command_port}; "
            f"telemetry_port={self.mock_ctrl.telemetry_port}"
        )
        self.initial_min_position = self.mock_ctrl.config.min_position
        self.initial_max_position = self.mock_ctrl.config.max_position
        self.initial_max_velocity = self.mock_ctrl.config.max_velocity
        self.initial_cmd_position = self.mock_ctrl.telemetry.cmd_position

        self.client = await self.make_client()

        # List of asyncio stream writers to close in tearDown
        self.writers = []

    async def asyncTearDown(self):
        if self.mock_ctrl is not None:
            await self.mock_ctrl.close()
        for writer in self.writers:
            try:
                await hexrotcomm.close_stream_writer(writer)
            except asyncio.CancelledError:
                pass
        if self.client is not None:
            await self.client.close()

    async def make_client(self):
        """Make a simple controller and wait for it to connect.

        Sets attribute self.mock_ctrl to the controller.
        """
        client = hexrotcomm.CommandTelemetryClient(
            log=self.mock_ctrl.log,
            ConfigClass=hexrotcomm.SimpleConfig,
            TelemetryClass=hexrotcomm.SimpleTelemetry,
            host=tcpip.LOCAL_HOST,
            command_port=self.mock_ctrl.command_port,
            telemetry_port=self.mock_ctrl.telemetry_port,
            connect_callback=self.connect_callback,
            config_callback=self.config_callback,
            telemetry_callback=self.telemetry_callback,
        )
        assert not client.should_be_connected
        await asyncio.wait_for(client.connect_task, timeout=STD_TIMEOUT)
        assert client.should_be_connected
        await self.assert_next_connected(command=True, telemetry=True, skip=1)
        print(
            f"client started; "
            f"command_port={self.mock_ctrl.command_port}; "
            f"telemetry_port={self.mock_ctrl.telemetry_port}"
        )
        return client

    def connect_callback(self, server):
        self.connect_queue.put_nowait(
            (server.command_connected, server.telemetry_connected)
        )
        if self.callbacks_raise:
            raise RuntimeError(
                "connect_callback raising because self.callbacks_raise is true"
            )

    def config_callback(self, server):
        self.config_queue.put_nowait(server.config)
        if self.callbacks_raise:
            raise RuntimeError(
                "config_callback raising because self.callbacks_raise is true"
            )

    def telemetry_callback(self, server):
        self.telemetry_list.append(server.telemetry)
        if self.callbacks_raise:
            raise RuntimeError(
                "telemetry_callback raising because self.callbacks_raise is true"
            )

    async def open_command_socket(self):
        """Make a controller command socket. Return reader, writer.

        Keeps track of the writer and closes it in tearDown if necessary.
        """
        connect_coro = asyncio.open_connection(
            host=tcpip.LOCAL_HOST, port=self.client.command_port
        )
        reader, writer = await asyncio.wait_for(connect_coro, timeout=STD_TIMEOUT)
        self.writers.append(writer)
        return reader, writer

    async def open_telemetry_socket(self):
        """Make a controller telemetry socket. Return reader, writer.

        Keeps track of the writer and closes it in tearDown if necessary.
        """
        connect_coro = asyncio.open_connection(
            host=tcpip.LOCAL_HOST, port=self.client.telemetry_port
        )
        reader, writer = await asyncio.wait_for(connect_coro, timeout=STD_TIMEOUT)
        self.writers.append(writer)
        return reader, writer

    async def next_config(self):
        """Wait for next config."""
        return await asyncio.wait_for(self.config_queue.get(), timeout=STD_TIMEOUT)

    async def assert_next_connected(
        self, command, telemetry, skip=0, timeout=STD_TIMEOUT
    ):
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
        assert skip >= 0
        for n in range(skip + 1):
            next_command, next_telemetry = await asyncio.wait_for(
                self.connect_queue.get(), timeout=timeout
            )
        assert command == next_command
        assert telemetry == next_telemetry

    def assert_connected(self, command, telemetry):
        """Assert that server command and/or telemetry sockets are
        connected."""
        assert self.client.command_connected == command
        assert self.client.telemetry_connected == telemetry
        assert self.client.connected == command and telemetry

    async def test_initial_conditions(self):
        self.assert_connected(command=True, telemetry=True)
        assert self.mock_ctrl.connected
        assert self.mock_ctrl.command_connected
        assert self.mock_ctrl.telemetry_connected
        config = await self.next_config()
        assert config.min_position == self.initial_min_position
        assert config.max_position == self.initial_max_position
        telemetry = await self.client.next_telemetry()
        assert telemetry.cmd_position == self.initial_cmd_position
        telemetry.curr_position >= self.initial_cmd_position

        # extra calls to client.connect should fail
        with pytest.raises(RuntimeError):
            await self.client.connect()

    async def test_client_reconnect(self):
        """Test that CommandTelemetryServer allows reconnection."""
        await self.client.close()
        await self.assert_next_connected(command=False, telemetry=False)
        assert not self.mock_ctrl.command_connected
        assert not self.mock_ctrl.telemetry_connected

        client = await self.make_client()
        try:
            assert self.mock_ctrl.connected
        finally:
            await client.close()

    async def test_move_command(self):
        config = await self.next_config()
        assert config.min_position == self.initial_min_position
        assert config.max_position == self.initial_max_position
        telemetry = await self.client.next_telemetry()
        assert telemetry.cmd_position == self.initial_cmd_position

        expected_counter = 0
        for good_position in (
            self.initial_min_position,
            self.initial_max_position,
            (self.initial_min_position + self.initial_max_position) / 2,
        ):
            expected_counter += 1
            with self.subTest(good_position=good_position):
                await self.check_move(
                    cmd_position=good_position, expected_counter=expected_counter
                )
        last_good_position = good_position

        for bad_position in (
            self.initial_min_position - 0.001,
            self.initial_max_position + 0.001,
        ):
            expected_counter += 1
            with self.subTest(bad_position=bad_position):
                await self.check_move(
                    cmd_position=bad_position,
                    expected_counter=expected_counter,
                    expected_position=last_good_position,
                )

        # set position commands should not trigger configuration output.
        with pytest.raises(asyncio.TimeoutError):
            await self.next_config()

    async def test_bad_frame_id(self):
        """Test that sending a header with an unknown frame ID causes
        the server to flush the rest of the message and continue.
        """
        # Stop the telemetry loop and clear telemetry_list
        self.mock_ctrl.telemetry_loop_task.cancel()
        # give the task time to finish; probably not needed
        await asyncio.sleep(0.01)
        writer = self.mock_ctrl.telemetry_server.writer
        self.telemetry_list = []

        # Fill a telemetry struct with arbitrary data.
        telemetry = hexrotcomm.SimpleTelemetry(
            application_status=5,
            state=1,
            enabled_substate=2,
            offline_substate=3,
            curr_position=6.3,
            cmd_position=-15.4,
        )

        # Write a header with invalid frame ID and telemetry data.
        # The bad header should trigger an error log message
        # and the data should be flushed.
        bad_frame_id = (
            hexrotcomm.SimpleTelemetry().FRAME_ID + hexrotcomm.SimpleConfig().FRAME_ID
        )
        bad_header = hexrotcomm.Header()
        bad_header.frame_id = bad_frame_id
        with self.assertLogs(level=logging.ERROR):
            await hexrotcomm.write_from(writer, bad_header)
            await hexrotcomm.write_from(writer, telemetry)
            # Give the reader time to read and deal with the message.
            await asyncio.sleep(STD_TIMEOUT)
        assert len(self.telemetry_list) == 0

        # Write a good header and telemetry and test that they are read.
        good_header = hexrotcomm.Header()
        good_header.frame_id = telemetry.FRAME_ID
        await hexrotcomm.write_from(writer, good_header)
        await hexrotcomm.write_from(writer, telemetry)
        # Give the reader time to read and deal with the message.
        await asyncio.sleep(STD_TIMEOUT)
        assert len(self.telemetry_list) == 1
        read_telemetry = self.telemetry_list[0]
        for name in (
            "application_status",
            "state",
            "enabled_substate",
            "offline_substate",
            "curr_position",
            "cmd_position",
        ):
            assert getattr(read_telemetry, name) == getattr(telemetry, name), name

    async def test_put_command_errors(self):
        """Test expected failures in CommandTelemetryServer.put_command."""
        assert self.client.connected

        command = hexrotcomm.Command()
        command.code = hexrotcomm.SimpleCommandCode.MOVE
        command.param1 = 0

        # Should fail if not an instance of Command
        with pytest.raises(ValueError):
            await self.client.put_command("not a command")

        # The client should still be connected
        assert self.client.connected

        # Should fail if command client is not connected
        await self.client.close()
        assert not self.client.connected
        assert not self.client.telemetry_connected
        with pytest.raises(RuntimeError):
            await self.client.put_command(command)

    async def test_failed_callbacks(self):
        """Check that the server gracefully handles callback functions
        that raise an exception.
        """
        self.callbacks_raise = True
        await self.client.next_telemetry()
        config = await self.next_config()
        assert config.min_position == self.initial_min_position
        assert config.max_position == self.initial_max_position
        assert len(self.telemetry_list) >= 1
        self.assert_connected(command=True, telemetry=True)

    async def test_should_be_connected(self):
        """Test that should_be_connected remains true for basic_close.

        Note that make_client already checks that should_be_connected
        is false before making a connection and true after.
        """
        assert self.client.connected
        assert self.client.should_be_connected

        await self.client.basic_close()
        assert self.client.should_be_connected
        assert not self.client.connected
        await self.assert_next_connected(command=False, telemetry=False)

        await self.client.close()
        assert not self.client.connected
        assert not self.client.should_be_connected

        # should_be_connected should still be true if the connection is lost
        client = await self.make_client()
        await self.mock_ctrl.close_client()
        assert not client.connected
        assert client.should_be_connected
        await self.assert_next_connected(command=False, telemetry=False)

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
        await self.client.put_command(command)
        assert command.counter == expected_counter

        telemetry = await self.client.next_telemetry()
        assert telemetry.cmd_position == expected_position


class SimpleMockControllerTestCase(unittest.IsolatedAsyncioTestCase):
    """Test SimpleMockController constructor errors."""

    async def test_constructor_errors(self):
        log = logging.getLogger()
        # port=0 and host=None is not allowed
        with pytest.raises(ValueError):
            hexrotcomm.SimpleMockController(
                log=log,
                port=0,
                host=None,
                initial_state=ControllerState.ENABLED,
            )
