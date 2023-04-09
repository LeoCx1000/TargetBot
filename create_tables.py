import asyncio
import os

from dotenv import load_dotenv

from main import TargetBot

async def runner():
    load_dotenv()
    with open('schema.sql', 'r') as fp:
        schema = fp.read()

    async with (
        TargetBot.temporary_pool(uri=os.environ['PG_DSN']) as pool,
        pool.acquire() as conn, conn.transaction()
    ):
        await conn.execute(schema)

asyncio.run(runner())