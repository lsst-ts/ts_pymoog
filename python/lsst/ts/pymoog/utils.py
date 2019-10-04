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

__all__ = ["read_into", "write_from"]

import ctypes


async def read_into(reader, struct):
    """Read binary data from a socket into a `ctypes.Structure`.

    Parameters
    ----------
    reader :  `asyncio.StreamReader`
        Asynchronous stream reader.
    struct : `ctypes.Structure`
        Structure to set.
    """
    nbytes = ctypes.sizeof(struct)
    data = await reader.read(nbytes)
    ctypes.memmove(ctypes.addressof(struct), data, nbytes)


async def write_from(writer, *structs):
    r"""Write binary data from one or `ctypes.Structure`\ s to a socket.

    Parameters
    ----------
    writer : `asyncio.StreamWriter`
        Asynchronous stream writer.
    structs : `ctypes.Structure`
        One or more structures to write.
    """
    for struct in structs:
        writer.write(bytes(struct))
        await writer.drain()
