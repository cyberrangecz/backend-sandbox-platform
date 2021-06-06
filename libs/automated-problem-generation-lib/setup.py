import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="generator",
    version="0.1.14",
    author="Daniel Kosc",
    author_email="485652@mail.muni.cz",
    description="Random generator of selected variables",
    install_requires=['pyYAML', 'better_profanity'],
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://gitlab.fi.muni.cz/kypolab/theses/kosc-automated-problem-generation",
    packages = setuptools.find_packages(),
    package_dir={'generator': './generator'},
    package_data={'generator': ['./*.txt','./*.yml']},
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
