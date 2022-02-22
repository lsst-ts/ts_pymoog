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
import contextlib
import logging
import unittest

import pytest

from lsst.ts import tcpip
from lsst.ts import salobj
from lsst.ts.idl.enums.MTRotator import ControllerState
from lsst.ts import hexrotcomm

# Standard timeout (seconds)
STD_TIMEOUT = 5

logging.basicConfig()


class CommandTelemetryClientTestCase(unittest.IsolatedAsyncioTestCase):
    """Test CommandTelemetryClient, SimpleMockController, and thus
    BaseMockController.
    """

    async def asyncSetUp(self):
        self.client = None  # set by make_client

        # Queue of connected, filled by the self.connect_callback
        self.connect_queue = asyncio.Queue()

        # Queue of config filled by the self.config_callback
        self.config_list = []

        # List of telemetry, filled by the server's telemetry_callback.
        # This is a list (not a queue like connect_queue and config_list)
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
        print(f"mock controller started; port={self.mock_ctrl.port}")
        self.initial_min_position = self.mock_ctrl.config.min_position
        self.initial_max_position = self.mock_ctrl.config.max_position
        self.initial_max_velocity = self.mock_ctrl.config.max_velocity
        self.initial_cmd_position = self.mock_ctrl.telemetry.cmd_position

    async def asyncTearDown(self):
        if self.mock_ctrl is not None:
            await self.mock_ctrl.close()

    @contextlib.asynccontextmanager
    async def make_client(self):
        """Make a simple controller and wait for it to connect.

        Sets attribute self.mock_ctrl to the controller.
        """
        self.client = hexrotcomm.CommandTelemetryClient(
            log=self.mock_ctrl.log,
            ConfigClass=hexrotcomm.SimpleConfig,
            TelemetryClass=hexrotcomm.SimpleTelemetry,
            host=tcpip.LOCAL_HOST,
            port=self.mock_ctrl.port,
            connect_callback=self.connect_callback,
            config_callback=self.config_callback,
            telemetry_callback=self.telemetry_callback,
        )
        assert not self.client.should_be_connected
        await asyncio.wait_for(self.client.connect_task, timeout=STD_TIMEOUT)
        assert self.client.should_be_connected
        await self.assert_next_connected(True)
        print(f"client started; port={self.mock_ctrl.port}")
        try:
            yield
        finally:
            await self.client.close()

    async def connect_callback(self, server):
        self.connect_queue.put_nowait(server.connected)
        if self.callbacks_raise:
            raise RuntimeError(
                "connect_callback raising because self.callbacks_raise is true"
            )

    async def config_callback(self, server):
        self.config_list.append(server.config)
        if self.callbacks_raise:
            raise RuntimeError(
                "config_callback raising because self.callbacks_raise is true"
            )

    async def telemetry_callback(self, server):
        self.telemetry_list.append(server.telemetry)
        if self.callbacks_raise:
            raise RuntimeError(
                "telemetry_callback raising because self.callbacks_raise is true"
            )

    async def assert_next_connected(self, connected, timeout=STD_TIMEOUT):
        """Assert results of next connect_callback.

        Parameters
        ----------
        connected : `bool`
            Should we be connected?
        timeout : `float`
            Time to wait for connect_callback (seconds).
        """
        is_connected = await asyncio.wait_for(self.connect_queue.get(), timeout=timeout)
        assert connected == is_connected

    async def test_constructor_errors(self):
        good_callbacks = dict(
            config_callback=self.config_callback,
            telemetry_callback=self.telemetry_callback,
            connect_callback=self.connect_callback,
        )

        def non_coro_callback(_):
            pass

        for key in good_callbacks:
            bad_callbacks = good_callbacks.copy()
            bad_callbacks[key] = non_coro_callback
            with pytest.raises(TypeError):
                hexrotcomm.CommandTelemetryClient(
                    log=self.mock_ctrl.log,
                    ConfigClass=hexrotcomm.SimpleConfig,
                    TelemetryClass=hexrotcomm.SimpleTelemetry,
                    host=tcpip.LOCAL_HOST,
                    port=self.mock_ctrl.port,
                    **bad_callbacks,
                )

    async def test_initial_conditions(self):
        async with self.make_client():
            assert self.client.connected
            assert self.mock_ctrl.connected
            await asyncio.wait_for(self.client.configured_task, timeout=STD_TIMEOUT)
            config = self.client.config
            assert config.min_position == self.initial_min_position
            assert config.max_position == self.initial_max_position
            telemetry = await asyncio.wait_for(
                self.client.next_telemetry(), timeout=STD_TIMEOUT
            )
            assert telemetry.cmd_position == self.initial_cmd_position
            telemetry.curr_position >= self.initial_cmd_position

            # extra calls to client.connect should fail
            with pytest.raises(RuntimeError):
                await self.client.connect()

    async def test_client_reconnect(self):
        """Test that BaseMockController allows reconnection."""
        async with self.make_client():
            await self.client.close()
            await self.assert_next_connected(False)
            assert not self.mock_ctrl.connected

        async with self.make_client():
            assert self.mock_ctrl.connected

    async def test_move_command(self):
        async with self.make_client():
            await asyncio.wait_for(self.client.configured_task, timeout=STD_TIMEOUT)
            config = self.client.config
            assert config.min_position == self.initial_min_position
            assert config.max_position == self.initial_max_position
            telemetry = await self.client.next_telemetry()
            assert telemetry.cmd_position == self.initial_cmd_position

            expected_counter = -1
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
                        should_fail=True,
                    )

            # set position commands should not trigger configuration output.
            await asyncio.sleep(1)
            assert len(self.config_list) == 1

    async def test_bad_frame_id(self):
        """Test that sending a header with an unknown frame ID causes
        the server to flush the rest of the message and continue.
        """
        async with self.make_client():
            # Stop the telemetry loop and clear telemetry_list
            self.mock_ctrl.telemetry_loop_task.cancel()
            # give the task time to finish; probably not needed
            await asyncio.sleep(0.01)
            writer = self.mock_ctrl.writer
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
            bad_frame_id = 1025
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
            good_header.frame_id = hexrotcomm.FrameId.TELEMETRY
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

    async def test_run_command_errors(self):
        """Test expected failures in BaseMockController.run_command."""
        async with self.make_client():
            assert self.client.connected

            command = hexrotcomm.Command()
            command.code = hexrotcomm.SimpleCommandCode.MOVE
            command.param1 = 0

            # Should fail if not an instance of Command
            with pytest.raises(ValueError):
                await self.client.run_command("not a command")

            # The client should still be connected
            assert self.client.connected

            # Should fail if command client is not connected
            await self.client.close()
            assert not self.client.connected
            with pytest.raises(RuntimeError):
                await self.client.run_command(command)

    async def test_failed_callbacks(self):
        """Check that the server gracefully handles callback functions
        that raise an exception.
        """
        self.callbacks_raise = True
        async with self.make_client():
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(self.client.configured_task, timeout=STD_TIMEOUT)
            config = self.client.config
            assert config.min_position == self.initial_min_position
            assert config.max_position == self.initial_max_position
            assert len(self.config_list) == 1
            await asyncio.wait_for(self.client.next_telemetry(), timeout=STD_TIMEOUT)
            assert len(self.telemetry_list) >= 1
            assert self.client.connected

    async def test_should_be_connected(self):
        """Test the should_be_connected attribute.

        Note that make_client already checks that should_be_connected
        is false before making a connection and true after.
        """
        async with self.make_client():
            assert self.client.connected
            assert self.client.should_be_connected

            # should_be_connected should still be true after basic_close
            await self.client.basic_close()
            await self.assert_next_connected(False)
            assert not self.client.connected
            assert self.client.should_be_connected

            # should_be_connected should be false after close
            await self.client.close()
            assert not self.client.connected
            assert not self.client.should_be_connected

        # should_be_connected should still be true
        # if the connection is lost
        async with self.make_client():
            await self.mock_ctrl.close_client()
            await self.assert_next_connected(False)
            assert not self.client.connected
            assert self.client.should_be_connected

    async def test_truncate_command_status_reason(self):
        """Test that a too-long command status reason is truncated."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=tcpip.LOCAL_HOST, port=self.mock_ctrl.port),
            timeout=STD_TIMEOUT,
        )
        try:
            reason_len = hexrotcomm.CommandStatus.reason.size
            assert reason_len > 0
            command_status = hexrotcomm.CommandStatus()
            counter = 45
            status = hexrotcomm.CommandStatusCode.ACK
            duration = 3.14
            too_long_reason = "ab" * reason_len
            too_long_reason_bytes = too_long_reason.encode()
            await self.mock_ctrl.write_command_status(
                counter=counter,
                status=status,
                duration=duration,
                reason=too_long_reason,
            )

            header, command_status = await asyncio.wait_for(
                self.next_command_status(reader), timeout=STD_TIMEOUT
            )
            assert header.counter == counter
            assert command_status.status == status
            assert command_status.duration == duration
            assert len(command_status.reason) < len(too_long_reason_bytes)
            assert command_status.reason == too_long_reason_bytes[0:reason_len]
        finally:
            await asyncio.wait_for(
                tcpip.close_stream_writer(writer), timeout=STD_TIMEOUT
            )

    async def next_command_status(self, reader):
        """Read next command status. Reader header and command status."""
        header = hexrotcomm.Header()
        command_status = hexrotcomm.CommandStatus()
        config = hexrotcomm.SimpleConfig()
        telemetry = hexrotcomm.SimpleTelemetry()
        while True:
            await tcpip.read_into(reader, header)
            if header.frame_id == hexrotcomm.FrameId.COMMAND_STATUS:
                await tcpip.read_into(reader, command_status)
                return header, command_status
            elif header.frame_id == hexrotcomm.FrameId.CONFIG:
                await tcpip.read_into(reader, config)
            elif header.frame_id == hexrotcomm.FrameId.TELEMETRY:
                await tcpip.read_into(reader, telemetry)
            else:
                raise RuntimeError(f"Unrecognized frame_id: {header.frame_id}")

    async def check_move(
        self, cmd_position, expected_counter, expected_position=None, should_fail=False
    ):
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
        if should_fail:
            with pytest.raises(salobj.ExpectedError):
                await self.client.run_command(command)
        else:
            await self.client.run_command(command)
        assert command.commander == command.COMMANDER
        assert command.counter == expected_counter

        telemetry = await self.client.next_telemetry()
        assert telemetry.cmd_position == expected_position
