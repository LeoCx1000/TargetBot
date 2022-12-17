from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from logging import getLogger

if TYPE_CHECKING:
    from main import TargetBot

_log = getLogger('TargetBot.command_handler')


class HandlerContext(commands.Context['TargetBot']):
    command: HandlerCommand


async def command_callback(ctx: HandlerContext):
    await ctx.send(content=ctx.command.content, embed=ctx.command.embed)


class HandlerCommand(commands.Command[Any, ..., Any]):
    def __init__(
        self, *, name: str, aliases: list[str], content: str | None, embed: dict | None, description: str | None
    ) -> None:
        super().__init__(command_callback, aliases=aliases, name=name, brief=description)  # type: ignore
        self.content = content
        if embed:
            self.embed: discord.Embed | None = discord.Embed.from_dict(embed)
        else:
            self.embed = None
