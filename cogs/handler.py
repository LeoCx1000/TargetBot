import io
import traceback
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
    async def error_handler(self, ctx: commands.Context, error):
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
            traceback_string = "".join(traceback.format_exception(error))
            _log.error("Unhandled Exception in command %s", ctx.command, exc_info=error)
            if ctx.guild:
                command_data = (
                    f"by: {ctx.author.name} ({ctx.author.id})"
                    f"\ncommand: {ctx.message.content[0:1700]}"
                    f"\nguild_id: {ctx.guild.id} - channel_id: {ctx.channel.id}"
                    f"\nowner: {ctx.guild.owner} ({getattr(ctx.guild.owner, 'id', None)})"
                )
            else:
                command_data = f"command: {ctx.message.content[0:1700]}" f"\nCommand executed in DMs"

            to_send = f"```yaml\n{command_data}``````py\n{ctx.command} " f"command raised an error:\n{traceback_string}\n```"
            if len(to_send) < 2000:
                await self.error_channel.send(to_send)

            else:
                await self.error_channel.send(
                    f"```yaml\n{command_data}``````py Command: {ctx.command}" f"Raised the following error:\n```",
                    file=discord.File(io.BytesIO(traceback_string.encode()), filename="traceback.py"),
                )

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
