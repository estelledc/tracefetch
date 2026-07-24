from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from tracefetch.config import Policy
from tracefetch.security import validate_public_url


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    status: str
    allowed: bool
    message: str


class RobotsPolicy:
    """RFC 9309-oriented robots evaluator with conservative network failure handling."""

    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser | RobotsDecision] = {}

    def evaluate(self, url: str, policy: Policy) -> RobotsDecision:
        if not policy.obey_robots:
            return RobotsDecision("unchecked", True, "robots checks disabled by policy")
        parsed = urlsplit(url)
        authority = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        cached = self._cache.get(authority)
        if cached is None:
            cached = self._load(authority, policy)
            self._cache[authority] = cached
        if isinstance(cached, RobotsDecision):
            return cached
        product_token = policy.user_agent.split("/", 1)[0]
        allowed = cached.can_fetch(product_token, url)
        return RobotsDecision(
            "allowed" if allowed else "blocked",
            allowed,
            "robots.txt allows URL" if allowed else "robots.txt disallows URL",
        )

    def _load(self, authority: str, policy: Policy) -> RobotFileParser | RobotsDecision:
        current = validate_public_url(f"{authority}/robots.txt", policy)
        headers = {"User-Agent": policy.user_agent, "Accept": "text/plain,*/*;q=0.1"}
        try:
            with httpx.Client(
                timeout=policy.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for _ in range(policy.max_redirects + 1):
                    response = client.get(current, headers=headers)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            break
                        current = validate_public_url(urljoin(current, location), policy)
                        continue
                    if 400 <= response.status_code < 500:
                        return RobotsDecision("unavailable", True, "robots.txt unavailable")
                    if response.status_code >= 500:
                        message = f"robots.txt status {response.status_code}"
                        return self._unreachable(policy, message)
                    if not response.is_success:
                        return self._unreachable(policy, "robots.txt request failed")
                    content = response.content[: 512 * 1024]
                    parser = RobotFileParser()
                    parser.set_url(current)
                    parser.parse(content.decode("utf-8", errors="replace").splitlines())
                    return parser
        except (httpx.HTTPError, ValueError) as exc:
            return self._unreachable(policy, f"robots.txt unreachable: {exc}")
        return self._unreachable(policy, "robots.txt redirect limit exceeded")

    @staticmethod
    def _unreachable(policy: Policy, message: str) -> RobotsDecision:
        allowed = not policy.robots_fail_closed
        return RobotsDecision("unavailable", allowed, message)
