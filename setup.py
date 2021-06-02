from pathlib import Path

from setuptools import setup

setup(
    name="sshfs",
    version="2021.06.0",
    description="SSH File System -- SSH/SFTP backend for fsspec",
    license="Apache License 2.0",
    install_requires=Path("requirements.txt").read_text(),
    python_modules=["sshfs"],
)
