from __future__ import annotations
from dataclasses import dataclass, field
from logging import getLogger
from typing_extensions import Self
import discord
import asyncpg
import asyncio
from discord.ext import commands
from main import TargetBot


FORUM_CHANNEL_ID = 1360292638993154260
BANNED_TAG_ID = 1360292846363476068

log = getLogger(__name__)


@dataclass
class DM:
    user_id: int
    thread_id: int | None = None
    messages: list[tuple[discord.Message, discord.Message]] = field(default_factory=list)
    # list[tuple[(message in DMs, message in thread)]]

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        return cls(
            user_id=record["user_id"],
            thread_id=record["channel_id"],
        )

    def update(self, record: asyncpg.Record) -> None:
        self.thread_id = record["channel_id"]


class Webhook:
    def __init__(self, webhook: discord.Webhook) -> None:
        self.webhook = webhook
        self.send_lock = asyncio.Lock()
        self.channel_ids: list[int] = []

    async def send(self, *, message: discord.Message, thread: discord.Thread, dm: DM):
        async with self.send_lock:
            try:
                content = message.content + '\n'
                files: list[discord.File] = []
                errored: list[str] = []
                for attachment in message.attachments:
                    if attachment.size < thread.guild.filesize_limit:
                        files.append(await attachment.to_file())
                    else:
                        errored.append(f"[{attachment.filename}](<{attachment.url}>)")

                if errored:
                    content += f"\n-# Extra (too big) files: {', '.join(errored)}"

                reference = message.reference
                if reference and reference.message_id:
                    msgs = discord.utils.find(lambda x: x[0].id == reference.message_id, dm.messages)
                    if msgs:
                        content += f"\n-# replying to [this message](<{msgs[1].jump_url}>)"

                if len(content) > 2000:
                    embeds = [discord.Embed(description=content)]
                    content = ""
                else:
                    embeds = []

                message_sent = await self.webhook.send(
                    content=content,
                    files=files,
                    embeds=embeds,
                    username=message.author.name,
                    avatar_url=message.author.display_avatar.url,
                    thread=thread,
                    wait=True,
                )
                dm.messages.append((message, message_sent))

            except discord.HTTPException as e:
                await message.add_reaction('\N{WARNING SIGN}')
                await message.author.send(
                    embed=discord.Embed(
                        description='Failed to send message. You must provide <content> or <files>, or both.',
                        color=discord.Color.red(),
                    ),
                    delete_after=20,
                )
                log.error('Could not send message', exc_info=e)


class WebhookManager:
    def __init__(self, webhooks: list[discord.Webhook]) -> None:
        self.webhooks = [Webhook(w) for w in webhooks]
        self._get_lock = asyncio.Lock()

    async def get_webhook(self, channel_id: int) -> Webhook:
        async with self._get_lock:
            webhook_list = [w for w in self.webhooks if channel_id in w.channel_ids]
            if not webhook_list:
                kps = {len(w.channel_ids): w for w in self.webhooks}
                webhook = kps[min(kps.keys())]
                webhook.channel_ids.append(channel_id)
                return webhook
            return webhook_list[0]


