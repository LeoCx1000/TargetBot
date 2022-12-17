from __future__ import annotations
from dataclasses import dataclass, field
from logging import getLogger
from typing_extensions import Self
import re
import discord
import asyncpg
from discord.ext import commands
from main import TargetBot


CATEGORY_ID = 881734985244086342

pattern = re.compile(r"Maya (?P<NUM>\d+): (?P<MSG>.+)")
log = getLogger(__name__)


@dataclass
class DM:
    user_id: int
    enabled: bool
    channel: int | None = None
    wh_url: str | None = None
    messages: list[tuple[discord.Message, discord.Message]] = field(
        default_factory=list
    )

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        return cls(
            user_id=record["user_id"],
            enabled=record["dms_enabled"],
            channel=record["dm_channel"],
            wh_url=record["dm_webhook"],
        )

    def update(self, record: asyncpg.Record) -> None:
        self.enabled = record["dms_enabled"]
        self.channel = record["dm_channel"]
        self.wh_url = record["dm_webhook"]


class TestingShit(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot: TargetBot = bot
        self.dms: dict[int, DM] = {}

    @property
    def dm_category(self) -> discord.CategoryChannel:
        if not self.bot.is_ready():
            raise RuntimeError("Bot not ready")
        channel = self.bot.get_channel(CATEGORY_ID)
        if not channel:
            raise RuntimeError("Channel not found")
        assert isinstance(channel, discord.CategoryChannel)
        return channel

    async def get_dm_object(
        self, obj: discord.User | discord.Member | discord.TextChannel
    ) -> DM | None:
        """gets a DM object from the database or cache"""
        if isinstance(obj, discord.abc.User):
            dm = self.dms.get(obj.id)
            query = "SELECT * FROM dm_flow WHERE user_id = $1"
            fallback = "INSERT INTO dm_flow (user_id) VALUES $1"
        else:

            def pred(dm: DM):
                return dm.channel == obj.id

            dm = discord.utils.find(pred, self.dms.values())
            query = "SELECT * FROM dm_flow WHERE dm_channel = $1"
            fallback = None
        if dm:
            return dm
        record = await self.bot.pool.fetchrow(query, obj.id)
        if not record:
            if fallback:
                await self.bot.pool.execute(fallback, obj.id)
                record = await self.bot.pool.fetchrow(query, obj.id)
                await obj.send(
                    "You are now contacting the moderators. They will reply soon."
                )
        if record:
            dm = DM.from_record(record)
            self.dms[obj.id] = dm
            return dm

    @commands.Cog.listener("on_message")
    async def events_handler(self, message: discord.Message):
        """Takes a message, runs checks and passes information on to the main functions"""
        if message.author.bot:
            return

        if message.channel.type is discord.ChannelType.private:
            dm = await self.get_dm_object(message.author)
            if dm:
                if dm.enabled:
                    await self.process_dm(message, dm)
                else:
                    return await message.author.send(
                        "You are blacklisted from the modmail"
                    )
            else:
                return

        elif (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.category_id == CATEGORY_ID
        ):
            dm = await self.get_dm_object(message.channel)
            if dm:
                return await self.process_message(message, dm)
            await message.delete()

    async def process_dm(self, message: discord.Message, dm: DM) -> None:
        """Takes a message and sends it to the DM channel"""
        channel = self.dm_category.guild.get_channel(dm.channel or 0)
        if not channel:
            channel = await self.dm_category.create_text_channel(
                name=str(message.author), topic=str(message.author.id)
            )
            webhook = await channel.create_webhook(
                name=message.author.name,
                avatar=await message.author.display_avatar.read(),
            )
            row = await self.bot.pool.fetchrow(
                "UPDATE dm_flow SET dm_channel = $1, dm_webhook = $2 WHERE user_id = $3 RETURNING *",
                channel.id,
                webhook.url,
                message.author.id,
            )
            if not row:
                return
            dm.update(row)
        assert dm.wh_url
        webhook = discord.Webhook.from_url(dm.wh_url, session=self.bot.session)

        content = message.content

        reference = message.reference
        if reference and reference.message_id:
            msgs = discord.utils.find(
                lambda x: x[0].id == reference.message_id, dm.messages
            )
            if msgs:
                content += f"\n\n*replying to [this message](<{msgs[1].jump_url}>)*"

        files = [
            await a.to_file()
            for a in message.attachments
            if a.size >= self.dm_category.guild.filesize_limit
        ]

        if len(files) > len(message.attachments):
            content += "\n\n*some files could not be sent due to filesize limit*"

        try:
            try:
                wh_msg = await webhook.send(content=content, files=files, wait=True)
                dm.messages.append((message, wh_msg))
                await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            except discord.HTTPException:
                await message.add_reaction("\N{WARNING SIGN}")
        except discord.HTTPException:
            pass

    async def process_message(self, message: discord.Message, dm: DM) -> None:
        user = self.bot.get_user(dm.user_id)
        if not user:
            await message.channel.send(
                embed=discord.Embed(title="No mutual guilds."), delete_after=5
            )
            return await message.delete()

        content = f"**{message.author}:** {message.content}"
        reference = message.reference
        reply: discord.Message = None  # type: ignore
        if reference and reference.message_id:
            msgs = discord.utils.find(
                lambda x: x[1].id == reference.message_id, dm.messages
            )
            if msgs:
                reply = msgs[0]

        files = [
            await a.to_file()
            for a in message.attachments
            if a.size >= self.dm_category.guild.filesize_limit
        ]

        if len(files) > len(message.attachments):
            content += "\n\n*some files could not be sent due to filesize limit*"

        try:
            try:
                msg = await user.send(content=content, files=files, reference=reply)
            except discord.HTTPException:
                await message.delete()
                await message.channel.send(
                    embed=discord.Embed(title="User has DMs closed."), delete_after=5
                )
            else:
                dm.messages.append((msg, message))
        except discord.HTTPException:
            pass

    async def find_msgs(
        self, data: discord.RawMessageDeleteEvent | discord.RawMessageUpdateEvent
    ) -> tuple[discord.Message, discord.Message, DM, int] | None:
        cht = 0  # 0 = DM ; 1 = GUILD
        if data.guild_id:
            cht = 1
            channel = self.dm_category.guild.get_channel(data.channel_id)
            if not channel or channel.category != self.dm_category:
                return
            dm = await self.get_dm_object(channel)  # type: ignore
        else:
            if data.cached_message:
                dm = await self.get_dm_object(data.cached_message.author)
            else:
                try:
                    channel = await self.bot.fetch_channel(data.channel_id)
                except discord.HTTPException:
                    return
                if isinstance(channel, discord.DMChannel):
                    if channel.recipient:
                        dm = await self.get_dm_object(channel.recipient)
                    else:
                        dm = None
                else:
                    dm = await self.get_dm_object(channel)  # type: ignore  # how?
                    cht = 1

        if not dm:
            return
        msgs = discord.utils.find(lambda m: m[cht].id == data.message_id, dm.messages)
        if msgs:
            return *msgs, dm, cht

    @commands.Cog.listener("on_raw_message_delete")
    async def delete_listener(self, data: discord.RawMessageDeleteEvent):
        stuff = await self.find_msgs(data)
        if not stuff:
            return
        dm_message, staff_message, dm, cht = stuff
        dm.messages.remove((dm_message, staff_message))
        if cht:
            await dm_message.delete()
        else:
            await staff_message.edit(
                content=None,
                embed=discord.Embed(
                    description=staff_message.content, color=discord.Color.red()
                ).set_footer(text="deleted message"),
            )

    @commands.Cog.listener("on_raw_message_edit")
    async def update_listener(self, data: discord.RawMessageUpdateEvent):
        if data.data.get("author", {}).get("bot"):
            return
        stuff = await self.find_msgs(data)
        if not stuff:
            return
        dm_message, staff_message, _, cht = stuff
        if cht:  # if message belongs to a guild
            new_message = discord.Message(
                state=self.bot._connection,
                channel=staff_message.channel,
                data=data.data,
            )
            await dm_message.edit(content=new_message.content)
        else:
            new_message = discord.Message(
                state=self.bot._connection, channel=dm_message.channel, data=data.data
            )
            await staff_message.edit(content=new_message.content)
