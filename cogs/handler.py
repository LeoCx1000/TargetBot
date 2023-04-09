from logging import getLogger

import discord
from discord.ext import commands

from main import TargetBot

_log = getLogger(__name__)


async def setup(bot):
    await bot.add_cog(Handler(bot))


class Handler(commands.Cog, name="Handler"):
    def __init__(self, bot):
        self.bot: TargetBot = bot

    @property
    def error_channel(self) -> discord.TextChannel:
        chnanel = self.bot.get_channel(880181130408636456)
        if isinstance(chnanel, discord.TextChannel):
            return chnanel
        raise RuntimeError("Channel not found.")

    @commands.Cog.listener("on_command_error")
    async def error_handler(self, ctx: commands.Context, error: Exception):
        error = getattr(error, "original", error)

        ignored = (
            commands.CommandNotFound,
            commands.CheckFailure,
        )

        if isinstance(error, ignored):
            return
        elif isinstance(error, commands.BadUnionArgument):
            es = str(error) + "\n\n" + "\n".join(str(e) for e in error.errors)
            await ctx.send(es)
        elif isinstance(error, commands.UserInputError):
            await ctx.send(str(error))
        else:
            await self.bot.errors.add_error(error=error, ctx=ctx)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if channel.guild.id != 717140270789033984:
            return
        if channel.type is not discord.ChannelType.text:
            return
        await channel.set_permissions(
            discord.Object(849734365293445132, type=discord.Role),
            attach_files=False,
            embed_links=False,
            reason=f"automatic NoMediaRole",
        )
