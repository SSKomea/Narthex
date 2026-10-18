"""Compatibility setup script for offline/legacy editable installs.

The canonical project metadata lives in ``pyproject.toml``.  This shim lets a
Python 3.12 environment install the project without downloading a build
backend, which is useful in restricted networks.
"""

from setuptools import find_packages, setup

setup(
    name="narthex",
    version="0.1.0",
    description="A runtime-switchable Cathedral/Bazaar multi-agent orchestration runtime",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"narthex": ["py.typed", "data/*.yaml"]},
    python_requires=">=3.12",
)
