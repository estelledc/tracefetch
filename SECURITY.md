# Security policy

TraceFetch 0.1.x is the only supported line during alpha development. Security fixes may require a
minor release and receipt-schema migration.

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
- robots allows a URL whose terms or license prohibit reuse;
- DNS rebinding remains possible without network-layer egress controls.

See [the security model](docs/security-model.md) for the intended trust boundary.
