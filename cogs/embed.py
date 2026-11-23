"""cogs/embed.py — Interactive embed builder (buttons, modals, channel dropdown) + editing of the bot's own embeds."""

import re
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed
from utils.permissions import is_manager_or_mod
from utils.ui import parse_color
from utils.validation import is_valid_url

LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


class EmbedData:
    def __init__(self):
        self.title = self.description = self.author_name = self.author_icon = None
        self.footer_text = self.footer_icon = self.thumbnail = self.image = None
        self.color = 0x5865F2
        self.timestamp = False
        self.fields: list[tuple] = []

    @classmethod
    def from_embed(cls, e: discord.Embed) -> "EmbedData":
        d = cls()
        d.title, d.description = e.title, e.description
        d.color = e.color.value if e.color else 0x5865F2
        d.author_name, d.author_icon = (e.author.name, e.author.icon_url) if e.author else (None, None)
        d.footer_text, d.footer_icon = (e.footer.text, e.footer.icon_url) if e.footer else (None, None)
        d.thumbnail, d.image = (e.thumbnail.url if e.thumbnail else None), (e.image.url if e.image else None)
        d.timestamp = e.timestamp is not None
        d.fields = [(f.name, f.value, f.inline) for f in e.fields]
        return d

    def build(self) -> discord.Embed:
        e = discord.Embed(title=self.title, description=self.description, color=self.color)
        if self.author_name:
            e.set_author(name=self.author_name, icon_url=self.author_icon)
        if self.footer_text:
            e.set_footer(text=self.footer_text, icon_url=self.footer_icon)
        if self.thumbnail:
            e.set_thumbnail(url=self.thumbnail)
        if self.image:
            e.set_image(url=self.image)
        if self.timestamp:
            e.timestamp = datetime.now(timezone.utc)
        for n, v, i in self.fields:
            e.add_field(name=n, value=v, inline=i)
        return e

    def empty(self) -> bool:
        return not any([self.title, self.description, self.fields, self.image, self.author_name, self.footer_text, self.thumbnail])


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _bad_url(*urls) -> str | None:
    return next((u for u in urls if u and not is_valid_url(u)), None)


class TextModal(discord.ui.Modal, title="Title, description & colour"):
    def __init__(self, b: "Builder"):
        super().__init__()
        self.b = b
        d = b.data
        self.t = discord.ui.TextInput(label="Title", required=False, max_length=256, default=d.title)
        self.desc = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000, default=d.description)
        self.col = discord.ui.TextInput(label="Colour (hex, e.g. #FF8800)", required=False, max_length=7, default=f"#{d.color:06X}")
        for i in (self.t, self.desc, self.col):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        c = parse_color(self.col.value)
        if c is None:
            return await interaction.response.send_message("❌ Invalid colour — use hex like `#FF8800`.", ephemeral=True)
        self.b.data.title, self.b.data.description, self.b.data.color = _clean(self.t.value), _clean(self.desc.value), c
        await self.b.refresh(interaction)


class AuthorFooterModal(discord.ui.Modal, title="Author & footer"):
    def __init__(self, b: "Builder"):
        super().__init__()
        self.b = b
        d = b.data
        self.an = discord.ui.TextInput(label="Author name", required=False, max_length=256, default=d.author_name)
        self.ai = discord.ui.TextInput(label="Author icon URL", required=False, default=d.author_icon)
        self.ft = discord.ui.TextInput(label="Footer text", required=False, max_length=2048, default=d.footer_text)
        self.fi = discord.ui.TextInput(label="Footer icon URL", required=False, default=d.footer_icon)
        for i in (self.an, self.ai, self.ft, self.fi):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        bad = _bad_url(_clean(self.ai.value), _clean(self.fi.value))
        if bad:
            return await interaction.response.send_message("❌ Icon URLs must start with http(s)://", ephemeral=True)
        d = self.b.data
        d.author_name, d.author_icon, d.footer_text, d.footer_icon = _clean(self.an.value), _clean(self.ai.value), _clean(self.ft.value), _clean(self.fi.value)
        await self.b.refresh(interaction)


class ImagesModal(discord.ui.Modal, title="Thumbnail & image"):
    def __init__(self, b: "Builder"):
        super().__init__()
        self.b = b
        self.th = discord.ui.TextInput(label="Thumbnail URL (small, top right)", required=False, default=b.data.thumbnail)
        self.im = discord.ui.TextInput(label="Main image URL (large, bottom)", required=False, default=b.data.image)
        self.add_item(self.th)
        self.add_item(self.im)

    async def on_submit(self, interaction: discord.Interaction):
        if _bad_url(_clean(self.th.value), _clean(self.im.value)):
            return await interaction.response.send_message("❌ Image URLs must start with http(s)://", ephemeral=True)
        self.b.data.thumbnail, self.b.data.image = _clean(self.th.value), _clean(self.im.value)
        await self.b.refresh(interaction)


class FieldModal(discord.ui.Modal, title="Add a field"):
    def __init__(self, b: "Builder"):
        super().__init__()
        self.b = b
        self.n = discord.ui.TextInput(label="Field name", max_length=256)
        self.v = discord.ui.TextInput(label="Field value", style=discord.TextStyle.paragraph, max_length=1024)
        self.i = discord.ui.TextInput(label="Inline? (yes/no)", required=False, max_length=3, default="no")
        for x in (self.n, self.v, self.i):
            self.add_item(x)

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.b.data.fields) >= 25:
            return await interaction.response.send_message("❌ An embed can have at most 25 fields.", ephemeral=True)
        self.b.data.fields.append((self.n.value.strip(), self.v.value.strip(), self.i.value.strip().lower() in ("y", "yes", "true", "1")))
        await self.b.refresh(interaction)


