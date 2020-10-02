import os

from setuptools import setup, find_namespace_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(filename):
    return open(os.path.join(os.path.dirname(__file__), filename)).read()


setup(
    name='generator-pkg-485652-Daniel-Kosc',
    author='Daniel Kosc',
    author_email='485652@mail.muni.cz',
    description='',
    long_description=read('README.md'),
    packages=find_namespace_packages(include=['./*.txt','./*.yml'], exclude=['tests']),
    install_requires=['pyYAML'],
    python_requires='>=3.6',
    zip_safe=False
)
