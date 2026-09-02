#!/usr/bin/env python3

import os
import time
import random
import shutil
import socket
import asyncio
import logging
import tempfile
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
	__version__ = "1.2.4"

thread_pool = None
# --- Thread Pool Sizing ---
PROXY_SERVER = os.getenv("PROXY_SERVER", None)
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "en-US")
HEADLESS = str(os.getenv("HEADLESS", "false")).lower() == "true"
NO_SANDBOX = str(os.getenv("NO_SANDBOX", "false")).lower() == "true"
SAVE_SS = str(os.getenv("SAVE_SS", "false")).lower() == "true"

_fmt = "%(levelname)-9s %(threadName)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=_fmt)
for noisy_logger in ("nodriver", "fastapi"):logging.getLogger(noisy_logger).setLevel(logging.WARNING)

class SolveReq(BaseModel):
    url: str
    timeout: int = 95

@asynccontextmanager
async def lifespan(app: FastAPI):
    global thread_pool
    thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Worker")
    logging.info(f"NoFlare v{__version__} Initialized ThreadPoolExecutor with {MAX_WORKERS} workers.")

    yield

    logging.info("Shutting down thread pool...")
    thread_pool.shutdown(wait=False, cancel_futures=True)

app = FastAPI(lifespan=lifespan)

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def create_browser_config(temp_profile_dir: str, debug_port: int) -> uc.Config:
    config = uc.Config()
    config.port = debug_port
    config.lang = BROWSER_LOCALE
    config.headless = HEADLESS
    config.user_data_dir = temp_profile_dir

    config.add_argument("--disable-gpu")
    config.add_argument("--no-first-run")
    config.add_argument("--no-service-autorun")
    config.add_argument("--password-store=basic")
    config.add_argument("--disable-dev-shm-usage")

    if NO_SANDBOX:
        config.sandbox = False
        config.add_argument("--disable-setuid-sandbox")

    return config

