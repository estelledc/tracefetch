# Release process

TraceFetch publishes immutable Git tags and GitHub Release wheel/sdist assets. PyPI is a separate
channel and must not be claimed until trusted publishing is configured and a live package is
verified.

## 1.0 acceptance gates

1. `make check` passes on Python 3.11, 3.12, and 3.13.
2. `python scripts/check_version.py --tag vMAJOR.MINOR.PATCH` confirms pyproject, package,
   changelog, and tag coherence.
3. A clean clone can install the wheel and execute `tracefetch --version`, local search, doctor,
   schema, and provider-example smoke tests.
4. Public search runs at least one configured provider and reports partial failure rather than
   hiding it.
5. A public-source fetch produces a bundle whose independent `verify` result is `valid=true`.
6. `git diff --check`, schema drift, strict mypy, Ruff, coverage >= 80%, wheel, and sdist gates pass.
7. The release commit contains no credentials, private URLs, absolute developer paths, or live
   internal search output.

## Automated tag flow

Pushing `vMAJOR.MINOR.PATCH` runs `.github/workflows/release.yml`. The workflow repeats the full
gate, verifies that the tag matches the package version, builds wheel/sdist, installs the wheel in
an isolated environment, and creates the GitHub Release with both artifacts.

If any step fails, no successful GitHub Release is claimed. Delete or replace a failed unpublished
tag only after reviewing whether anyone consumed it; never silently overwrite a published release
asset or tag.
