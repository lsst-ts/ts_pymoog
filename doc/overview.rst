.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm_overview:

ts_hexrotcomm Overview
######################

Introduction
============

Python code to communicate with the main telescope camera rotator and hexapod low level controllers (code running in PXI computers).
Contents include:

* `BaseCsc`: base class for the main telescope and hexapod Commandable SAL Components (CSCs).
* `CommandTelemetryClient`: A TCP/IP client that communicates with the low level controllers to send commands and receive telemetry.
* `BaseMockController`: base class for mock controllers.
* `CommandTelemetryServer`: A TCP/IP server for reading commands and writing telemetry.
  Only one stream may be connected on each port.
  `BaseMockController` inherits from this class.
* `Command` and `Header`: C structures used for communication.
* `SimpleMockController`: a simple mock controller for testing `BaseCsc` and `CommandTelemetryServer`.

.. _lsst.ts.hexrotcomm_communication_protocol:

TCP/IP Communication Protocol
=============================

The CSC communicates with the low-level controller using two unidirectional sockets:
one to send commands to the low-level controller, the other to report configuration and state to the CSC.
All data is sent as binary C data structures with no padding (the C structures are defined using ``__attribute__((__packed__))``).

The CSC connects to the low-level controller as part of the ``start`` command.

The CSC enables the low-level controller (including clearing errors) as part of the ``enable`` command.

The CSC disconnects from the low-level controller as part of the ``standby`` command.
In addition, if the CSC loses its connection on either stream, it will disconnect both streams and go to fault state.

If the low-level controller goes to fault state *while the CSC is enabled*,
the CSC will also go to fault state, but remain connected.
This gives users the ability to monitor recovery efforts using the EUI.
Once you have resolved the underlying problem, you can recover the CSC using the usual command sequence:

* ``standby``: the CSC disconnects (if connected).
* ``start``: the CSC connects.
* ``enable``: the CSC tries to clear the error, and, if successful, enables the low-level controller.
  If the CSC cannot clear the error state, it remains in disabled state.
  Once you have resolved the underlying problem, you can issue the ``enable`` command to try again (without having to go back to standby state).
