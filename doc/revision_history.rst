.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm.revision_history:

##############################
ts_hexrotcomm Revision History
##############################

v0.2.1
======

Allow the ``connect_callback`` argument of `OneClientServer` to be `None`.
That actually worked before, but it was not documented and resulted in an exception being logged for each callback.

v0.2.0
======

Add `BaseCsc.make_command` and `BaseCsc.run_multiple_commands`.
Update for Rotator XML refinements.
Disambiguate the use of `cmd` (*warning*: not backwards compatible):

* Rename Command.cmd to Command.code
* Rename cmd argument to command for BaseCsc.run_command
  and CommandTelemetryServer.put_command

Requires:
* ts_salobj 5
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using `make_idl_file.py Rotator` (for SimpleCsc and unit tests)

v0.1.1
======

Fix an error in the MockController's CLEAR_ERROR command.

Requires
* ts_salobj 5
* ts_idl 1
* Rotator IDL file, e.g. built using `make_idl_file.py Rotator` (for SimpleCsc and unit tests)

v0.1.0
======

Initial release.

Requires
* ts_salobj 5
* ts_idl 1
* Rotator IDL file, e.g. built using `make_idl_file.py Rotator`
