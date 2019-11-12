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

__all__ = ["CscCommander"]

import asyncio
import functools
import sys

from lsst.ts import salobj
from lsst.ts.idl.enums import Rotator

STD_TIMEOUT = 5  # timeout for command ack


def round_any(value, digits=4):
    """Round any value to the specified number of digits.

    This is a no-op for int and str values.
    """
    if isinstance(value, float):
        return round(value, digits)
    return value


async def stdin_generator():
    """Thanks to http://blog.mathieu-leplatre.info
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:  # EOF.
            break
        yield line.decode("utf-8").strip()


class CscCommander:
    """Command a Hexapod or Rotator CSC from the command line.
    """
    def __init__(self, name, index, help_text):
        self.domain = salobj.Domain()
        self.remote = salobj.Remote(domain=self.domain, name=name, index=index)
        self.help_text = help_text

        for name in self.remote.salinfo.event_names:
            if name == "heartbeat":
                continue
            topic_name = f"evt_{name}"
            topic = getattr(self.remote, topic_name)
            callback = getattr(self, f"{topic_name}_callback", None)
            if callback is None:
                callback = functools.partial(self.event_callback, name=name)
            setattr(topic, "callback", callback)

        for name in self.remote.salinfo.telemetry_names:
            topic_name = f"tel_{name}"
            setattr(self, f"previous_{topic_name}", None)
            topic = getattr(self.remote, topic_name)
            callback = getattr(self, f"{topic_name}_callback", None)
            if callback is None:
                callback = functools.partial(self.telemetry_callback, name=name)
            setattr(topic, "callback", callback)

    async def close(self):
        print("Exiting; please wait.")
        try:
            await self.remote.cmd_stop.start(timeout=STD_TIMEOUT)
        except Exception:
            # Best effort attempt to stop. The command may not
            # even be valid in the current state.
            pass
        await self.remote.close()
        await self.domain.close()

    def format_item(self, key, value):
        if isinstance(value, float):
            return f"{key}={value:0.4f}"
        return f"{key}={value}"

    def format_data(self, data):
        return ", ".join(self.format_item(key, value) for key, value in self.get_public_fields(data).items())

    def get_public_fields(self, data):
        return dict((key, value) for key, value in data.get_vars().items()
                    if not key.startswith("private_") and
                    key not in ("priority", "timestamp"))

    def get_rounded_public_fields(self, data):
        return dict((key, round_any(value)) for key, value in data.get_vars().items()
                    if not key.startswith("private_") and
                    key not in ("priority", "timestamp"))

    def event_callback(self, data, name):
        """Generic callback for events."""
        print(f"{name}: {self.format_data(data)}")

    def telemetry_callback(self, data, name):
        """Generic callback for telemetry."""
        prev_value_name = f"previous_tel_{name}"
        public_fields = self.get_rounded_public_fields(data)
        if public_fields != getattr(self, prev_value_name):
            setattr(self, prev_value_name, public_fields)
            formatted_data = ", ".join(f"{key}={value}" for key, value in public_fields.items())
            print(f"{name}: {formatted_data}")

    def evt_controllerState_callback(self, data):
        print(f"controllerState: state={Rotator.ControllerState(data.controllerState)!r}; "
              f"offline_substate={Rotator.OfflineSubstate(data.offlineSubstate)!r}; "
              f"enabled_substate={Rotator.EnabledSubstate(data.enabledSubstate)!r}; "
              f"applicationStatus={data.applicationStatus}")

    def check_arguments(self, args, *names):
        """Check that the required arguments are provided,
        and return them as a keyword argument dict with cast values.

        Parameters
        ----------
        args : `List` [`str`]
            Command arguments, as strings.
        *names : `List` [`str` or `tuple`]
            Argument name and optional cast function. Each element is either:

            * An argument name, in which case the argument is cast to a float
            * A tuple of (name, cast function), in which case the argument
                is cast using the cast function.
        """
        required_num_args = len(names)
        if len(args) != required_num_args:
            if required_num_args == 0:
                raise RuntimeError("no arguments allowed")
            else:
                raise RuntimeError(f"{required_num_args} arguments required:  "
                                   f"{names}; {len(args)} provided.")

        def cast(name, arg):
            if isinstance(name, tuple):
                if len(name) != 2:
                    raise RuntimeError("Cannot parse {name} as (name, casting function)")
                arg_name, cast_func = name
                return (arg_name, cast_func(arg))
            else:
                return (name, float(arg))

        return dict(cast(name, arg) for name, arg in zip(names, args))

    async def do_enterControl(self, args):
        self.check_arguments(args)
        await self.remote.cmd_enterControl.start(timeout=STD_TIMEOUT)

    async def do_start(self, args):
        self.check_arguments(args)
        await self.remote.cmd_start.start(timeout=STD_TIMEOUT)

    async def do_enable(self, args):
        self.check_arguments(args)
        await self.remote.cmd_enable.start(timeout=STD_TIMEOUT)

    async def do_disable(self, args):
        self.check_arguments(args)
        await self.remote.cmd_disable.start(timeout=STD_TIMEOUT)

    async def do_standby(self, args):
        self.check_arguments(args)
        await self.remote.cmd_standby.start(timeout=STD_TIMEOUT)

    async def do_exitControl(self, args):
        self.check_arguments(args)
        await self.remote.cmd_exitControl.start(timeout=STD_TIMEOUT)

    async def do_clearError(self, args):
        self.check_arguments(args)
        await self.remote.cmd_clearError.start(timeout=STD_TIMEOUT)

    async def amain(self):
        """Wait for the remote to start, then execute commands
        until the ``exit`` is seen.
        """
        try:
            print("Waiting for the remote to connect.")
            await self.remote.start_task

            print(f"\n{self.help_text}")
            async for line in stdin_generator():
                # Strip trailing comment, if any.
                if "#" in line:
                    line = line.split("#", maxsplit=1)[0].strip()
                if not line:
                    continue
                tokens = line.split()
                command = tokens[0]
                args = tokens[1:]
                try:
                    if command == "exit":
                        break
                    elif command == "help":
                        print(self.help_text)
                    else:
                        cmd_method = getattr(self, f"do_{command}", None)
                        if cmd_method is None:
                            print(f"Unrecognized command {command}")
                            continue
                        await cmd_method(args)
                except Exception as e:
                    print(f"Command {command} failed: {e}")
                    continue
                print(f"Finished command {command}")
        finally:
            await self.close()
