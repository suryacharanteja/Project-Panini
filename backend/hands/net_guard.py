"""Public-address checks for untrusted browsing (V2-08), ported from OpenDots `src/browser/security.ts`.

The `isolated` preset and sandboxes may only reach public HTTP(S) hosts on standard ports: no loopback,
private, link-local, carrier-grade NAT, documentation, benchmarking, multicast or reserved ranges, no
`localhost`/`.local` names, no credentials in the URL. Names are resolved and every address must be public.
"""

import ipaddress
import socket
from urllib.parse import urlsplit


class BlockedUrl(ValueError):
    pass


def is_public_address(address: str) -> bool:
    text = address.strip().lower().strip("[]")
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version == 4:
        a, b = ip.packed[0], ip.packed[1]
        return not (
            a == 0
            or a == 10
            or a == 127
            or a >= 224
            or (a == 100 and 64 <= b <= 127)
            or (a == 169 and b == 254)
            or (a == 172 and 16 <= b <= 31)
            or (a == 192 and b in (168, 0, 2))
            or (a == 198 and b in (18, 19, 51))
            or (a == 203 and b == 0)
        )
    if ip.ipv4_mapped is not None:
        return False  # ::ffff:a.b.c.d is judged as IPv4 by the OS; refuse the disguise outright.
    groups = ip.exploded.split(":")
    first, second = int(groups[0], 16), int(groups[1], 16)
    if first == 0x2001 and second < 0x200:
        return False  # 2001::/23 IETF protocol assignments (Teredo, benchmarking, ORCHID...).
    if first == 0x2001 and second in (0x0DB8, 0x0010, 0x0020):
        return False  # Documentation and ORCHID ranges.
    if first == 0x2002:
        return False  # 6to4 can wrap a private IPv4 address.
    return 0x2000 <= first <= 0x3FFF  # Global unicast only.


def resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_url(url: str, *, resolver=resolve) -> str:
    """The URL when it is a public HTTP(S) address on a standard port; otherwise BlockedUrl."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise BlockedUrl("Only public HTTP(S) URLs without credentials are allowed.")
    try:
        port = parts.port
    except ValueError:
        raise BlockedUrl("The URL has an invalid port.") from None
    if port not in (None, 80, 443):
        raise BlockedUrl("Only standard web ports are allowed.")
    host = (parts.hostname or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise BlockedUrl("Local addresses are blocked.")
    try:
        addresses = [host] if _is_ip(host) else resolver(host)
    except OSError:
        raise BlockedUrl("The address could not be resolved.") from None
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise BlockedUrl("Private or special network addresses are blocked.")
    return url


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


# Origins passed to Playwright as defence in depth for the isolated preset (never the boundary itself).
_PRIVATE_HOSTS = ("localhost", "127.0.0.1", "[::1]", "10.*", "192.168.*", "172.16.*", "169.254.*")
# A second layer under check_url: also stops clicks and redirects. Playwright matches the port too, so each host is
# listed both without a port and with ":*" (a bare "http://127.0.0.1" does not match "http://127.0.0.1:8000").
PRIVATE_ORIGINS = ";".join(
    f"{scheme}://{host}{port}"
    for host in _PRIVATE_HOSTS
    for scheme in ("http", "https")
    for port in ("", ":*")
)
