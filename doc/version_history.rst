.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm.version_history:

###############
Version History
###############

v0.5.1
======

Changes:

* Add ``tests/test_black.py`` to verify that files are formatted with black.
  This requires ts_salobj 5.11 or later.
* Update `BaseCscTestCase.check_bin_script` to be compatible with ts_salobj 5.12.
* Fix f strings with no {}.
* Update ``.travis.yml`` to remove ``sudo: false`` to github travis checks pass once again.

Requires:

* ts_salobj 5.11
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.5.0
======

Changes:

* Make `BaseCsc` forward compatible with ts_xml 5.2 and with explicitly listing which generic topics are used.

Requires:

* ts_salobj 5
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.4.0
======

Changes:

* The clearError command in the mock controller now transitions to STANDBY instead of OFFLINE/AVAILABLE.
  This matches a recent change to the rotator controller and a planned change to the hexapod controller.
* Include conda package build configuration.
* Added a Jenkinsfile to support continuous integration and to build conda packages.
* Fixed a bug in `OneClientServer.close`: it would fail if called twice.

Requires:

* ts_salobj 5
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.3.0
======

Major changes:

* Allow the ``connect_callback`` argument of `OneClientServer` to be `None`.
  That actually worked before, but it was not documented and resulted in an exception being logged for each callback.
* Code formatted by ``black``, with a pre-commit hook to enforce this. See the README file for configuration instructions.

Requires:

* ts_salobj 5
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

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
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.1.1
======

Fix an error in the MockController's CLEAR_ERROR command.

Requires:

* ts_salobj 5
* ts_idl 1
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.1.0
======

Initial release.

Requires:

* ts_salobj 5
* ts_idl 1
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)
