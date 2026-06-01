"""Parse a host inventory file (TOML) into a list of HostSpec entries.

Example hosts.toml:

    [[host]]
    ssh = "robotruck@100.64.0.6"
    name = "H002"                  # optional label; otherwise the detected hostname
    identity_file = "~/.ssh/id_rsa"  # optional
    port = 22                       # optional
    remote_log_dir = "/var/log/system-resource-monitor"  # optional
    ssh_options = ["ConnectTimeout=10"]                  # optional
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class HostSpec:
    ssh: str
    name: Optional[str] = None
    identity_file: Optional[str] = None
    port: Optional[int] = None
    remote_log_dir: Optional[str] = None
    ssh_options: Tuple[str, ...] = field(default_factory=tuple)


def host_spec_from_target(target: str, *, identity_file=None, port=None, remote_log_dir=None, ssh_options=()) -> HostSpec:
    """Build a HostSpec from a command-line user@host target plus shared SSH options."""
    return HostSpec(
        ssh=target,
        name=None,
        identity_file=identity_file,
        port=port,
        remote_log_dir=remote_log_dir,
        ssh_options=tuple(ssh_options),
    )


def load_inventory(path: Path) -> List[HostSpec]:
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    entries = data.get("host")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Inventory {path} must contain at least one [[host]] entry with an 'ssh' field")

    specs: List[HostSpec] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Inventory {path}: [[host]] entry #{index + 1} is not a table")
        ssh = entry.get("ssh")
        if not isinstance(ssh, str) or not ssh.strip():
            raise ValueError(f"Inventory {path}: [[host]] entry #{index + 1} is missing a non-empty 'ssh' field")
        ssh_options = entry.get("ssh_options", [])
        if not isinstance(ssh_options, list):
            raise ValueError(f"Inventory {path}: 'ssh_options' for {ssh} must be a list of strings")
        specs.append(
            HostSpec(
                ssh=ssh.strip(),
                name=entry.get("name"),
                identity_file=entry.get("identity_file"),
                port=entry.get("port"),
                remote_log_dir=entry.get("remote_log_dir"),
                ssh_options=tuple(str(option) for option in ssh_options),
            )
        )
    return specs
