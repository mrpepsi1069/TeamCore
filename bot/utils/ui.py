"""utils/ui.py — reusable TeamCore embeds and interactive views (confirm dialog, paginator)."""

import discord
from config import COLORS


def brand(title: str, description: str = None, color: int = None) -> discord.Embed:
    """The standard TeamCore embed: consistent colour + footer."""
    e = discord.Embed(title=title, description=description, color=color or COLORS["primary"])
    e.set_footer(text="TeamCore")
    return e


def bar(fraction: float, width: int = 10) -> str:
    n = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * n + "░" * (width - n)


class ConfirmView(discord.ui.View):
    """Ephemeral Yes/No dialog. After `await view.wait()`, check `view.value` (True/False/None=timed out)."""

    def __init__(self, user_id: int, confirm_label: str = "Confirm", timeout: float = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: bool | None = None
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class Paginator(discord.ui.View):
    """Prev/Next buttons over a list of embeds."""

    def __init__(self, pages: list, user_id: int | None = None, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.index = 0
        self.user_id = user_id
        self.message = None
        self._sync()

    def _sync(self):
        self.prev.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.user_id and interaction.user.id != self.user_id:
            await interaction.response.send_message("Run the command yourself to page through it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


async def send_paged(interaction: discord.Interaction, pages: list, **kwargs):
    """Send page 0 (with buttons only if there's more than one page). Works before or after defer()."""
    if len(pages) == 1:
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        return await send(embed=pages[0], **kwargs)
    view = Paginator(pages, interaction.user.id)
    if interaction.response.is_done():
        view.message = await interaction.followup.send(embed=pages[0], view=view, wait=True, **kwargs)
    else:
        await interaction.response.send_message(embed=pages[0], view=view, **kwargs)
        view.message = await interaction.original_response()


def parse_color(text: str | None, default: int = 0x5865F2) -> int | None:
    """'#FF8800' / 'ff8800' / '0xFF8800' -> int. Empty -> default. Invalid -> None."""
    if not text or not text.strip():
        return default
    t = text.strip().lstrip("#").lower().removeprefix("0x")
    if len(t) != 6:
        return None
    try:
        return int(t, 16)
    except ValueError:
        return None
