"""NoFlare package

Expose the ASGI `app` from the core module and provide package
metadata for runtime import and packaging.

This module intentionally keeps imports minimal so importing the
package does not eagerly start the browser process; the FastAPI
app is imported on demand by ASGI servers such as `uvicorn`.
"""
from importlib.metadata import version, PackageNotFoundError

try:
	__version__ = version("noflare")
except PackageNotFoundError:
	__version__ = "0.0.0"

from .noflare import app

__all__ = ["app", "__version__"]
