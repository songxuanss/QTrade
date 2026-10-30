"""Configure tested DNS only inside the dedicated Ubuntu 26 distro.

WSL's inherited router DNS returned no usable answer, while these resolvers
answered. Replace the guest link atomically rather than editing a shared target.
No Windows DNS, firewall, other distro, or running application is reset.
"""

import configparser
import os
import socket
import struct
import tempfile
from pathlib import Path


def atomic(path: Path, body: str) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    if os.environ.get("WSL_DISTRO_NAME") != "QTrade-Ubuntu-26" or os.geteuid() != 0:
        raise SystemExit("Run as root in the dedicated Ubuntu 26 distro")
    name = "api.mairuiapi.com"
    question = b"".join(bytes([len(part)]) + part.encode() for part in name.split("."))
    query = struct.pack("!HHHHHH", 1234, 256, 1, 0, 0, 0) + question + b"\0\0\1\0\1"
    servers = ("223.5.5.5", "1.1.1.1")
    for server in servers:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(3)
            connection.sendto(query, (server, 53))
            response, _ = connection.recvfrom(4096)
        identity, flags, _, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
        if identity != 1234 or flags & 15 or not answers:
            raise SystemExit("Resolver did not return a successful answer; DNS left unchanged")
    configuration = Path("/etc/wsl.conf")
    resolver = Path("/etc/resolv.conf")
    for path in (configuration, resolver):
        backup = path.with_name(path.name + ".qtrade-before")
        if not backup.exists():
            atomic(backup, path.read_text())
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(configuration)
    if not parser.has_section("network"):
        parser.add_section("network")
    # Preserve the documented key spelling instead of ConfigParser's default
    # lowercasing; migrate our earlier local spelling if it is present.
    parser.remove_option("network", "generateresolvconf")
    parser.set("network", "generateResolvConf", "false")
    from io import StringIO

    rendered = StringIO()
    parser.write(rendered)
    atomic(configuration, rendered.getvalue())
    atomic(resolver, "".join(f"nameserver {server}\n" for server in servers))
    socket.getaddrinfo(name, 443)
    print("Ubuntu-only DNS configured; MaiRui hostname resolves; services not restarted")


if __name__ == "__main__":
    main()
