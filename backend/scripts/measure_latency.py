"""Where the seconds actually go.

Every latency constant in this codebase was tuned against a number nobody
had separated into its parts. `policies.py` records a single-row indexed
SELECT at 2365ms; FINDINGS #9 records three concurrent reads timing 80%
slower than three sequential ones. Both were read as facts about the
database. They were facts about the distance to it - Railway in Amsterdam,
Supabase in Seoul - and about a pool that opened a fresh connection for
every concurrent caller.

This separates the two costs that matter and were never told apart:

    HANDSHAKE  - what it costs to OPEN a connection
    QUERY      - what it costs to use one that is already open

If handshake dominates, the fix is configuration: region, pooler, pool
warmth. If query dominates, the fix is in the SQL. Guessing wrong means
rewriting eighteen repositories to save nothing.

Run it before a change and after. Numbers without a baseline are anecdotes.

    python scripts/measure_latency.py
"""
import asyncio
import statistics
import sys
import time

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from app.core.config import settings  # noqa: E402
from app.db.database import get_db, prewarm_pool, run_db_async  # noqa: E402

SAMPLES = 5


def _fmt(label: str, samples: list) -> str:
    if not samples:
        return f"  {label:<34} (no samples)"
    ms = [s * 1000 for s in samples]
    return (
        f"  {label:<34} median {statistics.median(ms):7.0f} ms"
        f"   min {min(ms):7.0f}   max {max(ms):7.0f}"
    )


def _one_query() -> None:
    with get_db() as conn:
        conn.cursor().execute("SELECT 1")


def _fresh_connection() -> None:
    """Open a connection outside the pool, so this times the handshake."""
    import psycopg2

    conn = psycopg2.connect(settings.DATABASE_URL, connect_timeout=10)
    try:
        conn.cursor().execute("SELECT 1")
    finally:
        conn.close()


async def main() -> None:
    host = (settings.DATABASE_URL or "").split("@")[-1].split("/")[0]
    print()
    print("Kinato latency baseline")
    print(f"  database host : {host or '(sqlite)'}")
    print(f"  model         : {settings.LLM_MODEL}")
    print()

    # Warm first, so the pooled measurements are not measuring the pool
    # filling itself up.
    await prewarm_pool()

    print("DATABASE")
    pooled = []
    for _ in range(SAMPLES):
        t = time.perf_counter()
        await run_db_async(_one_query)
        pooled.append(time.perf_counter() - t)
    print(_fmt("SELECT 1 on a warm connection", pooled))

    fresh = []
    for _ in range(SAMPLES):
        t = time.perf_counter()
        try:
            await asyncio.to_thread(_fresh_connection)
        except Exception as e:
            print(f"  (fresh connection failed: {e})")
            break
        fresh.append(time.perf_counter() - t)
    print(_fmt("SELECT 1 on a NEW connection", fresh))

    if pooled and fresh:
        warm = statistics.median(pooled)
        handshake = statistics.median(fresh) - warm
        print()
        print(f"  => opening a connection costs ~{handshake * 1000:.0f} ms on top")
        print(f"     using an open one costs    ~{warm * 1000:.0f} ms")
        print()
        # `SELECT 1` does no work, so the warm number is not query cost - it
        # is the round trip itself. Both figures are geography, and they are
        # fixed by different things, so report both rather than picking a
        # winner: pool warmth removes the handshake, fewer statements
        # removes the round trips, and moving the backend closer removes
        # some of each.
        print("     Neither of those is query execution - SELECT 1 does no work.")
        print(f"     Both are distance. A turn making 12 statements pays")
        print(f"     ~{warm * 12 * 1000:.0f} ms of it even with every connection already open.")
        if handshake > warm:
            print("     Pool warmth is worth more than statement count here.")
        else:
            print("     Statement count is worth more than pool warmth here.")

    # Concurrency, which is the shape a live call actually makes: several
    # queries at once, not one after another. FINDINGS #9 measured this
    # going the wrong way.
    print()
    t = time.perf_counter()
    await asyncio.gather(*(run_db_async(_one_query) for _ in range(4)))
    concurrent = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(4):
        await run_db_async(_one_query)
    sequential = time.perf_counter() - t
    print(f"  4 queries concurrent : {concurrent * 1000:7.0f} ms")
    print(f"  4 queries sequential : {sequential * 1000:7.0f} ms")
    if concurrent > sequential:
        print("     Concurrency is SLOWER - the pool is still handing out cold")
        print("     connections under load. Raise POOL_MIN_CONNECTIONS.")

    # The other two things in a turn, for proportion. A turn that spends 1s
    # on the model and 11s on the database is not a model problem, and this
    # is the line that says so.
    print()
    print("EXTERNAL")
    if settings.OPENROUTER_API_KEY:
        from app.agents import runtime as agent_runtime

        client = agent_runtime._get_llm_client()
        llm = []
        for _ in range(3):
            t = time.perf_counter()
            try:
                await client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[{"role": "user", "content": "Say OK."}],
                    max_tokens=5,
                    timeout=20.0,
                )
            except Exception as e:
                print(f"  (llm call failed: {e.__class__.__name__}: {e})")
                break
            llm.append(time.perf_counter() - t)
        print(_fmt(f"one {settings.LLM_MODEL} round trip", llm))
    else:
        print("  (no OPENROUTER_API_KEY - skipping the model)")

    if settings.ELEVENLABS_API_KEY:
        from app.services.tts import generate_elevenlabs_audio

        tts = []
        for i in range(3):
            t = time.perf_counter()
            try:
                # Distinct text each time: the module caches by text, and
                # measuring the cache would tell us nothing.
                await generate_elevenlabs_audio(f"This is measurement number {i} of the speech path.")
            except Exception as e:
                print(f"  (tts call failed: {e.__class__.__name__}: {e})")
                break
            tts.append(time.perf_counter() - t)
        print(_fmt("one ElevenLabs render", tts))
    else:
        print("  (no ELEVENLABS_API_KEY - skipping speech)")

    print()
    print("A turn has to fit all of this inside Twilio's 15 seconds.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
