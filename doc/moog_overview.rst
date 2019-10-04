.. py:currentmodule:: lsst.ts.pymoog

.. _lsst.ts.pymoog_moog_overview:

Moog Controller Overview
########################

Introduction
============

Moog wrote controllers for the main telescope camera rotator and hexapods.
Each controller communicates over TCP/IP with a Commandable SAL Component (CSC).
Each controller also communicates with a LabVIEW GUI, and the controller can be configured to accept commands from either that or the CSC.

This package provides Python code to communicate with the controllers and a base class to emulate a controller:

* `Server`: a class that acts as the server in CSC.
* `BaseMockController`: a base class for mock controllers.

TCP/IP Communication
====================

TCP/IP communication is backwards from the usual.

Each controller creates two _client_ sockets that connect to socket _servers_ in the CSC.
One socket is used to read commands and the other socket is used to write telemetry and configuration.
Note that nothing is transmitted in the other direction: the controller writes nothing to its command socket and reads nothing from its telemetry and configuration socket.

As a result the controller provides no feedback if a command is rejected (e.g. because the controller is disabled or the command asks for a motion that is out of limits).

All data is sent as binary: C data structures with no padding (the C structures are defined using ``__attribute__((__packed__))``).
There is no "end of data" indicator so no way to resynchronize if any bytes are lost.

Moog CSCs
=========

Moog also provided CSCs, but we plan to replace those with Python.
Moog implemented the three CSC as a single program called the "wrapper", so the three controllers all try to connect to the same server.
Fortunately that is easy to change.
