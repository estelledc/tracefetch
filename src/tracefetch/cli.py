from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from tracefetch import __version__
from tracefetch.config import Policy, load_policy
from tracefetch.contracts import (
    CrawlReceipt,
    DoctorEnvelope,
    ErrorEnvelope,
    EvidenceReceipt,
    ProviderManifest,
    ProviderRequest,
    ProviderResponse,
    SearchEnvelope,
    SearchResultsEnvelope,
)
from tracefetch.crawl import crawl_site
from tracefetch.doctor import run_doctor
from tracefetch.errors import InvalidInputError, TraceFetchError, VerificationError
from tracefetch.pipeline import fetch_to_bundle, ingest_to_bundle
from tracefetch.providers import load_provider_manifest
from tracefetch.search import search_sources
from tracefetch.unified import search_everywhere
from tracefetch.verify import crawl_verification_payload, verification_payload


class AgentArgumentParser(argparse.ArgumentParser):
    """Keep malformed agent calls inside the stable JSON error contract."""

    def error(self, message: str) -> NoReturn:
        raise InvalidInputError(message)


def build_parser() -> AgentArgumentParser:
    parser = AgentArgumentParser(
        prog="tracefetch",
        description="Search, fetch, normalize, and verify provenance-bearing evidence bundles.",
    )
    parser.add_argument("--version", action="version", version=f"tracefetch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Inspect available search and reader adapters")
    doctor.add_argument("--providers", type=Path)
    doctor.add_argument("--root", type=Path, default=Path.cwd())
    _output_arguments(doctor)

    search = subparsers.add_parser("search", help="Search a workspace and approved providers")
    search.add_argument("query")
    search.add_argument("--scope", action="append")
    search.add_argument("--root", type=Path, default=Path.cwd())
    search.add_argument("--provider", choices=["auto", "all", "exa", "github"])
    search.add_argument("--providers", type=Path)
    search.add_argument("--allow-sensitive", action="store_true")
    search.add_argument("--include", action="append", dest="include_globs")
    search.add_argument("--limit", type=_positive_int, default=8)
    _output_arguments(search)

    fetch = subparsers.add_parser("fetch", help="Fetch one public URL into an evidence bundle")
    fetch.add_argument("url")
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--reader", choices=["auto", "direct", "jina", "firecrawl"], default="auto")
    _policy_arguments(fetch)
    _output_arguments(fetch)

    ingest = subparsers.add_parser("ingest", help="Ingest one explicit local file")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source-url")
    ingest.add_argument("--output", type=Path, required=True)
    _policy_arguments(ingest, remote_flags=False)
    _output_arguments(ingest)

    crawl = subparsers.add_parser("crawl", help="Run a bounded same-origin crawl")
    crawl.add_argument("url")
    crawl.add_argument("--output", type=Path, required=True)
    crawl.add_argument("--reader", choices=["auto", "direct", "jina", "firecrawl"], default="auto")
    crawl.add_argument("--max-pages", type=_positive_int, default=None)
    crawl.add_argument("--max-depth", type=_nonnegative_int, default=None)
    crawl.add_argument("--resume", action="store_true")
    _policy_arguments(crawl)
    _output_arguments(crawl)

    verify = subparsers.add_parser("verify", help="Verify an evidence or crawl bundle")
    verify.add_argument("path", type=Path)
    _output_arguments(verify)

    schema = subparsers.add_parser("schema", help="Print a machine-readable contract schema")
    schema.add_argument(
        "contract",
        choices=[
            "crawl",
            "doctor",
            "error",
            "evidence",
            "provider-manifest",
            "provider-request",
            "provider-response",
            "provider-search",
            "search",
        ],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        result = dispatch(args)
        if result is not None:
            _emit_success(result, str(getattr(args, "format", "json")))
    except TraceFetchError as exc:
        envelope = ErrorEnvelope(
            error=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        )
        print(envelope.model_dump_json(indent=2), file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except KeyboardInterrupt as exc:
        envelope = ErrorEnvelope(
            error="interrupted",
            message="interrupted by user",
            retryable=False,
        )
        print(envelope.model_dump_json(indent=2), file=sys.stderr)
        raise SystemExit(130) from exc


def dispatch(args: argparse.Namespace) -> Any:
    if args.command == "doctor":
        manifest, base_dir = load_provider_manifest(args.providers)
        return run_doctor(
            provider_manifest=manifest,
            provider_base_dir=base_dir,
            root=args.root,
        )
    if args.command == "search":
        if args.scope is None and args.provider is not None:
            return search_sources(
                args.query,
                provider=args.provider,
                limit=args.limit,
            )
        manifest, base_dir = load_provider_manifest(args.providers)
        return search_everywhere(
            args.query,
            scopes=args.scope,
            root=args.root,
            public_provider=args.provider or "all",
            limit=args.limit,
            provider_manifest=manifest,
            provider_base_dir=base_dir,
            allow_sensitive=args.allow_sensitive,
            include_globs=args.include_globs,
        )
    if args.command == "fetch":
        policy = _policy_from_args(args)
        return fetch_to_bundle(
            args.url,
            args.output,
            reader=args.reader,
            policy=policy,
        ).receipt
    if args.command == "ingest":
        policy = _policy_from_args(args)
        return ingest_to_bundle(
            args.path,
            args.output,
            source_url=args.source_url,
            policy=policy,
        ).receipt
    if args.command == "crawl":
        policy = _policy_from_args(args)
        updates: dict[str, Any] = {}
        if args.max_pages is not None:
            updates["max_pages"] = args.max_pages
        if args.max_depth is not None:
            updates["max_depth"] = args.max_depth
        if updates:
            policy = policy.model_copy(update=updates)
        return crawl_site(
            args.url,
            args.output,
            reader=args.reader,
            policy=policy,
            resume=args.resume,
        )
    if args.command == "verify":
        return _verify_target(args.path)
    if args.command == "schema":
        model: Any = {
            "crawl": CrawlReceipt,
            "doctor": DoctorEnvelope,
            "error": ErrorEnvelope,
            "evidence": EvidenceReceipt,
            "provider-manifest": ProviderManifest,
            "provider-request": ProviderRequest,
            "provider-response": ProviderResponse,
            "provider-search": SearchEnvelope,
            "search": SearchResultsEnvelope,
        }[args.contract]
        print(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True))
        return None
    raise InvalidInputError(f"unknown command: {args.command}")


def _policy_arguments(parser: argparse.ArgumentParser, *, remote_flags: bool = True) -> None:
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--no-robots", action="store_true")
    if remote_flags:
        parser.add_argument("--allow-remote", action="store_true")
        parser.add_argument("--allow-authenticated", action="store_true")


def _policy_from_args(args: argparse.Namespace) -> Policy:
    policy = load_policy(args.policy)
    updates: dict[str, Any] = {}
    if bool(getattr(args, "no_robots", False)):
        updates["obey_robots"] = False
    if bool(getattr(args, "allow_remote", False)):
        updates["allow_remote_adapters"] = True
    if bool(getattr(args, "allow_authenticated", False)):
        updates["allow_remote_adapters"] = True
        updates["allow_authenticated_adapters"] = True
    return policy.model_copy(update=updates) if updates else policy


def _output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "pretty"], default="json")
    parser.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help=argparse.SUPPRESS,
    )


