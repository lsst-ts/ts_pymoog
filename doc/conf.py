"""Sphinx configuration file for an LSST stack package.

This configuration only affects single-package Sphinx documentation builds.
"""

from documenteer.conf.pipelinespkg import *  # noqa
import lsst.ts.hexrotcomm  # noqa

project = "ts_hexrotcomm"
html_theme_options["logotext"] = project  # noqa
html_title = project
html_short_title = project
doxylink = {}  # Avoid warning: Could not find tag file _doxygen/doxygen.tag

intersphinx_mapping["ts_xml"] = ("https://ts-xml.lsst.io", None)  # noqa
intersphinx_mapping["ts_salobj"] = ("https://ts-salobj.lsst.io", None)  # noqa
intersphinx_mapping["ts_tcpip"] = ("https://ts-tcpip.lsst.io", None)  # noqa
