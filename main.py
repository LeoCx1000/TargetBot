from __future__ import annotations
import discord
import asyncio
import logging
import asyncpg
import os
import aiohttp
from dotenv import load_dotenv
from discord.ext import commands
from asyncpg.transaction import Transaction
from typing import Type, Tuple, Generic, Optional, TypeVar
from cogs.utils.custom_commands import HandlerCommand

_log = logging.getLogger("TargetBot")

TBT = TypeVar('TBT', bound='TargetBot')


class TBDefaultHelpCommand(commands.DefaultHelpCommand):
    def get_ending_note(self) -> str:
        return f'Type {self.context.clean_prefix}{self.invoked_with} <command> for more info on a command.\n'


class DbContextManager(Generic[TBT]):
    """A simple context manager used to manage database connections.

    .. note::

        Please note this was created instead of using `contextlib.asynccontextmanager` because
        I plan to add additional functionality to this class in the future.

    Attributes
    ----------
    bot: :class:`TargetBot`
        The bot instance.
    timeout: :class:`float`
        The timeout for acquiring a connection.
    """

    __slots__: Tuple[str, ...] = ("bot", "timeout", "_pool", "_conn", "_tr")

    def __init__(self, bot: TBT, *, timeout: float = 10.0) -> None:
        self.bot: TBT = bot
        self.timeout: float = timeout
        self._pool: asyncpg.Pool[asyncpg.Record] = bot.pool
        self._conn: Optional[asyncpg.Connection] = None
        self._tr: Optional[Transaction] = None

    async def acquire(self) -> asyncpg.Connection:
        return await self.__aenter__()

    async def release(self) -> None:
        return await self.__aexit__(None, None, None)

    async def __aenter__(self) -> asyncpg.Connection:
        self._conn = conn = await self._pool.acquire(timeout=self.timeout)  # type: ignore
        self._tr = conn.transaction()
        await self._tr.start()
        return conn  # type: ignore

    async def __aexit__(self, exc_type, exc, tb):
        if exc and self._tr:
            try:
                await self._tr.rollback()
            except asyncpg.InterfaceError as e:
                if 'already rolled back' in str(e):
                    return
                raise e

        elif not exc and self._tr:
            try:
                await self._tr.commit()
            except asyncpg.InterfaceError as e:
                if 'already rolled back' in str(e):
                    return
                raise e

        if self._conn is not None:
            await self._pool.release(self._conn)  # type: ignore


class DbTempContextManager(Generic[TBT]):
    """A class to handle a short term pool connection.

    .. code-block:: python3

        async with DbTempContextManager(bot, 'postgresql://user:password@localhost/database') as pool:
            async with pool.acquire() as conn:
                await conn.execute('SELECT * FROM table')

    Attributes
    ----------
    bot: Type[:class:`TargetBot`]
        A class reference to TargetBot.
    uri: :class:`str`
        The URI to connect to the database with.
    """

    __slots__: Tuple[str, ...] = ("bot", "uri", "_pool")

    def __init__(self, bot: Type[TBT], uri: str) -> None:
        self.bot: Type[TBT] = bot
        self.uri: str = uri
        self._pool: Optional[asyncpg.Pool] = None

    async def __aenter__(self) -> asyncpg.Pool:
        self._pool = pool = await self.bot.setup_pool(uri=self.uri)
        return pool

    async def __aexit__(self, *args) -> None:
        if self._pool:
            await self._pool.close()


class TargetBot(commands.Bot):
    INITIAL_EXTENSIONS = (
        "jishaku",
        "cogs.info",
        "cogs.handler",
        "cogs.autohelp",
    )

    CC_QUERY = """
        SELECT 
        cc.command_string,
        cc.command_content,
        cc.embed,
        (SELECT ARRAY(
            SELECT command_string
            FROM custom_commands
            WHERE aliases_to = cc.command_string
        )) AS aliases,
        cc.description
        FROM custom_commands AS cc
        WHERE aliases_to ISNULL
    """

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record], session: aiohttp.ClientSession):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.all(),
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=TBDefaultHelpCommand(no_category="Information Commands"),
            description="TargetBot: The support bot for the Stylized Resource Pack server!",
        )
        self.pool: asyncpg.Pool[asyncpg.Record] = pool
        self.session: aiohttp.ClientSession = session

    async def on_ready(self):
        _log.info("Logged in as %s", self.user)

    async def setup_hook(self):
        """|coro| Called when the bot logs in, prepares cache and extensions."""
        for ext in self.INITIAL_EXTENSIONS:
            try:
                await self.load_extension(ext)
                _log.info("Loaded extension %s", ext)
            except Exception as e:
                _log.error("Could not load extension %s", ext, exc_info=e)
        await self.populate_custom_commands()

    async def populate_custom_commands(self):
        """|coro| Pulls commands from the database and populates the handler."""
        data = await self.pool.fetch(self.CC_QUERY)
        for record in data:
            self.add_command(record)

    def add_command(self, record: asyncpg.Record | commands.Command) -> None:
        """ "It takes a record from the database and creates a HandlerCommand object from it

        Parameters
        ----------
        record: :class:`asyncpg.Record` | :class:`commands.Command`
            A database record with the necessary data, which is:
                name: str, content: str?, embed: json?, aliases: list[str]?

        Returns
        -------
            A HandlerCommand object

        """
        if isinstance(record, commands.Command):
            return super().add_command(record)
        name, content, embed, aliases, description = record
        command = HandlerCommand(name=name, aliases=aliases, content=content, embed=embed, description=description)
        return super().add_command(command)

    @classmethod
    async def setup_pool(cls, *, uri: str, **kwargs) -> asyncpg.Pool:
        """:meth: `asyncpg.create_pool` with some extra functionality.

        Parameters
        ----------
        uri: :class:`str`
            The Postgres connection URI.
        **kwargs:
            Extra keyword arguments to pass to :meth:`asyncpg.create_pool`.
        """  # copy_doc for create_pool maybe?

        def _encode_jsonb(value):
            return discord.utils._to_json(value)

        def _decode_jsonb(value):
            return discord.utils._from_json(value)

        old_init = kwargs.pop("init", None)

        async def init(con):
            await con.set_type_codec(
                "jsonb",
                schema="pg_catalog",
                encoder=_encode_jsonb,
                decoder=_decode_jsonb,
                format="text",
            )
            if old_init is not None:
                await old_init(con)

        pool = await asyncpg.create_pool(uri, init=init, **kwargs)
        assert pool is not None, "Pool is None"
        return pool

    @classmethod
    def temporary_pool(cls: Type[TBT], *, uri: str) -> DbTempContextManager[TBT]:
        """:class:`DbTempContextManager` A context manager that creates a
        temporary connection pool.

        Parameters
        ----------
        uri: :class:`str`
            The URI to connect to the database with.
        """
        return DbTempContextManager(cls, uri)

    def safe_connection(self, *, timeout: float = 10.0) -> DbContextManager:
        """A context manager that will acquire a connection from the bot's pool.

        This will neatly manage the connection and release it back to the pool when the context is exited.

        .. code-block:: python3

            async with bot.safe_connection(timeout=10) as conn:
                await conn.execute('SELECT * FROM table')
        """
        return DbContextManager(self, timeout=timeout)


async def startup():
    load_dotenv()
    async with (
        TargetBot.temporary_pool(uri=os.environ["PG_DSN"]) as pool,
        aiohttp.ClientSession() as session,
        TargetBot(pool, session) as bot,
    ):
        discord.utils.setup_logging()
        await bot.start(token=os.environ["TOKEN"])


if __name__ == "__main__":
    asyncio.run(startup())
