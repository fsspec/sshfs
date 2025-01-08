import textwrap
from pathlib import Path
from sshfs.config import parse_config

def make_config(tmpdir: Path, host: str, source: str):
    """Create and parse an SSH config file for a given host."""
    config_path = tmpdir / "ssh_config"
    with config_path.open("w") as stream:
        stream.write(textwrap.dedent(source))

    return parse_config(host=host, config_files=[config_path])

def test_config(tmpdir: Path):
    """Test parsing of a simple SSH configuration."""
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

def test_config_expansions(tmpdir: Path):
    """Test parsing of SSH configuration with ProxyCommand and expansions."""
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

    expected_command = "ssh proxy nc base.dvc.org 222".split()
    assert config.get("ProxyCommand").split() == expected_command
