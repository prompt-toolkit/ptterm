#!/usr/bin/env python
import os
from setuptools import setup, find_packages


long_description = open(
    os.path.join(
        os.path.dirname(__file__),
        'README.rst'
    )
).read()


setup(
    name='ptterm',
    author='Jonathan Slenders',
    version='0.1',
    license='LICENSE',
    url='https://github.com/jonathanslenders/',
    description='Terminal emulator for prompt_toolkit.',
    long_description=long_description,
    packages=find_packages('.'),
    install_requires = [
        'prompt_toolkit>=1.0.8,<1.1.0',
        'pyte>=0.5.1',
        'six>=1.9.0',
    ],
)
