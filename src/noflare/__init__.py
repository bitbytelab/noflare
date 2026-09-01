"""NoFlare package.

Expose the ASGI `app` from the core module and provide package
metadata for runtime import and packaging.

This module intentionally keeps imports minimal so importing the
package does not eagerly start the browser process; the FastAPI
app is imported on demand by ASGI servers such as `uvicorn`.
"""
from importlib.metadata import PackageNotFoundError, version

try:
	__version__ = version("noflare")
except PackageNotFoundError:
	__version__ = "1.1.2"

from .noflare import app

__all__ = ["__version__", "app"]
