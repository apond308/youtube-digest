"""CLI entry point for ``python -m youtube_digest``."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from . import setup_logging
from .pipeline import main as run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube_digest",
        description="YouTube Digest runner and server mode",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "run",
        help="Run one pipeline cycle (daily digest batch).",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run FastAPI server with scheduler for 24/7 operation.",
    )
    serve_parser.add_argument("--host", default="0.0.0.0", help="Server host.")
    serve_parser.add_argument("--port", type=int, default=8080, help="Server port.")
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only).",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if os.getenv("DEBUG") else logging.INFO
    setup_logging(level)

    command = args.command or "run"

    if command == "serve":
        uvicorn.run(
            "youtube_digest.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,
        )
        return 0

    return run_pipeline()


if __name__ == "__main__":
    sys.exit(main())
