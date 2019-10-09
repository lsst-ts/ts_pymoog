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

__all__ = ["Command", "Header"]

import ctypes


class Command(ctypes.Structure):
    """Command for Moog controller.
    """
    _pack_ = 1
    _fields_ = [
        ("sync_pattern", ctypes.c_ushort),
        ("counter", ctypes.c_ushort),
        ("cmd", ctypes.c_uint),
        ("param1", ctypes.c_double),
        ("param2", ctypes.c_double),
        ("param3", ctypes.c_double),
        ("param4", ctypes.c_double),
        ("param5", ctypes.c_double),
        ("param6", ctypes.c_double),
    ]


class Header(ctypes.Structure):
    """Initial part of telemetry or configuration data from a Moog controller.
    """
    _pack_ = 1
    _fields_ = [
        ("sync_pattern", ctypes.c_ushort),
        ("frame_id", ctypes.c_ushort),
        ("counter", ctypes.c_ushort),
        ("mjd", ctypes.c_int),
        ("mjd_frac", ctypes.c_double),
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_long),
    ]
