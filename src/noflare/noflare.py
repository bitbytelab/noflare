#!/usr/bin/env python3

import os
import time
import asyncio
import logging
import tempfile
import shutil
from contextlib import asynccontextmanager

import nodriver as uc
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse

# Global Config
MAX_CONCURRENT_INSTANCES = int(os.getenv("MAX_CONCURRENT_INSTANCES", "5"))
HEADLESS = str(os.getenv("HEADLESS", "true")).lower() == "true"
PROXY_SERVER = os.getenv("PROXY_SERVER", None)
PROXY_USERNAME = os.getenv("PROXY_USERNAME", None)
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "en-US")

__version__ = "1.0.5"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class SolveRequest(BaseModel):
    url: str
    timeout: int = 45

pool_semaphore: asyncio.Semaphore = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool_semaphore
    pool_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INSTANCES)
    logging.info(f"Initialized browser pool (Max concurrent instances: {MAX_CONCURRENT_INSTANCES})")
    yield

app = FastAPI(lifespan=lifespan)

def create_browser_config(temp_profile_dir: str) -> uc.Config:
    config = uc.Config()
    config.lang = BROWSER_LOCALE
    config.headless = HEADLESS
    
    # OVERRIDE: Force nodriver to use our temp dir so it doesn't use its leaky atexit cleanup
    config.user_data_dir = temp_profile_dir
    
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--no-first-run")
    config.add_argument("--no-service-autorun")
    config.add_argument("--password-store=basic")
    
    if PROXY_SERVER:
        config.add_argument(f"--proxy-server={PROXY_SERVER}")
        if PROXY_USERNAME:
            logging.warning("PROXY_USERNAME provided, but Chromium CLI does not support proxy auth.")

    return config

async def solve_in_dedicated_instance(url: str, timeout: int) -> dict:
    """Spawns an isolated Chrome process, solves the challenge, and cleans up heavily."""
    browser = None
    
    # 1. Create a dedicated temporary directory we control
    temp_dir = tempfile.mkdtemp(prefix="noflare_")
    
    try:
        browser = await uc.start(config=create_browser_config(temp_dir))
        tab = await browser.get("about:blank")
        
        # Non-blocking raw CDP navigation
        await tab.send(uc.cdp.page.navigate(url=url))

        for _ in range(timeout):
            try:
                current_url = await tab.evaluate("window.location.href")
                if "about:blank" in current_url:
                    await asyncio.sleep(1)
                    continue

                cookies = await tab.send(uc.cdp.network.get_cookies())
                if any(c.name == "cf_clearance" for c in cookies):
                    logging.info(f"[{url}] Bypass verified: cf_clearance cookie found.")
                    await asyncio.sleep(1.5)
                    break

                content = await tab.get_content()
                if content and len(content) >= 50:
                    cf_indicators = [
                        "cf-browser-verification",
                        "Just a moment...",
                        "cf-turnstile",
                        "DDoS protection is active",
                        "Checking your browser",
                        "Verify you are human"
                    ]

                    is_challenged = any(indicator in content for indicator in cf_indicators)
                    ready_state = await tab.evaluate("document.readyState")

                    if not is_challenged and ready_state == "complete":
                        logging.info(f"[{url}] Bypass verified: DOM challenge cleared.")
                        break

            except Exception:
                pass

            await asyncio.sleep(1)

        content = await tab.get_content()
        cookies = await tab.send(uc.cdp.network.get_cookies())
        user_agent = await tab.evaluate("navigator.userAgent")

        return {
            "url": tab.target.url,
            "status": 200,
            "headers": {},
            "response": content,
            "cookies": [c.to_json() for c in cookies],
            "userAgent": user_agent
        }

    finally:
        # 2. Strict Cleanup Sequence
        if browser:
            try:
                browser.stop()
            except Exception as e:
                logging.error(f"Error terminating browser instance: {e}")
        
        # Give the OS a moment to kill the Chrome process and release file locks
        await asyncio.sleep(1)
        
        # 3. Nuke the temporary profile directory from the disk manually
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logging.debug(f"Cleaned up temp profile: {temp_dir}")
        except Exception as e:
            logging.error(f"Failed to delete temp dir {temp_dir}: {e}")

@app.post("/v1")
async def solve(req: SolveRequest):
    start_timestamp = int(time.time() * 1000)

    # Throttles maximum simultaneous Chrome instances to protect RAM/CPU
    async with pool_semaphore:
        try:
            logging.info(f"Spawning dedicated browser process for: {req.url}")
            
            solution = await asyncio.wait_for(
                solve_in_dedicated_instance(req.url, req.timeout),
                timeout=req.timeout + 12  # Padding for process startup & termination
            )

            return {
                "status": "ok",
                "message": "Challenge solved!",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__,
                "solution": solution
            }

        except (asyncio.TimeoutError, TimeoutError):
            logging.error(f"Timeout solving {req.url}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Error: Timeout waiting for bypass",
                    "startTimestamp": start_timestamp,
                    "endTimestamp": int(time.time() * 1000),
                    "version": __version__
                }
            )
        except Exception as e:
            logging.error(f"Fatal error on {req.url}: {e}", exc_info=True)
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