async def async_worker_task(url: str, timeout: int) -> dict:
    """The heavy browser logic isolated entirely inside a thread's own event loop."""
    browser = None
    temp_dir = tempfile.mkdtemp(prefix="noflare_")

    try:
        # Prevent simultaneous CDP port grabs and botnet-like CPU spikes
        await asyncio.sleep(random.uniform(0.5, 1.5))

        debug_port = get_free_port()
        browser = await uc.start(config=create_browser_config(temp_dir, debug_port))
        tab = await browser.get("about:blank")

        raw_ua = await tab.evaluate("navigator.userAgent")
        clean_ua = raw_ua.replace("HeadlessChrome", "Chrome")
        await tab.send(uc.cdp.emulation.set_user_agent_override(user_agent=clean_ua))
        await tab.send(uc.cdp.emulation.set_focus_emulation_enabled(enabled=True))

        await tab.send(uc.cdp.page.navigate(url=url))
        bypassed = False

        _st_ts = time.time()
        while (time.time() - _st_ts) < timeout:
            if SAVE_SS and (time.time() - _st_ts) < timeout-10:
                await page.save_screenshot(f"/tmp/ss/ss_{time.strftime('%y_%m_%d_%H_%M_%S')}.png")
            try:
                current_url = await tab.evaluate("window.location.href")
                if "about:blank" in current_url:
                    await asyncio.sleep(1)
                    continue

                try:
                    logging.debug(f"[{url}] looking for accept cookies")
                    if (await tab.find_all("accept all co", timeout=3.0)):
                        logging.debug(f"[{url}] FOUND accept cookies btn")
                        await (await tab.find("accept all co", timeout=3.0)).click()
                except TimeoutError:
                    pass
                try:
                    logging.debug(f"[{url}] looking for verify you are human checkbox")
                    if (await tab.find_all("verify you are", timeout=3.0)):
                        logging.info(f"[{url}] FOUND verify you are human checkbox")
                        await (await tab.find("verify you are", timeout=3.0)).mouse_click()
                except TimeoutError:
                    pass

                logging.debug(f"[{url}] checking cookies")
                cookies = await tab.send(uc.cdp.network.get_cookies())
                if any(c.name == "cf_clearance" for c in cookies):
                    logging.info(f"[{url}] Bypass verified: cf_clearance cookie found.")
                    bypassed = True
                    await asyncio.sleep(1.5)
                    break

                logging.debug(f"[{url}] getting tab content")
                content = await tab.get_content()
                if content and len(content) >= 50:
                    cf_indicators = [
                        "Just a moment...", "DDoS protection is active", "Checking your browser", "cf-turnstile",
                        "Verify you are human", "Performing security verification", "cf-browser-verification",
                    ]

                    is_challenged = any(indicator in content for indicator in cf_indicators)
                    ready_state = await tab.evaluate("document.readyState")

                    if not is_challenged and ready_state == "complete":
                        logging.info(f"[{url}] Bypass verified: DOM challenge cleared.")
                        bypassed = True
                        break
                    else:
                        # <input type="checkbox" aria-label="Verify you are human">
                        logging.info(f"[{url}] looking for verify you are human checkbox")
                        try:
                            cb = await tab.find("Verify you are", timeout=5.0)
                            if cb:
                                logging.info(f"[{url}] Found and attempting 'Verify you are human' checkbox click")
                                await (await tab.find("Verify you are", timeout=5.0)).mouse_click()
                                # await tab.verify_cf()
                        except TimeoutError:
                            pass
                        else:
                            logging.info(f"[{url}] CLICKED 'Verify you are human checkbox")

            except Exception as _e:
                logging.info(f"[{url}] Skipped Error: {_e}")
                pass

            await asyncio.sleep(1)

        if not bypassed:
            return {"error": "Cloudflare challenge was not solved within the timeout limit."}

        content = await tab.get_content()
        cookies = await tab.send(uc.cdp.network.get_cookies())

        return {
            "url": tab.target.url,
            "status": 200,
            "headers": {},
            "response": content,
            "cookies": [c.to_json() for c in cookies],
            "userAgent": clean_ua
        }

    except Exception as _e:
        return {"error": str(_e)}

    finally:
        if browser:
            with suppress(Exception):
                browser.stop()
            await asyncio.sleep(1)
        shutil.rmtree(temp_dir, ignore_errors=True)

def worker_entrypoint(url: str, timeout: int) -> dict:
    """Synchronous bridge: spins up a fresh event loop specifically for this thread."""
    return asyncio.run(async_worker_task(url, timeout))


@app.post("/")
@app.post("/v1")
async def solve(
    req: Request,
    q: SolveReq,
):
    start_timestamp = int(time.time() * 1000)


    try:
        x_forward = req.headers.get("x-forwarded-for")
        origin_ip = x_forward.split(",").strip() if x_forward else req.headers.get("x-real-ip", req.client.host)
        
        logging.info(f"Solving X '{q.url}' timeout={q.timeout} from {origin_ip}")

        loop = asyncio.get_running_loop()
        solution = await loop.run_in_executor(thread_pool, worker_entrypoint, q.url, q.timeout)

        if "error" in solution:
            raise Exception(solution["error"])

        return {
            "status": "ok",
            "message": "Challenge solved!",
            "startTimestamp": start_timestamp,
            "endTimestamp": int(time.time() * 1000),
            "version": __version__,
            "solution": solution
        }

    except Exception as _e:
        logging.error(f"Failed on {q.url}: {_e!s}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error: {_e!s}",
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
        "version": __version__,
        "headless": HEADLESS,
        "max_workers": MAX_WORKERS,
        "timestamp": time.strftime("%Y %b %d %I:%M:%S %p"),
        "browser_locale": BROWSER_LOCALE,
        "proxy_server": PROXY_SERVER
    }
