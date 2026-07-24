# Security policy

TraceFetch 1.x is the supported stable line. Security fixes use a patch release when contracts stay
compatible; a schema or trust-boundary break requires a new major version. The 0.1 alpha line is
unsupported after the 1.0.0 release.

Report vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/estelledc/tracefetch/security/advisories/new).
Include the affected version, threat scenario, minimal reproduction, and whether credentials,
private-network access, path escape, or remote data disclosure is involved.

Do not open a public issue or attach live credentials, cookies, private URLs, proprietary source
content, or exploit output. If private reporting is unavailable, contact the repository owner by a
private channel and send only enough information to establish contact first.

The following are important limitations but not, by themselves, vulnerabilities:

- a public site blocks or rate-limits the declared TraceFetch user agent;
- a remote reader returns incomplete or transformed content;
- normalized content contains prompt injection;
- an explicitly configured command provider can access the same local account state as its caller;
- robots allows a URL whose terms or license prohibit reuse;
- DNS rebinding remains possible without network-layer egress controls.

See [the security model](docs/security-model.md) for the intended trust boundary.
