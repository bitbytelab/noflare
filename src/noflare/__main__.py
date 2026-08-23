"""Command line entrypoint for the `noflare` package.

Provides a simple CLI wrapper around `uvicorn` so the package can
be executed after `pip install .` as `python -m noflare` or (if a
console script is added) as `noflare`.
"""
from __future__ import annotations

import os
import argparse

import uvicorn

DEFAULT_PORT = int(os.getenv("PORT", "8191"))


def parse_args(argv=None):
	p = argparse.ArgumentParser(prog="noflare")
	p.add_argument("--host", default="0.0.0.0", help="Host to bind to")
	p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
	p.add_argument("--headless", action="store_true", help="Run browser in headless mode")
	p.add_argument("--data-dir", dest="data_dir", help="User data directory for browser profile")
	p.add_argument("--lang", dest="lang", help="Language/locale to use, e.g. en-US")
	p.add_argument("--proxy", dest="proxy", help="Proxy server URL (e.g. http://host:port)")
	p.add_argument("--debug", action="store_true", help="Run with reload and debug logging")
	return p.parse_args(argv)


def main(argv=None):
	args = parse_args(argv)

	if args.headless:
		os.environ["HEADLESS"] = "true"
	if args.data_dir:
		os.environ["DATA_DIR"] = args.data_dir
	if args.lang:
		os.environ["LANGUAGE"] = args.lang
	if args.proxy:
		os.environ["PROXY_SERVER"] = args.proxy

	log_level = "debug" if args.debug else os.getenv("LOG_LEVEL", "info").lower()

	uvicorn.run(
		"noflare.noflare:app",
		host=args.host,
		port=args.port,
		log_level=log_level,
		reload=args.debug,
	)


if __name__ == "__main__":
	main()