class Builder(discord.ui.View):
    def __init__(self, user_id: int, guild: discord.Guild, default_channel, data: EmbedData | None = None, edit_message: discord.Message | None = None):
        super().__init__(timeout=900)
        self.user_id, self.guild = user_id, guild
        self.data = data or EmbedData()
        self.target = default_channel
        self.edit_message = edit_message
        if edit_message is None:  # channel dropdown only when publishing a new embed
            sel = discord.ui.ChannelSelect(placeholder=f"Publish to… (default: #{default_channel.name})", channel_types=[discord.ChannelType.text, discord.ChannelType.news], min_values=0, max_values=1, row=2)
            sel.callback = self._pick_channel
            self.add_item(sel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This builder belongs to someone else.", ephemeral=True)
            return False
        return True

    def preview(self) -> discord.Embed:
        if self.data.empty():
            return discord.Embed(title="Embed preview", description="Your embed is empty — use the buttons below to add content.", color=self.data.color)
        return self.data.build()

    def header(self) -> str:
        where = "**Editing an existing embed**" if self.edit_message else f"Publishing to {self.target.mention}"
        return f"{where} — live preview below ({len(self.preview())}/6000 characters)"

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=self.header(), embed=self.preview(), view=self)

    async def _pick_channel(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        ch = self.guild.get_channel(int(values[0])) if values else None
        if ch is not None:
            self.target = ch
        await self.refresh(interaction)

    @discord.ui.button(label="Title / Text / Colour", style=discord.ButtonStyle.primary, row=0)
    async def text_btn(self, interaction, button):
        await interaction.response.send_modal(TextModal(self))

    @discord.ui.button(label="Author / Footer", style=discord.ButtonStyle.primary, row=0)
    async def af_btn(self, interaction, button):
        await interaction.response.send_modal(AuthorFooterModal(self))

    @discord.ui.button(label="Images", style=discord.ButtonStyle.primary, row=0)
    async def img_btn(self, interaction, button):
        await interaction.response.send_modal(ImagesModal(self))

    @discord.ui.button(label="Add field", style=discord.ButtonStyle.secondary, row=1)
    async def field_btn(self, interaction, button):
        await interaction.response.send_modal(FieldModal(self))

    @discord.ui.button(label="Remove last field", style=discord.ButtonStyle.secondary, row=1)
    async def unfield_btn(self, interaction, button):
        if self.data.fields:
            self.data.fields.pop()
        await self.refresh(interaction)

    @discord.ui.button(label="Toggle timestamp", style=discord.ButtonStyle.secondary, row=1)
    async def ts_btn(self, interaction, button):
        self.data.timestamp = not self.data.timestamp
        await self.refresh(interaction)

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.success, row=3)
    async def publish_btn(self, interaction: discord.Interaction, button):
        if self.data.empty():
            return await interaction.response.send_message("❌ Add some content first.", ephemeral=True)
        embed = self.data.build()
        if len(embed) > 6000:
            return await interaction.response.send_message(f"❌ Embeds are limited to 6000 characters in total (yours is {len(embed)}).", ephemeral=True)
        try:
            if self.edit_message:
                await self.edit_message.edit(embed=embed)
                done = f"✅ Updated {self.edit_message.jump_url}"
            else:
                if not self.target.permissions_for(interaction.user).send_messages:
                    return await interaction.response.send_message(f"❌ You can't send messages in {self.target.mention}.", ephemeral=True)
                msg = await self.target.send(embed=embed)
                done = f"✅ Published: {msg.jump_url}"
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"❌ Couldn't publish: {e}", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content=done, embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=3)
    async def cancel_btn(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class EmbedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="embed", description="Build custom embeds", guild_only=True)

    async def _deny(self, interaction):
        await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)

    @group.command(name="create", description="Open the embed builder")
    async def create(self, interaction: discord.Interaction):
        if not await is_manager_or_mod(interaction):
            return await self._deny(interaction)
        b = Builder(interaction.user.id, interaction.guild, interaction.channel)
        await interaction.response.send_message(content=b.header(), embed=b.preview(), view=b, ephemeral=True)

    @group.command(name="edit", description="Edit an embed the bot previously posted")
    @app_commands.describe(message="Message link (or ID if it's in this channel)")
    async def edit(self, interaction: discord.Interaction, message: str):
        if not await is_manager_or_mod(interaction):
            return await self._deny(interaction)
        m = LINK_RE.search(message)
        gid, cid, mid = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (interaction.guild_id, interaction.channel_id, int(message) if message.strip().isdigit() else 0)
        channel = interaction.guild.get_channel(cid) if gid == interaction.guild_id else None
        if channel is None or not mid:
            return await interaction.response.send_message(embed=error_embed("Invalid Message", "Paste a message link from this server."), ephemeral=True)
        if not channel.permissions_for(interaction.user).send_messages:
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You can't post in that channel."), ephemeral=True)
        try:
            msg = await channel.fetch_message(mid)
        except discord.HTTPException:
            return await interaction.response.send_message(embed=error_embed("Not Found", "I couldn't find that message."), ephemeral=True)
        if msg.author.id != self.bot.user.id or not msg.embeds:
            return await interaction.response.send_message(embed=error_embed("Can't Edit", "I can only edit embeds that I posted."), ephemeral=True)
        b = Builder(interaction.user.id, interaction.guild, channel, EmbedData.from_embed(msg.embeds[0]), edit_message=msg)
        await interaction.response.send_message(content=b.header(), embed=b.preview(), view=b, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedCog(bot))
