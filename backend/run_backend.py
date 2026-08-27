import sys
import logging
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    load_dotenv()
    # Without this, every app-level logger.info() call (agents, voice
    # runtime, orchestrator, etc.) is silently dropped by Python's
    # logging-module default (a WARNING-level "handler of last resort") -
    # only uvicorn's own request-access logs were ever visible. This is
    # what makes real-time debugging (e.g. a live voice call) possible.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)