from __future__ import annotations

import socket

import pytest

from tracefetch.config import Policy
from tracefetch.errors import PolicyBlockedError, TraceFetchError
from tracefetch.security import normalize_url, validate_public_url


def test_normalize_url_canonicalizes_idn_default_port_and_fragment() -> None:
    assert (
        normalize_url("HTTPS://BÜCHER.example:443/a?q=1#section")
        == "https://xn--bcher-kva.example/a?q=1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https://user:secret@example.com/",
        "https:///missing-host",
    ],
)
def test_normalize_url_rejects_unsafe_forms(url: str) -> None:
    with pytest.raises(TraceFetchError):
        normalize_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_literal_addresses_are_blocked(url: str) -> None:
    with pytest.raises(PolicyBlockedError):
        validate_public_url(url, Policy())


def test_dns_answer_with_any_private_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers)

    with pytest.raises(PolicyBlockedError, match="non-public"):
        validate_public_url("https://example.test/", Policy())


def test_domain_allow_and_deny_lists_apply_to_subdomains() -> None:
    policy = Policy(
        deny_private_networks=False,
        allowed_domains=["example.com"],
        denied_domains=["blocked.example.com"],
    )
    assert validate_public_url("https://docs.example.com/a", policy).startswith(
        "https://docs.example.com/"
    )
    with pytest.raises(PolicyBlockedError):
        validate_public_url("https://other.test/", policy)
    with pytest.raises(PolicyBlockedError):
        validate_public_url("https://blocked.example.com/", policy)


def test_private_network_policy_can_be_explicitly_disabled() -> None:
    policy = Policy(deny_private_networks=False)
    assert validate_public_url("http://192.168.1.10/a", policy) == "http://192.168.1.10/a"