class ModMail(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot: TargetBot = bot
        self.dms: dict[int, DM] = {}
        self.manager: WebhookManager | None = None

    async def get_manager(self) -> WebhookManager:
        await self.bot.wait_until_ready()
        if not self.manager:
            webhooks = await self.forum_channel.webhooks()
            if not webhooks:
                while True:
                    try:
                        await self.forum_channel.create_webhook(name="ModMail")
                    except:
                        log.error("Failed creating webhook.")
                        break
            self.manager = WebhookManager(webhooks)
        return self.manager

    @property
    def forum_channel(self) -> discord.ForumChannel:
        if not self.bot.is_ready():
            raise RuntimeError("Bot not ready")
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel:
            raise RuntimeError("Channel not found")
        assert isinstance(channel, discord.ForumChannel)
        return channel

    async def get_dm_object(self, obj: discord.User | discord.Member | discord.Thread) -> DM | None:
        """gets a DM object from the database or cache"""
        if isinstance(obj, discord.abc.User):
            dm = self.dms.get(obj.id)
            query = "SELECT * FROM modmail WHERE user_id = $1"
            fallback = "INSERT INTO modmail (user_id) VALUES ($1)"
        else:

            def pred(dm: DM):
                return dm.thread_id == obj.id

            dm = discord.utils.find(pred, self.dms.values())
            query = "SELECT * FROM modmail WHERE channel_id = $1"
            fallback = None
        if dm:
            return dm
        record = await self.bot.pool.fetchrow(query, obj.id)
        if not record:
            if fallback:
                await self.bot.pool.execute(fallback, obj.id)
                record = await self.bot.pool.fetchrow(query, obj.id)
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
                await self.process_dm(message, dm)
            return

        elif isinstance(message.channel, discord.Thread) and message.channel.parent_id == FORUM_CHANNEL_ID:
            dm = await self.get_dm_object(message.channel)
            if dm:
                return await self.process_message(message, dm)
            await message.delete()

    async def make_thread(self, message: discord.Message, dm: DM) -> discord.Thread:
        await message.author.send(
            embed=discord.Embed(
                title="You are now in contact with the StylizedRP moderators.",
                description="They will reply at their soonest convenience, please be patient.",
            )
        )

        thread, _ = await self.forum_channel.create_thread(
            name=str(message.author), content=f'DM with user of ID: {message.author.id}'
        )
        data = await self.bot.pool.fetchrow(
            'INSERT INTO modmail (user_id, channel_id) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET channel_id = $2 RETURNING *',
            message.author.id,
            thread.id,
        )
        if data:
            dm.update(data)
        return thread

    async def process_dm(self, message: discord.Message, dm: DM):
        """Takes a message and sends it to the DM channel"""
        if not dm.thread_id:
            thread = await self.make_thread(message, dm)
        else:
            thread = self.forum_channel.get_thread(dm.thread_id)

        if not thread:
            thread = await self.make_thread(message, dm)

            row = await self.bot.pool.fetchrow(
                "UPDATE modmail SET channel_id = $1 WHERE user_id = $2 RETURNING *",
                thread.id,
                message.author.id,
            )
            if not row:
                return
            dm.update(row)

        if BANNED_TAG_ID in thread._applied_tags:
            return await message.author.send("You are blacklisted from the modmail.")

        manager = await self.get_manager()
        webhook = await manager.get_webhook(thread.id)
        await webhook.send(message=message, thread=thread, dm=dm)

    async def process_message(self, message: discord.Message, dm: DM) -> None:
        user = self.bot.get_user(dm.user_id)
        if not user:
            await message.channel.send(embed=discord.Embed(title="No mutual guilds."), delete_after=5)
            return await message.add_reaction('\N{NO ENTRY}')

        content = f"**{message.author}:** {message.content}"

        reply = None
        reference = message.reference
        if reference and reference.message_id:

            found_messages = discord.utils.find(lambda x: x[1].id == reference.message_id, dm.messages)
            if found_messages:
                reply = discord.MessageReference(
                    message_id=found_messages[0].id,
                    channel_id=found_messages[0].channel.id,
                    guild_id=None,
                    fail_if_not_exists=False,
                )

        content = message.content + '\n'
        files: list[discord.File] = []
        errored: list[str] = []
        for attachment in message.attachments:
            if attachment.size < self.forum_channel.guild.filesize_limit:
                files.append(await attachment.to_file())
            else:
                errored.append(f"[{attachment.filename}]({attachment.url})")

        if errored:
            content += f"\n-# Some files could not be sent. Here are links instead: {', '.join(errored)}"

        if len(files) > len(message.attachments):
            content += "\n\n*some files could not be sent due to filesize limit*"

        try:
            try:
                msg = await user.send(content=content, files=files, reference=reply)
            except discord.HTTPException:
                await message.channel.send(embed=discord.Embed(title="User has DMs closed."), delete_after=5)
                return await message.add_reaction('\N{NO ENTRY}')
            else:
                dm.messages.append((msg, message))
        except discord.HTTPException:
            pass

    async def find_thread_messages(
        self, data: discord.RawMessageDeleteEvent | discord.RawMessageUpdateEvent
    ) -> tuple[discord.Message, discord.Message, DM, bool] | None:
        is_guild = False
        dm = None
        if data.guild_id:
            is_guild = True
            thread = self.forum_channel.get_thread(data.channel_id)
            if not thread:
                thread = await self.forum_channel.guild.fetch_channel(data.channel_id)

            if not isinstance(thread, discord.Thread) or thread.parent != self.forum_channel:
                return
            dm = await self.get_dm_object(thread)

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

        if not dm:
            return
        msgs = discord.utils.find(lambda m: m[int(is_guild)].id == data.message_id, dm.messages)
        if msgs:
            return *msgs, dm, is_guild

    @commands.Cog.listener("on_raw_message_delete")
    async def delete_listener(self, data: discord.RawMessageDeleteEvent):

        message_data = await self.find_thread_messages(data)
        if not message_data:
            return

        dm_message, staff_message, dm, is_message_from_guild = message_data
        dm.messages.remove((dm_message, staff_message))

        if is_message_from_guild:
            await dm_message.delete()
        else:
            await staff_message.edit(
                content=None,
                embed=discord.Embed(description=staff_message.content, color=discord.Color.red()).set_footer(
                    text="deleted message"
                ),
            )

    @commands.Cog.listener("on_raw_message_edit")
    async def update_listener(self, data: discord.RawMessageUpdateEvent):
        if data.data.get("author", {}).get("bot"):
            return

        message_data = await self.find_thread_messages(data)

        if not message_data:
            return

        dm_message, staff_message, _, is_message_from_guild = message_data
        content = data.data["content"]

        if is_message_from_guild:
            await dm_message.edit(content=content)
        else:
            await staff_message.edit(content=content)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModMail(bot))
