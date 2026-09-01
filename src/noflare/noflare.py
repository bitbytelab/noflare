#!/usr/bin/env python3

import os
import time
import asyncio
import logging
import tempfile
import shutil
import socket
import random
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import nodriver as uc
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse

__version__ = "1.0.6"

# --- Thread Pool Sizing ---
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
HEADLESS = str(os.getenv("HEADLESS", "true")).lower() == "true"
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "en-US")
PROXY_SERVER = os.getenv("PROXY_SERVER", None)

_fmt = "%(asctime)s %(threadName)s %(levelname)-8s - %(message)s"
logging.basicConfig(level=logging.INFO, format=_fmt, datefmt="%X")
for noisy_logger in ("nodriver", "fastapi"):logging.getLogger(noisy_logger).setLevel(logging.WARNING)


class SolveRequest(BaseModel):
    url: str
    timeout: int = 55

# Global Thread Pool
thread_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global thread_pool
    # Initialize the Thread Pool
    thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ChromeWorker")
    logging.info(f"Initialized ThreadPoolExecutor with {MAX_WORKERS} concurrent workers.")
    
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
    config.lang = BROWSER_LOCALE
    config.headless = HEADLESS
    config.user_data_dir = temp_profile_dir
    config.port = debug_port
    
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--no-first-run")
    config.add_argument("--no-service-autorun")
    config.add_argument("--password-store=basic")
    
    if PROXY_SERVER:
        config.add_argument(f"--proxy-server={PROXY_SERVER}")

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
        # for _ in range(timeout):
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
                except TimeoutError:pass
                try:
                    logging.debug(f"[{url}] looking for verify you are")
                    if (await tab.find_all("verify you are", timeout=3.0)):
                        logging.debug(f"[{url}] FOUND verify you are")
                        await (await tab.find("verify you are", timeout=3.0)).mouse_click()
                except TimeoutError:pass

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
                        logging.debug(f"[{url}] looking for verify you are human checkbox")
                        try:
                            cb = await tab.find("Verify you are", timeout=5.0)
                            if cb: 
                                logging.info(f"[{url}] Found and attempting checkbox click")
                                await (await tab.find("Verify you are", timeout=5.0)).mouse_click()
                                # await tab.verify_cf()
                        except TimeoutError:pass
                        else:logging.info(f"[{url}] CLICKED verify you are human checkbox")

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

    except Exception as e:
        return {"error": str(e)}

    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass
        
        await asyncio.sleep(1)
        shutil.rmtree(temp_dir, ignore_errors=True)

def worker_entrypoint(url: str, timeout: int) -> dict:
    """Synchronous bridge: spins up a fresh event loop specifically for this thread."""
    return asyncio.run(async_worker_task(url, timeout))


@app.post("/v1")
async def solve(req: SolveRequest):
    start_timestamp = int(time.time() * 1000)

    try:
        logging.info(f"Received request for '{req.url}'' timeout={req.timeout} Dispatching to thread pool...")
        
        loop = asyncio.get_running_loop()
        # run_in_executor uses the ThreadPoolExecutor gracefully
        solution = await loop.run_in_executor(thread_pool, worker_entrypoint, req.url, req.timeout)
        
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

    except Exception as e:
        logging.error(f"Failed on {req.url}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error: {str(e)}",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__
            }
        )
