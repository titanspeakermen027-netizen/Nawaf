from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import get_config, set_config
from cogs.access_control import can_control_bot
from cogs.mini_games import MiniGames


class IndividualGames(commands.Cog):
    """Expose the existing mini-games only inside one configured channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.engine = MiniGames(bot)

    async def cog_load(self):
        return

    def allowed(self, message: discord.Message) -> bool:
        cfg = get_config(message.guild.id)
        channel_id = cfg["individual_game_channel"]
        return channel_id is not None and int(channel_id) == message.channel.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not self.allowed(message):
            return
        command = message.content.strip().casefold()
        handler = self.engine.handlers.get(command)
        if handler is not None:
            await handler(message)

    @app_commands.command(name="individual-games-room", description="تحديد أو إلغاء أو عرض روم الألعاب الفردية")
    @app_commands.describe(action="تعيين أو إلغاء أو عرض", channel="الروم الذي ستعمل فيه الألعاب الفردية")
    @app_commands.choices(action=[
        app_commands.Choice(name="تعيين", value="set"),
        app_commands.Choice(name="إلغاء", value="clear"),
        app_commands.Choice(name="عرض", value="show"),
    ])
    async def room(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel | None = None):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الإعداد للإدارة المخولة بالتحكم في البوت فقط.", ephemeral=True)
        if action.value == "show":
            cfg = get_config(interaction.guild.id)
            current = interaction.guild.get_channel(cfg["individual_game_channel"]) if cfg["individual_game_channel"] else None
            return await interaction.response.send_message(f"🎮 روم الألعاب الفردية: {current.mention if current else 'غير محدد'}", ephemeral=True)
        if action.value == "clear":
            set_config(interaction.guild.id, individual_game_channel=None)
            return await interaction.response.send_message("✅ تم إلغاء روم الألعاب الفردية.", ephemeral=True)
        if channel is None:
            return await interaction.response.send_message("❌ حدد الروم عند اختيار تعيين.", ephemeral=True)
        set_config(interaction.guild.id, individual_game_channel=channel.id)
        await interaction.response.send_message(f"✅ الألعاب الفردية ستعمل الآن فقط في {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(IndividualGames(bot))
