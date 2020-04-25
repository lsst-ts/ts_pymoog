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

__all__ = ["BaseCscTestCase"]

import abc
import asyncio
import logging
import shutil

from lsst.ts import salobj

STD_TIMEOUT = 5  # timeout for command ack
LONG_TIMEOUT = 30  # timeout for CSCs to start


class BaseCscTestCase(metaclass=abc.ABCMeta):
    """Base class for CSC tests.

    Subclasses must:

    * Inherit both from this and `asynctest.TestCase`.
    * Override `basic_make_csc` to return a CSC.

    Also we suggest:

    * Add a method ``test_standard_state_transitions`` which calls
      `check_standard_state_transitions`.
    * Add a method ``test_bin_script`` which calls `check_bin_script`,
      assuming you have a binary script to run your CSC.
    """

    async def setUp(self):
        salobj.set_random_lsst_dds_domain()
        self.csc = None  # set by make_csc
        self.remote = None

    async def tearDown(self):
        close_tasks = []
        if self.csc is not None:
            close_tasks.append(self.csc.close())
        if self.remote is not None:
            close_tasks.append(self.remote.close())
        if close_tasks:
            await asyncio.wait_for(asyncio.gather(*close_tasks), timeout=STD_TIMEOUT)

    @abc.abstractmethod
    def basic_make_csc(self, initial_state, simulation_mode=1):
        """Make and return a CSC.
        """
        raise NotImplementedError()

    async def make_csc(
        self,
        initial_state,
        simulation_mode=1,
        wait_connected=True,
        log_level=logging.INFO,
    ):
        """Create a CSC and remote and wait for them to start.

        If your CSC is indexed, we suggest you use a different index
        for each call.

        Parameters
        ----------
        initial_state : `lsst.ts.salobj.State` or `int` (optional)
            The initial state of the CSC. Ignored except in simulation mode
            because in normal operation the initial state is the current state
            of the controller.
        simulation_mode : `int` (optional)
            Simulation mode.
        wait_connected : `bool` (optional)
            If True then wait for the controller to connect.
        log_level : `int` (optional)
            Logging level, such as `logging.INFO`.
        """
        self.csc = self.basic_make_csc(
            initial_state=initial_state, simulation_mode=simulation_mode
        )
        if len(self.csc.log.handlers) < 2:
            self.csc.log.addHandler(logging.StreamHandler())
            self.csc.log.setLevel(log_level)
        self.remote = salobj.Remote(
            domain=self.csc.domain,
            name=self.csc.salinfo.name,
            index=self.csc.salinfo.index,
        )

        await asyncio.wait_for(
            asyncio.gather(self.csc.start_task, self.remote.start_task),
            timeout=LONG_TIMEOUT,
        )
        self.csc.mock_ctrl.log.setLevel(log_level)
        if wait_connected:
            for i in range(3):
                data = await self.remote.evt_connected.next(
                    flush=False, timeout=STD_TIMEOUT
                )
                if data.command and data.telemetry:
                    print("Connected")
                    break

    async def assert_next_summary_state(self, state, timeout=STD_TIMEOUT):
        """Wait for and check the next ``summaryState`` event.

        Parameters
        ----------
        state : `lsst.ts.salobj.State` or `int`
            Desired value for ``summaryState.summaryState``.
        timeout : `float`
            Time limit for getting a ``summaryState`` event (sec).
        """
        data = await self.remote.evt_summaryState.next(flush=False, timeout=timeout)
        self.assertEqual(data.summaryState, state)

    async def assert_next_controller_state(
        self,
        controllerState=None,
        offlineSubstate=None,
        enabledSubstate=None,
        timeout=STD_TIMEOUT,
    ):
        """Wait for and check the next controllerState event.

        Parameters
        ----------
        controllerState : `lsst.ts.idl.enums.Rotator.ControllerState` or `int`
            Desired controller state.
        offlineSubstate : `lsst.ts.idl.enums.Rotator.OfflineSubstate` or `int`
            Desired offline substate.
        enabledSubstate : `lsst.ts.idl.enums.Rotator.EnabledSubstate` or `int`
            Desired enabled substate.
        timeout : `float`
            Time limit for getting a ``controllerState`` event (sec).
        """
        data = await self.remote.evt_controllerState.next(flush=False, timeout=timeout)
        if controllerState is not None:
            self.assertEqual(data.controllerState, controllerState)
        if offlineSubstate is not None:
            self.assertEqual(data.offlineSubstate, offlineSubstate)
        if enabledSubstate is not None:
            self.assertEqual(data.enabledSubstate, enabledSubstate)

    async def check_bin_script(self, name, index, exe_name):
        """Test running the CSC command line script.

        Parameters
        ----------
        name : `str`
            Name of SAL component, e.g. "Rotator"
        index : `int` or `None`
            SAL index of component.
        exe_name : `str`
            Name of executable, e.g. "run_rotator.py"
        """
        exe_path = shutil.which(exe_name)
        if exe_path is None:
            self.fail(
                f"Could not find bin script {exe_name}; did you setup or install this package?"
            )

        if index is None:
            process = await asyncio.create_subprocess_exec(exe_name, "--simulate")
        else:
            process = await asyncio.create_subprocess_exec(
                exe_name, str(index), "--simulate"
            )
        try:
            async with salobj.Domain() as domain:
                remote = salobj.Remote(domain=domain, name=name, index=index)
                summaryState_data = await remote.evt_summaryState.next(
                    flush=False, timeout=60
                )
                self.assertEqual(summaryState_data.summaryState, salobj.State.OFFLINE)

        finally:
            process.terminate()

    async def check_standard_state_transitions(self, enabled_commands):
        """Test standard CSC state transitions.

        Parameters
        ----------
        enabled_commands : `List` [`str`]
            List of commands that are valid in the enabled/stationary state,
            for example ("move", "stop").
            Need not include the standard commands, which are "disable"
            and "setLogLevel".
        """
        await self.make_csc(initial_state=salobj.State.OFFLINE)
        await self.assert_next_summary_state(salobj.State.OFFLINE)
        await self.check_bad_commands(good_commands=("enterControl", "setLogLevel"))

        # send enterControl; new state is STANDBY
        await self.remote.cmd_enterControl.start(timeout=STD_TIMEOUT)
        # Check CSC summary state directly to make sure it has changed
        # before the command is acknowledged as done.
        self.assertEqual(self.csc.summary_state, salobj.State.STANDBY)
        await self.assert_next_summary_state(salobj.State.STANDBY)
        await self.check_bad_commands(
            good_commands=("start", "exitControl", "setLogLevel")
        )

        # send start; new state is DISABLED
        await self.remote.cmd_start.start(timeout=STD_TIMEOUT)
        self.assertEqual(self.csc.summary_state, salobj.State.DISABLED)
        await self.assert_next_summary_state(salobj.State.DISABLED)
        await self.check_bad_commands(
            good_commands=("enable", "standby", "setLogLevel")
        )

        # send enable; new state is ENABLED
        await self.remote.cmd_enable.start(timeout=STD_TIMEOUT)
        self.assertEqual(self.csc.summary_state, salobj.State.ENABLED)
        await self.assert_next_summary_state(salobj.State.ENABLED)
        good_enabled_commands = set(("disable", "setLogLevel")) | set(enabled_commands)
        await self.check_bad_commands(good_commands=good_enabled_commands)

        # send disable; new state is DISABLED
        await self.remote.cmd_disable.start(timeout=STD_TIMEOUT)
        self.assertEqual(self.csc.summary_state, salobj.State.DISABLED)
        await self.assert_next_summary_state(salobj.State.DISABLED)

        # send standby; new state is STANDBY
        await self.remote.cmd_standby.start(timeout=STD_TIMEOUT)
        self.assertEqual(self.csc.summary_state, salobj.State.STANDBY)
        await self.assert_next_summary_state(salobj.State.STANDBY)

        # send exitControl; new state is OFFLINE
        await self.remote.cmd_exitControl.start(timeout=STD_TIMEOUT)
        self.assertEqual(self.csc.summary_state, salobj.State.OFFLINE)
        await self.assert_next_summary_state(salobj.State.OFFLINE)

    async def check_bad_commands(self, bad_commands=None, good_commands=None):
        """Check that bad commands fail.

        Parameters
        ----------
        bad_commands : `List`[`str`] or `None` (optional)
            Names of bad commands to try, or None for all commands.
        good_commands : `List`[`str`] or `None` (optional)
            Names of good commands to skip, or None to skip none.

        Notes
        -----
        If a command appears in both lists it is considered a good command.
        """
        if bad_commands is None:
            bad_commands = self.remote.salinfo.command_names
        if good_commands is None:
            good_commands = ()
        commands = self.remote.salinfo.command_names
        for command in commands:
            print(f"Try bad_command={command}")
            if command in good_commands:
                continue
            with self.subTest(command=command):
                cmd_attr = getattr(self.remote, f"cmd_{command}")
                with salobj.assertRaisesAckError(ack=salobj.SalRetCode.CMD_FAILED):
                    await cmd_attr.start(timeout=STD_TIMEOUT)

    async def test_clear_error(self):
        await self.make_csc(initial_state=salobj.State.FAULT)
        await self.assert_next_summary_state(salobj.State.FAULT)
        await self.remote.cmd_clearError.start(timeout=STD_TIMEOUT)
        await self.assert_next_summary_state(salobj.State.STANDBY)

    async def test_initial_state_offline(self):
        await self.check_initial_state(salobj.State.OFFLINE)

    async def test_initial_state_standby(self):
        await self.check_initial_state(salobj.State.STANDBY)

    async def test_initial_state_disabled(self):
        await self.check_initial_state(salobj.State.DISABLED)

    async def test_initial_state_enabled(self):
        await self.check_initial_state(salobj.State.ENABLED)

    async def check_initial_state(self, initial_state):
        await self.make_csc(initial_state=initial_state)
        await self.assert_next_summary_state(initial_state)

    def test_bad_simulation_modes(self):
        """Test simulation_mode argument of TestCsc constructor.

        The only allowed values are 0 and 1.
        """
        for bad_simulation_mode in (-1, 2, 3):
            with self.assertRaises(ValueError):
                self.basic_make_csc(
                    initial_state=salobj.State.OFFLINE,
                    simulation_mode=bad_simulation_mode,
                )

    async def test_non_simulation_mode(self):
        ignored_initial_state = salobj.State.DISABLED
        async with self.basic_make_csc(
            initial_state=ignored_initial_state, simulation_mode=0
        ) as csc:
            self.assertIsNone(csc.mock_ctrl)
            await asyncio.sleep(0.2)
            self.assertFalse(csc.server.command_connected)
            self.assertFalse(csc.server.telemetry_connected)
            self.assertFalse(csc.server.connected)
