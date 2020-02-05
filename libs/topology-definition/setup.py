import os

from setuptools import setup, find_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(filename):
    return open(os.path.join(os.path.dirname(__file__), filename)).read()


setup(
    name='kypo-topology-definition',
    author='Daniel Tovarnak',
    author_email='tovarnak@ics.muni.cz',
    description='',
    long_description=read('README.md'),
    packages=find_packages(exclude=['tests']),
    install_requires=[
        'yamlize',
        'structlog',
        'netaddr'
    ],
    python_requires='>=3',
    zip_safe=False
)
