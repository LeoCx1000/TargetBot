from __future__ import annotations

import sys

import asyncio
import datetime
import os
import traceback
from logging import getLogger
from typing import TYPE_CHECKING, Any, Dict, Generator, List, Optional, Tuple, TypedDict

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

if TYPE_CHECKING:
    from main import TargetBot as BotClass


__all__: Tuple[str, ...] = ('ExceptionsManager', )

load_dotenv()

ERROR_WEBHOOK_URL = os.environ['ERROR_WEBHOOK']
log = getLogger('ErrorManager')

class TracebackTypeOptional(TypedDict, total=False):
    author: int
    guild: Optional[int]
    channel: int
    event_name: str
    command: Optional[commands.Command[Any, ..., Any] | app_commands.Command[Any, ..., Any] | app_commands.ContextMenu]


class TracebackType(TracebackTypeOptional):
    time: datetime.datetime
    exception: Exception

class ExceptionsManager:
    """A simple exception handler that sends all exceptions to a error
    Webhook and then logs them to the console.

    This class handles cooldowns with a simple lock, so you dont have to worry about
    rate limiting your webhook and getting banned :).

    .. note::

        If some code is raising MANY errors VERY fast and you're not there to fix it,
        this will take care of things for you.

    Attributes
    ----------
    bot: :class:`BotClass`
        The bot instance.
    cooldown: :class:`datetime.timedelta`
        The cooldown between sending errors. This defaults to 5 seconds.
    errors: Dict[str, Dict[str, Any]]
        A mapping of tracbacks to their error information.
    code_blocker: :class:`str`
        The code blocker used to format Discord codeblocks.
    error_webhook: :class:`discord.Webhook`
        The error webhook used to send errors.
    """

    __slots__: Tuple[str, ...] = ('bot', 'cooldown', '_lock', '_most_recent', 'errors', 'code_blocker', 'error_webhook')

    async def on_error(self, event_name: str, *event_args, **event_kwargs):
        _, error, _ = sys.exc_info()
        if isinstance(error, Exception):
            await self.add_error(error=error, ctx=event_name)

    def __init__(self, bot: BotClass, *, cooldown: datetime.timedelta = datetime.timedelta(seconds=5), hijack_error_event: bool = True) -> None:
        if not ERROR_WEBHOOK_URL:
            raise RuntimeError('No error webhook set in .env!')

        self.bot: BotClass = bot
        self.cooldown: datetime.timedelta = cooldown

        self._lock: asyncio.Lock = asyncio.Lock()
        self._most_recent: Optional[datetime.datetime] = None

        self.errors: Dict[str, List[TracebackType]] = {}
        self.code_blocker: str = '```py\n{}```'
        self.error_webhook: discord.Webhook = discord.Webhook.from_url(
            ERROR_WEBHOOK_URL, session=bot.session, bot_token=bot.http.token
        )
        if hijack_error_event:
            self.bot.on_error = self.on_error

    def _yield_code_chunks(self, iterable: str, *, chunksize: int = 2000) -> Generator[str, None, None]:
        cbs = len(self.code_blocker) - 2  # code blocker size

        for i in range(0, len(iterable), chunksize - cbs):
            yield self.code_blocker.format(iterable[i : i + chunksize - cbs])

    async def release_error(self, traceback: str, packet: TracebackType) -> None:
        """|coro|

        Releases an error to the webhook and logs it to the console. It is not recommended
        to call this yourself, call :meth:`add_error` instead.

        Parameters
        ----------
        traceback: :class:`str`
            The traceback of the error.
        packet: :class:`dict`
            The additional information about the error.
        """
        log.error('Releasing error to log', exc_info=packet['exception'])

        if self.error_webhook.is_partial():
            self.error_webhook = await self.error_webhook.fetch()

        fmt = {
            'time': discord.utils.format_dt(packet['time']),
        }
        if author := packet.get('author'):
            fmt['author'] = f'<@{author}>'

        # This is a bit of a hack,  but I do it here so guild_id
        # can be optional, and I wont get type errors.
        # No chai it gets mad...
        guild_id = packet.get('guild')
        guild = self.bot.get_guild(guild_id or 0)
        if guild:
            fmt['guild'] = f'{guild.name} ({guild.id})'
        else:
            log.warning('Ignoring error packet with unknown guild id %s', guild_id)

        if guild:
            channel_id = packet.get('channel')
            if channel_id and (channel := guild.get_channel(channel_id)):
                fmt['channel'] = f'{channel.name} - {channel.mention} - ({channel.id})'

            # Let's try and upgrade the author
            author_id = packet.get('author')
            if author_id:
                author = guild.get_member(author_id) or self.bot.get_user(author_id)
                if author:
                    fmt['author'] = f'{str(author)} - {author.mention} ({author.id})'

        if not fmt.get('author') and (author_id := packet.get('author')):
            fmt['author'] = f'<Unknown User> - <@{author_id}> ({author_id})'

        if command := packet.get('command'):
            fmt['command'] = command.qualified_name
            display = f'in command "{command.qualified_name}"'
        else:
            display = f'in no command (in {packet.get("event_name") or type(self.bot).__name__})'

        embed = discord.Embed(title=f'An error has occured in {display}', timestamp=packet['time'])
        embed.add_field(
            name='Metadata',
            value='\n'.join([f'**{k.title()}**: {v}' for k, v in fmt.items()]),
        )

        kwargs: Dict[str, Any] = {}
        if self.bot.user:
            kwargs['username'] = self.bot.user.display_name
            kwargs['avatar_url'] = self.bot.user.display_avatar.url

            embed.set_author(name=str(self.bot.user), icon_url=self.bot.user.display_avatar.url)

        webhook = self.error_webhook
        if webhook.is_partial():
            self.error_webhook = webhook = await self.error_webhook.fetch()

        code_chunks = list(self._yield_code_chunks(traceback))

        embed.description = code_chunks.pop(0)
        await webhook.send(embed=embed, **kwargs)

        embeds: List[discord.Embed] = []
        for entry in code_chunks:
            embed = discord.Embed(description=entry)
            if self.bot.user:
                embed.set_author(name=str(self.bot.user), icon_url=self.bot.user.display_avatar.url)

            embeds.append(embed)

            if len(embeds) == 10:
                await webhook.send(embeds=embeds, **kwargs)
                embeds = []

        if embeds:
            await webhook.send(embeds=embeds, **kwargs)

    async def add_error(
        self, *, error: Exception, ctx: Optional[commands.Context[BotClass] | discord.Interaction[BotClass] | str] = None
    ) -> None:
        """|coro|

        Add an error to the error manager. This will handle all cooldowns and internal cache management
        for you. This is the recommended way to add errors.

        Parameters
        ----------
        error: :class:`Exception`
            The error to add.
        ctx: Optional[:class:`commands.Context`]
            The invocation context of the error, if any.
        """
        log.info('Adding error "%s" to log.', str(error))

        def get_time(obj: Optional[commands.Context[BotClass] | discord.Interaction[BotClass] | str]) -> datetime.datetime:
            if isinstance(obj, discord.Interaction):
                return obj.created_at
            elif isinstance(obj, commands.Context):
                if obj.message:
                    return obj.message.created_at
                elif obj.interaction:
                    return obj.interaction.created_at
            return discord.utils.utcnow()

        packet: TracebackType = {'time': get_time(ctx), 'exception': error}
        if isinstance(ctx, str):
            packet['event_name'] = ctx
        elif ctx is not None:
            addons: TracebackTypeOptional = {
                'command': ctx.command,
                'author': getattr(ctx, 'user', ctx.author),  # type: ignore
                'guild': (ctx.guild and ctx.guild.id) or None,
                'channel': ctx.channel.id if ctx.channel else 0,
            }
            packet.update(addons)  # type: ignore

        traceback_string = ''.join(traceback.format_exception(type(error), error, error.__traceback__)).replace(
            os.getcwd(), 'CWD'
        )
        current = self.errors.get(traceback_string)

        if current:
            self.errors[traceback_string].append(packet)
        else:
            self.errors[traceback_string] = [packet]

        async with self._lock:
            # I want all other errors to be released after this one, which is why
            # lock is here. If you have code that calls MANY errors VERY fast,
            # this will ratelimit the webhook. We dont want that lmfao.

            if not self._most_recent:
                self._most_recent = discord.utils.utcnow()
                await self.release_error(traceback_string, packet)
            else:
                time_between = packet['time'] - self._most_recent

                if time_between > self.cooldown:
                    self._most_recent = discord.utils.utcnow()
                    return await self.release_error(traceback_string, packet)
                else:  # We have to wait
                    log.debug('Waiting %s seconds to release error', time_between.total_seconds())
                    await asyncio.sleep(time_between.total_seconds())

                    self._most_recent = discord.utils.utcnow()
                    return await self.release_error(traceback_string, packet)
