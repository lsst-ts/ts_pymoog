.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm_overview:

ts_hexrotcomm Overview
######################

Introduction
============

Python code to communicate with the main telescope camera rotator and hexapod low level controllers (code running in PXI computers).
Note that these controller use a rather strange :ref:`communication protocol <communication_protocol>`.
Contents include:

* `BaseCsc`: base class for the main telescope and hexapod Commandable SAL Components (CSCs).
* `BaseMockController`: base class for mock controllers.
* `CommandTelemetryServer`: A TCP/IP server that communicates with the low level controllers to send commands and receive telemetry.
* `Command` and `Header`: C structures used for communication.
* `SimpleMockController`: a simple mock controller for testing `BaseCsc` and `CommandTelemetryServer`.
* `CscCommander`: a base class for simple command-line scripts that control a rotator or hexapod CSC.

Note: Moog, the vendor that provided the low level controllers, also provided CSCs for the camera rotator and two hexapods.
Moog implemented these as a single C++ program called the "wrapper" that acts as all three CSCs at once.
We are replacing Moog's "wrapper" with Python CSCs which use this package for communication.

.. _communication_protocol:

TCP/IP Communication Protocol
=============================

TCP/IP communication is surprising in several ways:

* It is backwards from what you might expect.
  Each low level controller creates two _client_ sockets that connect to socket _servers_ in the CSC.
  One socket is used to read commands and the other socket is used to write telemetry and configuration.

* Despite using two sockets, each socket only transmits information in one direction.
  The low level controller writes nothing to its command socket and reads nothing from its telemetry and configuration socket.
  As a result the low level controller provides no clear feedback if it rejects a command (e.g. because the controller is disabled or the command asks for a motion that is out of limits).

All data is sent as binary: C data structures with no padding (the C structures are defined using ``__attribute__((__packed__))``).
There is no "end of data" indicator so no way to resynchronize if any bytes are lost.
`CommandTelemetryServer` handles this by closing the telemetry connection if it cannot understand a telemetry or configuration message, then waiting for the low level controller to reconnect.

Revision History
================

.. toctree::
    revision_history
    :maxdepth: 1
