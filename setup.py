# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
from codecs import open  # To use a consistent encoding
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the relevant file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Pull requirements from requirements.txt file
with open(path.join(here, 'requirements.txt'), encoding='utf-8') as f:
    install_requires, tests_require = [], []
    now_we_have_dev = False
    for line in f:
        if line.startswith("#"):
            if "test" in line:
                now_we_have_dev = True
        elif now_we_have_dev:
            tests_require.append(line.strip())
        else:
            install_requires.append(line.strip())

setup(
    name='HookTest',
    version="2.0.0",
    description='Library for testing CiteStructure data, using Dapitains library',
    long_description=long_description,
    url='http://github.com/cllg/HookTest',
    author='Thibault Clérice',
    author_email='leponteineptique@gmail.com',
    license='Mozilla Public License Version 2.0',
    packages=find_packages(exclude=("tests")),
    classifiers=[
        "Topic :: Software Development :: Quality Assurance",
        "Topic :: Software Development :: Testing",
        "Topic :: Software Development :: Version Control",
        "Topic :: Text Processing :: Markup :: XML",
        "Topic :: Text Processing :: General",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)"
    ],
    install_requires=install_requires,
    tests_require=tests_require,
    package_data={
        'HookTest': ['hooktest/resources/*.rng']
    },
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'hooktest=HookTest.cli:cli'
        ]
    },
    test_suite="tests",
    zip_safe=False
)
