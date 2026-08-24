#!/usr/bin/env python3

import os
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import nodriver as uc
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

# Global Default Config
MAX_TABS = 10
HEADLESS = False
DATA_DIR = Path.home() / '.noflare/data'
PROXY_SERVER = None
PROXY_USERNAME = None
PROXY_PASSWORD = None
BROWSER_LOCALE = 'en-US'

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class SolveRequest(BaseModel):
    url: str
    timeout: int = 45  # Extended default to allow CF to fully process

browser: uc.Browser = None
tab_sem: asyncio.Semaphore = None

def build_browser_config() -> uc.Config:
    config = uc.Config()
    config.lang = f"{os.getenv('BROWSER_LOCALE', BROWSER_LOCALE)}"
    config.user_data_dir = Path(os.getenv("DATA_DIR", DATA_DIR))
    config.headless = str(os.getenv("HEADLESS", HEADLESS)).lower() == "true"

    config.add_argument("--disable-gpu")
    config.add_argument("--disable-dev-shm-usage")

    proxy_server = os.getenv("PROXY_SERVER", PROXY_SERVER)

    if proxy_server:
        config.add_argument(f"--proxy-server={proxy_server}")
        if os.getenv("PROXY_USERNAME", PROXY_USERNAME):
            logging.warning(
                "PROXY_USERNAME provided, but Chromium CLI does not support proxy auth. Use IP-whitelisted proxies."
            )

    return config

@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser, tab_sem
    max_tabs = int(os.getenv("MAX_TABS", MAX_TABS))
    tab_sem = asyncio.Semaphore(max_tabs)

    try:
        logging.info("Starting nodriver browser process...")
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

async def wait_for_bypass(tab: uc.Tab, target_url: str, timeout: int = 45) -> dict:
    """Core logic to navigate and poll for successful bypass."""
    await tab.bring_to_front()

    for _ in range(timeout):
        try:
            # Prevent race condition: ensure we are not checking the DOM of about:blank
            current_url = await tab.evaluate("window.location.href")
            if "about:blank" in current_url:
                await asyncio.sleep(1)
                continue

            # Definite proof of bypass
            cookies = await tab.send(uc.cdp.network.get_cookies())
            if any(c.name == "cf_clearance" for c in cookies):
                logging.info(f"[{target_url}] Bypass verified: cf_clearance cookie found.")
                await asyncio.sleep(2)  # Allow final DOM to settle
                break

            # DOM state fallback
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
            logging.debug(f"[{target_url}] Polling interrupted (expected during redirects): {_e}")

        await asyncio.sleep(1)

    content = await tab.get_content()
    cookies = await tab.send(uc.cdp.network.get_cookies())
    user_agent = await tab.evaluate("navigator.userAgent")

    return {
        "status": "ok",
        "url": tab.target.url,
        "solution": {
            "userAgent": user_agent,
            "response": content,
            "cookies": [c.to_json() for c in cookies]
        }
    }

@app.post("/v1")
async def solve(req: SolveRequest):
    if not browser:
        raise HTTPException(status_code=500, detail="Browser instance is offline")

    async with tab_sem:
        tab = None
        try:
            logging.info(f"Dispatching target: {req.url}")
            tab = await browser.get(req.url, new_tab=True)

            result = await asyncio.wait_for(
                wait_for_bypass(tab, req.url, req.timeout),
                timeout=req.timeout
            )
            return result

        except TimeoutError:
            logging.error(f"Timeout solving {req.url}")
            raise HTTPException(status_code=504, detail="Timeout waiting for bypass") from TimeoutError
        except (Exception,) as _e:
            logging.error(f"Fatal error on {req.url}: {_e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(_e)) from _e
        finally:
            if tab:
                try:
                    await tab.close()
                except (Exception,) as _e:
                    logging.error(f"Failed to cleanly close tab: {_e}")
