from setuptools import setup
import setuptools_scm


scm_version_template = """# Generated by setuptools_scm
__all__ = ["__version__"]

__version__ = "{version}"
"""

setup(
    version=setuptools_scm.get_version(),
    use_scm_version={
        "write_to": "python/lsst/ts/hexrotcomm/version.py",
        "write_to_template": scm_version_template,
    },
)
