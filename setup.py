#!/usr/bin/env python

import os
import sys

from setuptools import setup, find_packages

EXCLUDE_FROM_PACKAGES = []

setup(
    name='ableton_log',
    version='0.1-dev',
    packages=find_packages(),
    install_requires=[
        "lxml>=3.4"
    ],
    include_package_data = True,
    zip_safe=False,
    entry_points={'console_scripts': [
        'abletondiff = ableton_log.ableton_diff:run',
    ]}
)
