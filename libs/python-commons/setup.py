import os
from setuptools import setup, find_namespace_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(filename):
    return open(os.path.join(os.path.dirname(__file__), filename)).read()


setup(
    name='crczp-python-commons',
    author='cybersecurityhub.cz',
    description='Python Commons',
    long_description=read('README.md'),
    packages=find_namespace_packages(include=['crczp.*'], exclude=['tests']),
    install_requires=[
        'crczp-topology-definition~=0.1.4',
    ],
    python_requires='>=3',
    zip_safe=False
)
