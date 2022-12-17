from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from logging import getLogger
import asyncpg
from asyncpg.transaction import Transaction
import json

import discord
from discord import app_commands, ui
from discord.ext import commands

from .utils.custom_commands import HandlerCommand
from main import TargetBot

_log = getLogger(__name__)
PATTERN = re.compile(r'[A-Za-z0-9_\-\.]+')
URLP = re.compile('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')


class AreYouSure(ui.View):
    if TYPE_CHECKING:
        message: discord.Message

    def __init__(self, user: discord.abc.User):
        self.owner = user
        self.confirmed = False
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return self.owner == interaction.user

    @ui.button(label='Delete Command')
    async def delete_command(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True


class Oops(ui.View):
    def __init__(self, modal: CreateCommandModal | EmbedModal, text: str = 'Edit Command'):
        super().__init__(timeout=30)
        self.modal = modal
        self.edit_command.label = text

    @discord.ui.button(label='...')
    async def edit_command(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(self.modal.copy())
        try:
            await interaction.delete_original_response()
        except:
            pass


class EmbedModal(ui.Modal, title='Create an embed for this command'):
    e_title = ui.TextInput(label='Title: The title for the embed', max_length=256)
    description = ui.TextInput(
        label='Description: The main body of the embed', required=False, style=discord.TextStyle.paragraph
    )
    colour = ui.TextInput(
        label='Colour: The colour of the embed',
        default='#1abc9c',
        required=False,
        placeholder='#1abc9c (default value)',
    )
    image = ui.TextInput(
        label='Image: An HTTPS link to an image',
        required=False,
        placeholder='https://imgur.com/my_image.png',
        min_length=10,
    )
    footer = ui.TextInput(label='Footer: The footer text to include', required=False, placeholder='Some text.')

    def __init__(self) -> None:
        super().__init__(timeout=3600)
        self.embed = None

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        self.interaction = interaction
        embed = discord.Embed(
            title=self.e_title.value.strip(),
            description=self.description.value.strip(),
            color=discord.Color.from_str(self.colour.value.strip()),
        )
        if self.footer.value.strip():
            embed.set_footer(text=self.footer.value.strip())
        if URLP.fullmatch(self.image.value.strip()):
            embed.set_image(url=self.image.value.strip())
        self.embed = embed
        self.stop()

    async def receive(self, interaction: discord.Interaction) -> tuple[discord.Embed | None, discord.Interaction]:
        await interaction.response.send_message(view=Oops(self, text='Create embed.'), ephemeral=True)
        try:
            await interaction.followup.send('', ephemeral=True, wait=True)
        except:
            pass
        await self.wait()
        return self.embed, self.interaction or interaction

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        _log.error('Error in modal:', exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(content=str(error), ephemeral=True)
        else:
            await interaction.response.send_message(content=str(error), ephemeral=True)

    def copy(self):
        return self


class CreateCommandModal(ui.Modal, title='Create a new custom command'):
    name = ui.TextInput(
        label='Name: A name for this command',
        placeholder='Max 20 characters: Must not already exist.',
        max_length=20,
    )
    description = ui.TextInput(
        label='Description: A description for this command',
        placeholder='Max 50 characters: It is used for the help command.',
        max_length=50,
    )
    content = ui.TextInput(
        label='Content: The command response',
        placeholder='Leave empty be promted to add an embed.',
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    aliases = ui.TextInput(
        label='Aliases: A list of comma-separated aliases.',
        placeholder='alias,another-alias (do not use whitespaces or "!")',
        style=discord.TextStyle.short,
        required=False,
    )
    raw_embed = ui.TextInput(
        label='Raw Embed: An embed in JSON format',
        style=discord.TextStyle.paragraph,
        required=False,
        placeholder="Type 'modal' here to add an embed via modal.",
    )

    def __init__(self, bot: TargetBot) -> None:
        super().__init__(timeout=3600)
        self.bot = bot

    COMMAND_QUERY = """
        INSERT INTO custom_commands (
            command_string,
            description, 
            command_content,
            embed
        ) VALUES ($1, $2, $3, $4)
        """

    ALIAS_QUERY = """
        INSERT INTO custom_commands (
            command_string,
            aliases_to
        ) VALUES ($1, $2)
        """

    async def validate_name(self, interaction: discord.Interaction, name: str) -> bool:
        if self.bot.get_command(name):
            await self.oops(interaction, description=True, msg='That command name already exists')
            return False
        if not PATTERN.fullmatch(name):
            await self.oops(interaction, name=True, msg='Name must only contain numbers, letters, "-" and "_".')
            return False
        return True

    def query_args(self, embed: discord.Embed | None):
        # TODO: Impl in child
        args: list[Any] = [self.name.value.strip(), self.description.value.strip(), self.content.value.strip()]
        if embed:
            args.append(embed.to_dict())
        else:
            args.append(None)
        return args

    async def on_submit(
        self, interaction: discord.Interaction, /, *, con: asyncpg.Connection | None = None, tr: Transaction | None = None
    ) -> None:
        # TODO: cleanup in child before adding
        async def inner(interaction: discord.Interaction, conn: asyncpg.Connection, tr: Transaction):
            name = self.name.value.strip()
            if not await self.validate_name(interaction, name):
                await tr.rollback()
                return
            if self.raw_embed.value == 'modal':
                embed, interaction = await EmbedModal().receive(interaction)
            elif not self.content.value.strip() and not self.raw_embed.value.strip():
                embed, interaction = await EmbedModal().receive(interaction)
            else:
                try:
                    data = json.loads(self.raw_embed.value)
                    data = data.get('embeds', [data])[0].get('embed', data)
                    embed = discord.Embed.from_dict(data)
                except:
                    await tr.rollback()
                    return await self.oops(interaction, msg='Embed JSON malformed or empty.')
                else:
                    if not len(embed):
                        await tr.rollback()
                        return await self.oops(interaction, msg='Embed JSON malformed or empty.')

            await conn.execute(self.COMMAND_QUERY, *self.query_args(embed))
            message = f"✅ Command `{name}`.\n"
            if self.aliases.value.strip():
                aliases = self.aliases.value.strip().split(',')
                for alias in set(map(lambda al: al.strip(), aliases)):
                    alias = alias.strip()
                    if not PATTERN.fullmatch(alias):
                        message += f'\n❕ Alias failed: `{alias}` (must only contain numbers and letters)'
                    if self.bot.get_command(alias):
                        message += f"\n❕ Alias failed: `{alias}` (already a command)"
                    if len(alias) > 20:
                        message += f"\n❕ Alias failed: `{alias}` (too long, max 20 characters)"
                    else:
                        await conn.execute(self.ALIAS_QUERY, alias, name)
                        message += f"\n☑️ Alias OK: `{alias}`"
            record = await conn.fetchrow(self.bot.CC_QUERY + '\nAND cc.command_string = $1', name)
            if record:
                self.bot.add_command(record)
                if interaction.response.is_done():
                    await interaction.followup.send(content=message, ephemeral=True)
                else:
                    await interaction.response.send_message(content=message, ephemeral=True)
            else:
                raise Exception('Something went wrong, <@349373972103561218>')

        if not con:
            ctm = self.bot.safe_connection()
            async with ctm as conn:
                return await inner(interaction, conn, ctm._tr)  # type: ignore
        return await inner(interaction, con, tr)  # type: ignore

    async def oops(
        self,
        interaction: discord.Interaction,
        *,
        msg: str,
        name: bool = False,
        description: bool = False,
        content: bool = False,
        aliases: bool = False,
        raw_embed: bool = False,
    ):
        if name:
            self.name.default = None
        else:
            self.name.default = self.name.value
        if content:
            self.content.default = None
        else:
            self.content.default = self.content.value
        if aliases:
            self.aliases.default = None
        else:
            self.aliases.default = self.aliases.value
        if description:
            self.description.default = None
        else:
            self.description.default = self.description.value
        if raw_embed:
            self.raw_embed.default = None
        else:
            self.raw_embed.default = self.raw_embed.value

        await interaction.response.send_message(
            embed=discord.Embed(title=msg, color=discord.Color.red()), view=Oops(self), ephemeral=True
        )
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        _log.error('Error in modal:', exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(content=str(error), ephemeral=True)
        else:
            await interaction.response.send_message(content=str(error), ephemeral=True)

    def copy(self):
        new = self.__class__(self.bot)
        new.name.default = self.name.default
        new.content.default = self.content.value
        new.aliases.default = self.aliases.value
        new.description.default = self.description.value
        new.raw_embed.default = self.raw_embed.value
        return new


class EditCommandModal(CreateCommandModal):
    COMMAND_QUERY = """
        UPDATE custom_commands 
            SET command_string = $1,
            description = $2,
            command_content = $3,
            embed = $4
        WHERE command_string = $5
        """

    def __init__(self, bot: TargetBot, command: HandlerCommand, result: asyncpg.Record) -> None:
        super().__init__(bot)
        self.command = command
        self.record = result

    def query_args(self, embed: discord.Embed | None):
        ret = super().query_args(embed)
        ret.append(self.record['command_string'])
        return ret

    def update(self, record: asyncpg.Record):
        self.name.default = record['command_string']
        self.description.default = record['description']
        self.content.default = record['command_content']
        if record['aliases']:
            self.aliases.default = ','.join(record['aliases'])
        if record['embed']:
            text = json.dumps(record['embed'], indent=2)
            if len(text) < 4000:
                self.raw_embed.default = text
            else:
                self.raw_embed.default = 'modal'

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cm = self.bot.safe_connection()
        async with cm as con:
            for alias in self.command.aliases:
                query = "DELETE FROM custom_commands WHERE command_string = $1"
                await con.execute(query, alias)
            self.bot.remove_command(self.command.name)
            return await super().on_submit(interaction, con=con, tr=cm._tr)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.bot.add_command(self.command)
        return await super().on_error(interaction, error)

    async def oops(self, *args, **kw):
        self.bot.add_command(self.command)
        return await super().oops(*args, **kw)


class CustomCommands(commands.Cog):
    def __init__(self, bot: TargetBot) -> None:
        self.bot: TargetBot = bot
        super().__init__()

    cc = app_commands.Group(
        name='customcommand',
        description='Base command to create custom commands.',
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @cc.command(name='create', description='Creates a new custom command')
    async def cc_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateCommandModal(self.bot))
        try:
            await interaction.delete_original_response()
        except:
            pass

    @cc.command(name='edit', description='Edits a custom command')
    @app_commands.describe(command='The command you want to edit')
    async def cc_edit(self, interaction: discord.Interaction, command: str):
        result = await self.bot.pool.fetchrow(self.bot.CC_QUERY + '\nAND command_string = $1', command)
        cmd = self.bot.get_command(command)
        if not result or not isinstance(cmd, HandlerCommand):
            return await interaction.response.send_message('Sorry, but that does not seem to be a command.', ephemeral=True)

        modal = EditCommandModal(self.bot, cmd, result)
        modal.update(result)
        await interaction.response.send_modal(modal)
        try:
            await interaction.delete_original_response()
        except:
            pass

    @cc.command(name='delete', description='Deletes a custom command.')
    @app_commands.describe(command='The command you want to delete', confirm='Confirm that you want to delete the commmand')
    async def cc_delete(self, interaction: discord.Interaction, command: str, confirm: bool = False):
        if not confirm:
            return await interaction.response.send_message('You must confirm, please try again.', ephemeral=True)
        cmd_obj = self.bot.remove_command(command)
        if not cmd_obj:
            return await interaction.response.send_message('That command doesn\'t exist.', ephemeral=True)
        await self.bot.pool.execute("DELETE FROM custom_commands WHERE command_string = $1", command)
        if command in cmd_obj.aliases:
            new = list(cmd_obj.aliases)
            new.remove(command)
            cmd_obj.aliases = new
            await interaction.response.send_message('Alias succesfully deleted.')
        else:
            await interaction.response.send_message('Command and corresponding aliases deleted.')

    @cc_delete.autocomplete('command')
    async def cc_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        if len(current.strip()) > 2:
            query = "SELECT command_string FROM custom_commands WHERE SIMILARITY(command_string, $1) > 0.1 LIMIT 25"
            rows = await self.bot.pool.fetch(query, current.strip())
        else:
            query = "SELECT command_string FROM custom_commands ORDER BY SIMILARITY(command_string, $1) LIMIT 25"
            rows = await self.bot.pool.fetch(query, current.strip())
        return [app_commands.Choice(name=r['command_string'], value=r['command_string']) for r in rows]

    @cc_edit.autocomplete('command')
    async def cce_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        if len(current.strip()) > 2:
            query = "SELECT command_string FROM custom_commands WHERE SIMILARITY(command_string, $1) > 0.1 AND aliases_to ISNULL LIMIT 25"
            rows = await self.bot.pool.fetch(query, current.strip())
        else:
            query = "SELECT command_string FROM custom_commands WHERE aliases_to ISNULL ORDER BY SIMILARITY(command_string, $1) LIMIT 25"
            rows = await self.bot.pool.fetch(query, current.strip())
        return [app_commands.Choice(name=r['command_string'], value=r['command_string']) for r in rows]


async def setup(bot):
    await bot.add_cog(CustomCommands(bot))
