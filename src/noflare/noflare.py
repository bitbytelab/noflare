#!/usr/bin/env python3

import os
import time
import random
import shutil
import socket
import asyncio
import logging
import tempfile
import threading
from contextlib import suppress, asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version

import nodriver as uc
from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse

try:
	__version__ = version("noflare")
except PackageNotFoundError:
	__version__ = "1.3.0"

thread_pool = None
shutdown_event = threading.Event()

# --- Thread Pool Sizing ---
LOCALE = os.getenv("LOCALE", "en-US")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
DEBUG = str(os.getenv("DEBUG", "false")).lower() == "true"
SANDBOX = str(os.getenv("SANDBOX", "false")).lower() == "true"
HEADLESS = str(os.getenv("HEADLESS", "false")).lower() == "true"
DISABLE_MEDIA = str(os.getenv("DISABLE_MEDIA", "false")).lower() == "true"

logging.basicConfig(
    level=getattr(logging, "DEBUG" if DEBUG else os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(levelname)-9s %(threadName)s - %(message)s"
)
for noisy_logger in ("nodriver", "fastapi"):logging.getLogger(noisy_logger).setLevel(logging.WARNING)

class SolveReq(BaseModel):
    url: str
    timeout: int = 95
    disableMedia: bool = DISABLE_MEDIA

@asynccontextmanager
async def lifespan(app: FastAPI):
    global thread_pool
    thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Worker")
    logging.info(f"NoFlare v{__version__} Initialized ThreadPoolExecutor with {MAX_WORKERS} workers.")
    yield
    shutdown_event.set()
    logging.info("Shutting down thread pool...")
    thread_pool.shutdown(wait=False, cancel_futures=True)

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def create_browser_config(data_dir: str, debug_port: int) -> uc.Config:
    config = uc.Config()
    config.lang = LOCALE
    config.port = debug_port
    config.headless = HEADLESS
    config.user_data_dir = data_dir

    config.add_argument("--disable-gpu")
    config.add_argument("--no-first-run")
    config.add_argument("--no-service-autorun")
    config.add_argument("--disable-default-apps")
    config.add_argument("--password-store=basic")
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--disable-background-networking")
    # config.add_argument("--disable-blink-features=AutomationControlled")
    if os.name != "nt" and not SANDBOX:
        config.sandbox = SANDBOX
        config.add_argument("--disable-setuid-sandbox")
    return config

async def async_worker_task(url: str, timeout: int, start_timestamp: int, disable_media: bool) -> dict:
    """The heavy browser logic isolated entirely inside a thread's own event loop."""
    browser = None
    captured_headers = {}
    response_status = 200
    temp_dir = tempfile.mkdtemp(prefix="_nf_")

    asyncio.get_running_loop().set_exception_handler(
        lambda _l, ctx: None
        if type(ctx.get("exception")).__name__ in ("ConnectionError", "ProtocolException")
        else _l.default_exception_handler(ctx)
    )

    async def on_response_received(event: uc.cdp.network.ResponseReceived):
        nonlocal captured_headers, response_status
        if event.type_ == uc.cdp.network.ResourceType.DOCUMENT:
            response_status = event.response.status
            headers = dict(event.response.headers)
            headers["status"] = str(response_status)
            captured_headers = headers
            logging.debug(f"[{url}] Captured new document headers (Status: {response_status})")

    try:
        # Stagger start times to prevent CPU/IO spikes and Port collisions
        await asyncio.sleep(random.uniform(0.1, 0.8))

        debug_port = get_free_port()
        browser = await uc.start(config=create_browser_config(temp_dir, debug_port))
        tab = await browser.get("about:blank")

        clean_ua = (await tab.evaluate("navigator.userAgent")).replace("Headless", "")
        await tab.send(uc.cdp.emulation.set_user_agent_override(user_agent=clean_ua))
        await tab.send(uc.cdp.emulation.set_focus_emulation_enabled(enabled=True))

        tab.add_handler(uc.cdp.network.ResponseReceived, on_response_received)
        await tab.send(uc.cdp.network.enable())

        if disable_media:
            async def on_request_paused(event: uc.cdp.fetch.RequestPaused):
                req_url = event.request.url.lower()

                if "cloudflare" in req_url or "turnstile" in req_url:
                    with suppress(Exception):
                        await tab.send(uc.cdp.fetch.continue_request(request_id=event.request_id))
                    return

                with suppress(Exception):
                    await tab.send(uc.cdp.fetch.fail_request(
                        request_id=event.request_id,
                        error_reason=uc.cdp.network.ErrorReason.BLOCKED_BY_CLIENT
                    ))

            tab.add_handler(uc.cdp.fetch.RequestPaused, on_request_paused)
            await tab.send(uc.cdp.fetch.enable(
                patterns=[
                    uc.cdp.fetch.RequestPattern(resource_type=uc.cdp.network.ResourceType.IMAGE),
                    uc.cdp.fetch.RequestPattern(resource_type=uc.cdp.network.ResourceType.MEDIA),
                ]
            ))
            logging.info(f"[{url}] Media Blocker ENABLED (Images/Media blocked).")

        bypassed = False
        await tab.send(uc.cdp.page.navigate(url=url))
        elapsed = int(time.time() - (start_timestamp / 1000.0))

        while time.time() - (start_timestamp / 1000.0) < timeout:
            if shutdown_event.is_set():
                logging.info(f"[{url}] Aborting worker task due to system shutdown.")
                return {"error": "Server shutting down."}

            elapsed = int(time.time() - (start_timestamp / 1000.0))
            logging.debug(f"--------- timeout: {timeout}s | {elapsed}s elapsed ---------")

            try:
                current_url = await tab.evaluate("window.location.href")
                if "about:blank" in current_url:
                    await asyncio.sleep(0.5)
                    continue

                cookies = await tab.send(uc.cdp.network.get_cookies())
                has_clearance = any(c.name == "cf_clearance" for c in cookies)

                content = (await tab.get_content()).lower()
                ready_state = await tab.evaluate("document.readyState")

                if 'access denied' in content and 'banned your ip address' in content:
                    return {"error": f"[{url}] Access denied: IP address banned."}

                cf_indicators = [
                    "just a moment...", "cf-turnstile", "ddos protection is active", "checking your browser",
                    "verify you are human", "performing security verification", "cf-browser-verification",
                ]
                is_challenged = any(indicator in content for indicator in cf_indicators)

                if has_clearance and not is_challenged and ready_state == "complete":
                    logging.info(f"[{url}] Bypass verified: cf_clearance found & DOM cleared in {elapsed}s.")
                    bypassed = True
                    break

                if is_challenged:
                    logging.info(f"[{url}] Challenge detected. Attempting interaction (Elapsed: {elapsed}s)")
                    with suppress(TimeoutError, asyncio.TimeoutError, ConnectionError, asyncio.CancelledError):
                        cb = await asyncio.wait_for(tab.find("Verify you are", timeout=1.5), timeout=2.0)
                        if cb:
                            logging.info(f"[{url}] Found 'Verify you are human' CheckBox, clicking...")
                            await cb.mouse_click()
                            logging.info(f"[{url}] Click 'Verify you are human' CheckBox")

            except Exception as e:
                logging.debug(f"[{url}] Loop iteration skipped due to internal error: {e!s}")

            await asyncio.sleep(1)

        if not bypassed:
            return {"error": f"Cloudflare challenge was not solved within the timeout limit {elapsed}s."}

        with suppress(Exception):
            if disable_media:
                await tab.send(uc.cdp.fetch.disable())
            await tab.send(uc.cdp.network.disable())

        turnstile_token = await tab.evaluate("""
            (() => {
                const input = document.querySelector('input[name="cf-turnstile-response"]') || 
                              document.querySelector('textarea[name="cf-turnstile-response"]');
                return input ? input.value : "";
            })()
        """)

        final_cookies = await tab.send(uc.cdp.network.get_cookies([url]))
        final_ua = await tab.evaluate("navigator.userAgent")
        captured_headers["User-Agent"] = final_ua
        final_content = await tab.get_content()

        return {
            "status": response_status,
            "url": tab.target.url,
            "response": final_content,
            "userAgent": final_ua,
            "headers": captured_headers,
            "cookies": [c.to_json() for c in final_cookies],
            "turnstile_token": turnstile_token or ""
        }

    except Exception as e:
        logging.error(f"Worker task failed after for {url}: {e!s}")
        return {"error": str(e)}

    finally:
        with suppress(Exception):
            browser.stop()
        await asyncio.sleep(0.5)
        shutil.rmtree(temp_dir, ignore_errors=True)

def worker_entrypoint(url: str, timeout: int, start_timestamp: int, disable_media: bool) -> dict:
    """Synchronous bridge: spins up a fresh event loop specifically for this thread."""
    return asyncio.run(async_worker_task(url, timeout, start_timestamp, disable_media))

app = FastAPI(lifespan=lifespan)

@app.post("/")
@app.post("/v1")
async def solve(req: Request, q: SolveReq):
    start_timestamp = int(time.time() * 1000)
    try:
        _x = req.headers.get("x-forwarded-for")
        origin_ip = _x.split(",")[0].strip() if _x else req.headers.get("x-real-ip", req.client.host)
        logging.info(f"Solving: '{q.url}' from: '{origin_ip}' timeout: {q.timeout}")

        loop = asyncio.get_running_loop()

        solution = await loop.run_in_executor(
            thread_pool,
            worker_entrypoint,
            q.url,
            q.timeout,
            start_timestamp,
            q.disableMedia
        )

        if "error" in solution:
            raise Exception(solution["error"])

        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "message": "Challenge solved!",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__,
                "solution": solution
            }
        )

    except (asyncio.exceptions.CancelledError, KeyboardInterrupt):
        logging.warning("Request Cancelled.")
        return JSONResponse(status_code=499, content={"status": "error", "message": "Client closed request"})

    except Exception as e:
        logging.error(f"Failed on {q.url}: {e!s}")
        return JSONResponse(
            status_code=403 if 'ccess denied' in str(e) else 500,
            content={
                "status": "error",
                "message": f"Error: {e!s}",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__
            }
        )

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "locale": LOCALE,
        "sandbox": SANDBOX,
        "version": __version__,
        "headless": HEADLESS,
        "max_workers": MAX_WORKERS,
        "disable_media": DISABLE_MEDIA,
        "timestamp": time.strftime("%Y %b %d %I:%M:%S %p"),
    }

