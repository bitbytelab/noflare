"""Command line entrypoint for the `noflare` package.

Provides a simple CLI wrapper around `uvicorn` so the package can
be executed after `pip install .` as `python -m noflare` or (if a
console script is added) as `noflare`.
"""
from __future__ import annotations

import os
import argparse
from importlib.metadata import PackageNotFoundError, version

import uvicorn

try:
	__version__ = version("noflare")
except PackageNotFoundError:
	__version__ = "1.3.0"


HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8191"))
LOCALE = os.getenv("LOCALE", "en-US")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
DEBUG = str(os.getenv("DEBUG", "false")).lower() == "true"
SANDBOX = str(os.getenv("SANDBOX", "false")).lower() == "true"
HEADLESS = str(os.getenv("HEADLESS", "false")).lower() == "true"
DISABLE_MEDIA = str(os.getenv("DISABLE_MEDIA", "false")).lower() == "true"


def parse_args(argv=None):
	p = argparse.ArgumentParser(prog="NoFlare - A Cloudflare Bypass Proxy")
	p.add_argument("--host", default=HOST, help="Host to bind to default: 0.0.0.0")
	p.add_argument("--port", type=int, default=PORT, help="Port to listen on default: 8191")
	p.add_argument("--lang", "--locale", default=LOCALE, help="Language/locale default: en-US")
	p.add_argument("--headless", action="store_true", default=HEADLESS, help="Run browser in headless mode")
	p.add_argument("--sandbox", action="store_true", default=SANDBOX, help="Browser Sandbox Mode default: False")
	p.add_argument("--data-dir", help="User data directory for browser profile. default: (temp dir for each run)")
	p.add_argument("--max-workers", type=int, default=MAX_WORKERS, help="Maximum number of worker threads default: 4")
	p.add_argument("--disable-media", action="store_true", default=DISABLE_MEDIA, help="Disable loading (Images/Media)")
	p.add_argument("--debug", action="store_true", default=DEBUG, help="Debug mode: Run with reload and debug logging")
	p.add_argument("-v", "-V", "--version", action="version", version=f"%(prog)s: v{__version__}")
	return p.parse_args(argv)


def main(argv=None):
	args = parse_args(argv)

	os.environ["LOCALE"] = args.lang
	os.environ["DEBUG"] = str(args.debug)
	os.environ["SANDBOX"] = str(args.sandbox)
	os.environ["HEADLESS"] = str(args.headless)
	os.environ["MAX_WORKERS"] = str(args.max_workers)
	os.environ["DISABLE_MEDIA"] = str(args.disable_media)

	if args.data_dir:
		os.environ["DATA_DIR"] = args.data_dir

	if args.debug:
		os.environ["HEADLESS"] = "false"

	uvicorn.run(
		"noflare.noflare:app",
		host=args.host,
		port=args.port,
		reload=args.debug,
		log_level="debug" if args.debug else os.getenv("LOG_LEVEL", "info").lower(),
	)


if __name__ == "__main__":
	main()
