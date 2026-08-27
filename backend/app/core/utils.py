import asyncio
import concurrent.futures

def sync_await(coro):
    """
    Safely execute an async coroutine from a synchronous context that might
    already be running inside an event loop (e.g. FastAPI / LangGraph nodes).
    It spawns a temporary thread with its own event loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(coro))
        return future.result()
