from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tracefetch import __version__
from tracefetch.config import Policy, load_policy
from tracefetch.contracts import CrawlReceipt, ErrorEnvelope, EvidenceReceipt, SearchEnvelope
from tracefetch.crawl import crawl_site
from tracefetch.doctor import run_doctor
from tracefetch.errors import InvalidInputError, TraceFetchError, VerificationError
from tracefetch.pipeline import fetch_to_bundle, ingest_to_bundle
from tracefetch.search import search_sources
from tracefetch.verify import crawl_verification_payload, verification_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracefetch",
        description="Search, fetch, normalize, and verify provenance-bearing evidence bundles.",
    )
    parser.add_argument("--version", action="version", version=f"tracefetch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Inspect available search and reader adapters")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    search = subparsers.add_parser("search", help="Discover candidate sources")
    search.add_argument("query")
    search.add_argument("--provider", choices=["auto", "all", "exa", "github"], default="auto")
    search.add_argument("--limit", type=_positive_int, default=5)
    search.add_argument("--json", action="store_true", dest="as_json")

    fetch = subparsers.add_parser("fetch", help="Fetch one public URL into an evidence bundle")
    fetch.add_argument("url")
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--reader", choices=["auto", "direct", "jina", "firecrawl"], default="auto")
    _policy_arguments(fetch)
    fetch.add_argument("--json", action="store_true", dest="as_json")

    ingest = subparsers.add_parser("ingest", help="Ingest one explicit local file")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source-url")
    ingest.add_argument("--output", type=Path, required=True)
    _policy_arguments(ingest, remote_flags=False)
    ingest.add_argument("--json", action="store_true", dest="as_json")

    crawl = subparsers.add_parser("crawl", help="Run a bounded same-origin crawl")
    crawl.add_argument("url")
    crawl.add_argument("--output", type=Path, required=True)
    crawl.add_argument("--reader", choices=["auto", "direct", "jina", "firecrawl"], default="auto")
    crawl.add_argument("--max-pages", type=_positive_int, default=None)
    crawl.add_argument("--max-depth", type=_nonnegative_int, default=None)
    crawl.add_argument("--resume", action="store_true")
    _policy_arguments(crawl)
    crawl.add_argument("--json", action="store_true", dest="as_json")

    verify = subparsers.add_parser("verify", help="Verify an evidence or crawl bundle")
    verify.add_argument("path", type=Path)
    verify.add_argument("--json", action="store_true", dest="as_json")

    schema = subparsers.add_parser("schema", help="Print a machine-readable contract schema")
    schema.add_argument("contract", choices=["evidence", "search", "crawl"])
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
        if result is not None:
            _emit_success(result, bool(getattr(args, "as_json", False)))
    except TraceFetchError as exc:
        envelope = ErrorEnvelope(
            error=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        )
        if bool(getattr(args, "as_json", False)):
            print(envelope.model_dump_json(indent=2), file=sys.stderr)
        else:
            print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except KeyboardInterrupt as exc:
        print("error[interrupted]: interrupted by user", file=sys.stderr)
        raise SystemExit(130) from exc


def dispatch(args: argparse.Namespace) -> Any:
    if args.command == "doctor":
        return run_doctor()
    if args.command == "search":
        return search_sources(args.query, provider=args.provider, limit=args.limit)
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
            "evidence": EvidenceReceipt,
            "search": SearchEnvelope,
            "crawl": CrawlReceipt,
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


def _emit_success(value: Any, as_json: bool) -> None:
    if hasattr(value, "model_dump_json"):
        if as_json:
            print(value.model_dump_json(indent=2))
            return
        if isinstance(value, EvidenceReceipt):
            print(f"{value.status}: {value.receipt_id}")
            return
        if isinstance(value, SearchEnvelope):
            for candidate in value.candidates:
                print(f"{candidate.rank}. {candidate.title} — {candidate.url}")
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
