#!/usr/bin/env python3

from setuptools import setup, find_packages

setup(
    name="apollo_lib",
    version="0.1.0",
    packages=["apollo_lib"],
    install_requires=[
        "colorama",
    ],
    entry_points={
        "console_scripts": [
            "apollo=apollo_lib.cli:main",
        ],
    },
)