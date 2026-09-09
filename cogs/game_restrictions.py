from __future__ import annotations

import discord
from discord.ext import commands

from cogs.access_control import can_manage_events
from cogs.game_channels import is_group_game_channel_allowed


class GameRestrictions(commands.Cog):
    """Shared checks for event permissions and allowed event channels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def allowed(message: discord.Message) -> bool:
        return bool(message.guild and is_group_game_channel_allowed(message.guild.id, message.channel.id))

    @staticmethod
    def can_start(member: discord.Member) -> bool:
        return can_manage_events(member)

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفرات.", ephemeral=True)
            return False
        if not can_manage_events(interaction.user):
            await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يدير الفعاليات.", ephemeral=True)
            return False
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(GameRestrictions(bot))
