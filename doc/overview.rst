.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm_overview:

ts_hexrotcomm Overview
######################

Introduction
============

This package provides Python code to communicate with the main telescope camera rotator and hexapod controllers written by Moog, plus a base class for mock controllers.
Contents include:

* `Server`: TCP/IP server that communicates with Moog controllers (see TCP/IP Communication below).
* `BaseMockController`: base class for mock controllers.
* `structs.Command` and `structs.Header`: C structures used for communication.
* `SimpleMockController`: a simple mock controller (much simpler than the camera rotator and hexapod) intended for testing the server.

Note: Moog also provided CSCs for the camera rotator and hexapods, implemented as a single program called the "wrapper" that acts as three CSCs.
We are replacing Moog's "wrapper" with Python CSCs which use this package for communication.


TCP/IP Communication
====================

TCP/IP communication is backwards from the usual.

Each controller creates two _client_ sockets that connect to socket _servers_ in the CSC.
One socket is used to read commands and the other socket is used to write telemetry and configuration.
Note that nothing is transmitted in the other direction: the controller writes nothing to its command socket and reads nothing from its telemetry and configuration socket.

As a result the controller provides no feedback if it rejects a command (e.g. because the controller is disabled or the command asks for a motion that is out of limits).

All data is sent as binary: C data structures with no padding (the C structures are defined using ``__attribute__((__packed__))``).
There is no "end of data" indicator so no way to resynchronize if any bytes are lost.
