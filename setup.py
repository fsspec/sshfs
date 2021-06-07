from pathlib import Path

from setuptools import setup

setup(
    name="sshfs",
    version="2021.06.0a11",
    description="SSH Filesystem -- Async SSH/SFTP backend for fsspec",
    license="Apache License 2.0",
    install_requires=Path("requirements.txt").read_text().splitlines(),
    long_description=Path("README.md").read_text(),
    long_description_content_type="text/markdown",
    py_modules=["sshfs"],
    extras_require={
        "bcrypt": ["asyncssh[bcrypt]"],
        "fido2": ["asyncssh[fido2]"],
        "gssapi": ["asyncssh[gssapi]"],
        "libnacl": ["asyncssh[libnacl]"],
        "pkcs11": ["asyncssh[python-pkcs11]"],
        "pyOpenSSL": ["asyncssh[pyOpenSSL]"],
        "pywin32": ["asyncssh[pywin32]"],
    },
)
