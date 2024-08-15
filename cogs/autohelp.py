import discord
from discord.ext import commands

support_link = (
    "https://support.patreon.com/hc/en-us/articles/212052266-Get-my-Discord-role#"
    ":~:text=I%20connected%20my%20Discord%20account%20to%20Patreon%2C%20but%20I%E"
    "2%80%99m%20not%20on%20my%20creator%E2%80%99s%20Discord%20server.%20What%20sh"
    "ould%20I%20do%3F"
)


class automod(commands.Cog):
    IGNORED_ROLES = [
        740351860325613598,  # @ Creator
        717151202135113808,  # @ Admin
        763960697506758676,  # @ Head Mod
        801385716017004544,  # @ Moderator 
        813988893912203277,  # @ helper
        754743164883173387,  # @minecraft mod
        717144039568441394,  # @Sublimer
        717144455156858940,  # @ Sapphirer
        874395441477873694,  # @ Steeler
        869040956350033930,  # @Forged Steeler
        776996394191814658,  # downloads
    ]

    def __init__(self, bot):
        self.bot: commands.Bot = bot

    @commands.Cog.listener('on_message')
    async def automatic_support(self, message: discord.Message):
        if (
            message.author.bot
            or not message.guild
            or message.guild.id != 717140270789033984
            or "download" not in message.content.lower()
            or not isinstance(message.author, discord.Member)
            or any(message.author.get_role(r) for r in self.IGNORED_ROLES)
        ):
            return

        embed = discord.Embed(
            description=(
                "Please check out this announcement and any subsequent ones for information on the state of affairs of the resource pack: "
                    "https://discord.com/channels/717140270789033984/717149588141768815/1260330455027155047 "
                f"\n\nIf you were a previous subscriber, we recommend to cancel the membership for the time being, and not re-subscribe. "
                f"If you were not, you can purchase the pack at https://patreon.com/Stylized"
            ),
            color=message.guild.me.color,
        )

        embed.set_author(name="Automatic support", icon_url="https://i.imgur.com/GTttbJW.png")
        await message.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(automod(bot))
