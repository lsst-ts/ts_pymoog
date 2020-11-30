.. py:currentmodule:: lsst.ts.hexrotcomm

.. _lsst.ts.hexrotcomm.version_history:

###############
Version History
###############

v0.12.1
=======

* Update Jenkinsfile.conda to use Jenkins Shared Library
* Pinned the ts-idl and ts-salobj version in conda recipe

v0.12.0
=======

* Update the mock controller to make the time used in update_telemetry match the time in the header:

    * Update `CommandTelemetryClient.update_and_get_header` to return the current time in addition to the header,
      and update the call to `update_telemetry` to provide that time.
    * Update `BaseMockController,update_telemetry` and `SimpleMockController.update_telemetry` to receive time as an argument.

Requires:

* ts_salobj 6.1
* ts_idl 2.2
* ts_xml 7
* MTRotator IDL file, e.g. built using ``make_idl_file.py MTRotator`` (for SimpleCsc and unit tests)

v0.11.0
=======

* Update for ts_xml 7 and ts_idl 2.2:

    * Rename SAL component and ts_idl enum module ``Rotator`` to ``MTRotator``.

Requires:

* ts_salobj 6.1
* ts_idl 2.2
* ts_xml 7
* MTRotator IDL file, e.g. built using ``make_idl_file.py MTRotator`` (for SimpleCsc and unit tests)

v0.10.0
=======

* Update for ts_salobj 6.1.
* Update the handling of initial_state in `BaseCsc`:

    * If initial_state != OFFLINE then report all transitional summary states and controller states at startup.
    * Require initial_state = OFFLINE unless simulating.
* Add `BaseCscTestCase` with overridden versions of:

    * `BaseCscTestCase.make_csc`: read all but the final controller state at startup,
    * `BaseCscTestCase.check_bin_script`: set ``default_initial_state``.

Requires:

* ts_salobj 6.1
* ts_idl 2
* ts_xml 4.6 - 6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.9.0
======

* Add `close_stream_writer` function that closes an `asyncio.StreamWriter` and waits for it to close.
* Update code to wait for stream writers to close.

Requires:

* ts_salobj 5.11 - 6.0
* ts_idl 1 (with salobj 5) or 2 (with salobj 6)
* ts_xml 4.6 - 6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.8.0
======

Backward-incompatible changes:

* Remove ``BaseCscTestCase`` and ``CscCommander`` classes; use the versions in ts_salobj instead.
* Bug fix: `BaseCsc.get_config_pkg` returned "ts_config_ocs" instead of "ts_config_mttcs".

Changes:

* Add missing call to ``begin_start`` to `BaseCsc.do_start`.
* Make `BaseCsc.fault` raise `NotImplementedError`, since the low-level controller maintains the summary state and offers no command to transition to the FAULT state.

Requires:

* ts_salobj 5.11 - 6
* ts_idl 1 (with salobj 5) or 2 (with salobj 6)
* ts_xml 4.6 - 6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.7.0
======

Changes:

* Make `BaseCsc` a configurable CSC.

Requires:

* ts_salobj 5.11 - 6
* ts_idl 1 (with salobj 5) or 2 (with salobj 6)
* ts_xml 4.6 - 6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.6.0
======

Changes:

* Update for compatibility with ts_salobj 6.

Requires:

* ts_salobj 5.11 - 6
* ts_idl 1 (with salobj 5) or 2 (with salobj 6)
* ts_xml 4.6 - 6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

v0.5.2
======

Changes:

* Add black to conda test dependencies

Requires:

* ts_salobj 5.11
* ts_idl 1
* ts_xml 4.6
* Rotator IDL file, e.g. built using ``make_idl_file.py Rotator`` (for SimpleCsc and unit tests)

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
