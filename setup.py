#!/usr/bin/env python
import os
import sys
from setuptools import setup, find_packages


long_description = open(
    os.path.join(
        os.path.dirname(__file__),
        'README.rst'
    )
).read()

requirements = [
    'prompt_toolkit>=2.0.0,<2.1.0',
    'pyte>=0.5.1',
    'six>=1.9.0',
]

# Install yawinpty on Windows only.
if sys.platform.startswith('win'):
    requirements.append('yawinpty')


setup(
    name='ptterm',
    author='Jonathan Slenders',
    version='0.1',
    license='LICENSE',
    url='https://github.com/jonathanslenders/ptterm',
    description='Terminal emulator for prompt_toolkit.',
    long_description=long_description,
    packages=find_packages('.'),
    install_requires = requirements,
)
