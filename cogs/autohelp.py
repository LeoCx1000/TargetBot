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
        801386133815951361,  # @staff
        717144039568441394,  # @Sublimer
        717144455156858940,  # @Sapphirer
        874395441477873694,  # @Steeler
        869040956350033930,  # @Forged Steeler
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

        if message.author.get_role(717144906350592061):
            embed = discord.Embed(
                description=(
                    "I see you're asking about downloads. To access the download chnanel, you need to have a "
                    "role `Steeler` or higher, but you seem to have the `Stoner` role.\nYou can get a `Steeler` "
                    "or higher subscription here: [patreon.com/stylized](https://www.patreon.com/Stylized)."
                    "\n_if you already have one, unlink and relink your patreon_"
                ),
                color=message.guild.me.color,
            )
        else:
            embed = discord.Embed(
                description=(
                    "I see you're asking about downloads. To access the download channel, you need to have a "
                    "role `Steeler` or higher, but you don't seem to have any roles.\nIf you already purchased "
                    f"a `Steeler` or higher subscription, link your Patreon to Discord. [[more info]]({support_link})"
                    f"\nIf your account is already linked, unlink and relink it. [[more info]]({support_link}) about "
                    "how to get your role.\nIf you don't already have a `Steeler` or higher subscription, you can get "
                    "one at [patreon.com/stylized](https://www.patreon.com/Stylized)."
                ),
                color=message.guild.me.color,
            )

        embed.set_author(name="Automatic support", icon_url="https://i.imgur.com/GTttbJW.png")
        await message.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(automod(bot))
