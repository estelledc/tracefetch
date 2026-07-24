from __future__ import annotations

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit, urlunsplit

from tracefetch.config import Policy
from tracefetch.errors import InvalidInputError, PolicyBlockedError

BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}


def _canonical_host(host: str) -> str:
    try:
        return host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidInputError(f"invalid internationalized hostname: {host!r}") from exc


def normalize_url(url: str) -> str:
    raw = url.strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise InvalidInputError(f"invalid URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidInputError("only http and https URLs are supported")
    if not parsed.hostname:
        raise InvalidInputError("URL host is required")
    if parsed.username is not None or parsed.password is not None:
        raise PolicyBlockedError("credentials embedded in URLs are not allowed")

    host = _canonical_host(parsed.hostname)
    display_host = f"[{host}]" if ":" in host else host
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    clean = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=parsed.path or "/",
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(clean)


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def _domain_matches(host: str, domain: str, allow_subdomains: bool) -> bool:
    return host == domain or (allow_subdomains and host.endswith(f".{domain}"))


def host_allowed(url: str, policy: Policy) -> bool:
    host = _canonical_host(urlsplit(url).hostname or "")
    if any(_domain_matches(host, item, policy.allow_subdomains) for item in policy.denied_domains):
        return False
    return not policy.allowed_domains or any(
        _domain_matches(host, item, policy.allow_subdomains) for item in policy.allowed_domains
    )


def validate_public_url(url: str, policy: Policy) -> str:
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    if policy.require_https and parsed.scheme != "https":
        raise PolicyBlockedError("HTTPS is required by policy")
    if not host_allowed(normalized, policy):
        raise PolicyBlockedError("URL is blocked by domain policy")

    host = _canonical_host(parsed.hostname or "")
    if host in BLOCKED_HOSTS or host.endswith(".local"):
        raise PolicyBlockedError("localhost and .local targets are blocked")
    if not policy.deny_private_networks:
        return normalized

    try:
        if not _is_public_ip(host):
            raise PolicyBlockedError("non-public IP targets are blocked")
        return normalized
    except ValueError:
        pass

    try:
        records = socket.getaddrinfo(host, parsed.port or 0, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PolicyBlockedError("DNS resolution failed", retryable=True) from exc
    addresses = {str(record[4][0]) for record in records}
    if not addresses:
        raise PolicyBlockedError("DNS resolution returned no addresses", retryable=True)
    blocked = sorted(address for address in addresses if not _is_public_ip(address))
    if blocked:
        raise PolicyBlockedError(
            "host resolves to a non-public address",
            details={"blocked_addresses": blocked},
        )
    return normalized
