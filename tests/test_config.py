import shlex
import textwrap

from sshfs.config import parse_config


def make_config(tmpdir, host, source):
    with open(tmpdir / "ssh_config", "w") as stream:
        stream.write(textwrap.dedent(source))

    return parse_config(host=host, config_files=[tmpdir / "ssh_config"])


def test_config(tmpdir):
    config = make_config(
        tmpdir,
        "iterative.ai",
        """
    Host iterative.ai
        HostName ssh.iterative.ai
        User batuhan
        Port 888
        IdentityFile ~/.ssh/id_rsa_it
    """,
    )

    assert config.get("Hostname") == "ssh.iterative.ai"
    assert config.get("User") == "batuhan"
    assert config.get("Port") == 888
    assert config.get("IdentityFile") == ["~/.ssh/id_rsa_it"]


def test_config_expansions(tmpdir):
    config = make_config(
        tmpdir,
        "base",
        """
    Host proxy
        User user_proxy
        HostName proxy.dvc.org

    Host base
        User user_base
        HostName base.dvc.org
        Port 222
        ProxyCommand ssh proxy nc %h %p
    """,
    )
    proxy_command = config.get("ProxyCommand")
    # asyncssh >= 2.24.2 returns the command split into arguments
    if isinstance(proxy_command, str):
        proxy_command = shlex.split(proxy_command)
    assert proxy_command == ["ssh", "proxy", "nc", "base.dvc.org", "222"]
