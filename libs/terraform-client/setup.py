import os
from setuptools import setup, find_namespace_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(filename):
    return open(os.path.join(os.path.dirname(__file__), filename)).read()


setup(
    name='crczp-terraform-client',
    author='cybersecurityhub.cz',
    description='Terraform Client',
    long_description=read('README.md'),
    packages=find_namespace_packages(include=['crczp.*'], exclude=['tests']),
    package_data={'crczp.terraform_driver': ['templates/*']},
    install_requires=[
        'crczp-python-commons~=1.0.1',
        'crczp-openstack-lib~=1.0.0',
        'Jinja2',
        'crczp-aws-lib>=1.0.1',
    ],
    python_requires='>=3',
    zip_safe=False
)
