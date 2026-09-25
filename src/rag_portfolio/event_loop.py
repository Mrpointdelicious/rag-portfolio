import asyncio


def loop_factory() -> asyncio.AbstractEventLoop:
    # psycopg async connections require a selector loop on Windows.
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop
