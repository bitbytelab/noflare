#!/usr/bin/env python3

import os
import time
import asyncio
import logging
from contextlib import asynccontextmanager

import nodriver as uc
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from fastapi.responses import JSONResponse

# Global Default Config
MAX_TABS = 10
HEADLESS = str(os.getenv("HEADLESS", "true")).lower() == "true"
PROXY_SERVER = os.getenv("PROXY_SERVER", None)
PROXY_USERNAME = os.getenv("PROXY_USERNAME", None)
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "en-US")

__version__ = "1.0.4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class SolveRequest(BaseModel):
    url: str
    timeout: int = 45  # Extended default to allow CF to fully process

browser: uc.Browser = None
tab_sem: asyncio.Semaphore = None

def build_browser_config() -> uc.Config:
    config = uc.Config()
    config.lang = BROWSER_LOCALE
    config.headless = HEADLESS
    
    # Run fully ephemeral (in RAM). No user_data_dir prevents state corruption & disk I/O bottlenecks.
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-dev-shm-usage")
    
    # Performance tweaks for concurrency
    config.add_argument("--disable-background-timer-throttling")
    config.add_argument("--disable-backgrounding-occluded-windows")
    config.add_argument("--disable-renderer-backgrounding")

    if PROXY_SERVER:
        config.add_argument(f"--proxy-server={PROXY_SERVER}")
        if PROXY_USERNAME:
            logging.warning("PROXY_USERNAME provided, but Chromium CLI does not support proxy auth. Use IP-whitelisted proxies.")

    return config

@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser, tab_sem
    max_tabs = int(os.getenv("MAX_TABS", MAX_TABS))
    tab_sem = asyncio.Semaphore(max_tabs)

    try:
        logging.info("Starting ephemeral nodriver browser process...")
        browser = await uc.start(config=build_browser_config())
        yield
    except Exception as e:
        logging.critical(f"Failed to start browser: {e}")
        raise
    finally:
        logging.info("Shutting down browser process...")
        if browser:
            browser.stop()

app = FastAPI(lifespan=lifespan)

async def wait_for_bypass(tab: uc.Tab, target_url: str, timeout: int) -> dict:
    """Core logic to navigate and poll for successful bypass concurrently."""
    
    # MAGIC TRICK: Do NOT use await tab.get(url). It blocks the event loop waiting for a DOM 
    # load event that Cloudflare intentionally stalls. Instead, we use raw CDP navigation.
    await tab.send(uc.cdp.page.navigate(url=target_url))

    for _ in range(timeout):
        try:
            current_url = await tab.evaluate("window.location.href")
            if "about:blank" in current_url:
                await asyncio.sleep(1)
                continue

            # 1. Definite proof of bypass
            cookies = await tab.send(uc.cdp.network.get_cookies())
            if any(c.name == "cf_clearance" for c in cookies):
                logging.info(f"[{target_url}] Bypass verified: cf_clearance cookie found.")
                await asyncio.sleep(1.5)  # Allow final destination DOM to settle
                break

            # 2. DOM state fallback
            content = await tab.get_content()
            if not content or len(content) < 50:
                await asyncio.sleep(1)
                continue

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
                logging.info(f"[{target_url}] Bypass verified: No challenge text and readyState complete.")
                break

        except (Exception,) as _e:
            # Expected during redirects/reloads caused by CF. We just keep looping.
            pass

        await asyncio.sleep(1)

    # Gather final output
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

@app.post("/v1")
async def solve(req: SolveRequest):
    start_timestamp = int(time.time() * 1000)

    if not browser:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Browser instance is offline",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__
            }
        )

    # Concurrency throttled purely by the Semaphore to protect RAM
    async with tab_sem:
        tab = None
        try:
            logging.info(f"Dispatching target: {req.url}")
            
            # Open a blank tab instantly. Doesn't block.
            tab = await browser.get("about:blank", new_tab=True)

            solution = await asyncio.wait_for(
                wait_for_bypass(tab, req.url, req.timeout),
                timeout=req.timeout + 5 # Add a 5s padding to the wait_for wrapper
            )

            return {
                "status": 200,
                "message": "Challenge solved!",
                "startTimestamp": start_timestamp,
                "endTimestamp": int(time.time() * 1000),
                "version": __version__,
                "solution": solution
            }

        except asyncio.TimeoutError:
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
        except (Exception,) as _e:
            logging.error(f"Fatal error on {req.url}: {_e}", exc_info=True)
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
        finally:
            if tab:
                try:
                    await tab.close()
                except (Exception,) as _e:
                    logging.error(f"Failed to cleanly close tab: {_e}")