def _verify_target(path: Path) -> dict[str, Any]:
    if (path / "receipt.json").is_file():
        payload = verification_payload(path)
        if not payload["valid"]:
            raise VerificationError(
                "evidence bundle verification failed",
                details={"failures": payload["failures"]},
            )
        return payload
    crawl_path = path / "crawl-receipt.json"
    if crawl_path.is_file():
        payload = crawl_verification_payload(path)
        if not payload["valid"]:
            raise VerificationError(
                "crawl bundle verification failed",
                details={"failures": payload["failures"]},
            )
        return payload
    raise InvalidInputError("path contains neither receipt.json nor crawl-receipt.json")


def _emit_success(value: Any, output_format: str) -> None:
    if hasattr(value, "model_dump_json"):
        if output_format == "json":
            print(value.model_dump_json(indent=2))
            return
        if isinstance(value, EvidenceReceipt):
            print(f"{value.status}: {value.receipt_id}")
            return
        if isinstance(value, SearchEnvelope):
            for legacy_candidate in value.candidates:
                print(f"{legacy_candidate.rank}. {legacy_candidate.title} — {legacy_candidate.url}")
            return
        if isinstance(value, SearchResultsEnvelope):
            for result_candidate in value.candidates:
                print(
                    f"{result_candidate.rank}. "
                    f"[{result_candidate.scope}/{result_candidate.provider}] "
                    f"{result_candidate.title} — {result_candidate.locator}"
                )
            return
        if isinstance(value, CrawlReceipt):
            completed = sum(page.status == "complete" for page in value.pages)
            print(f"{value.status}: {completed}/{len(value.pages)} pages — {value.crawl_id}")
            return
        print(value.model_dump_json(indent=2))
        return
    if isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(value)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _nonnegative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return value


if __name__ == "__main__":
    main()
