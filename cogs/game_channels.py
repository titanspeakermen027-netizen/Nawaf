from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import connect

MAX_GAME_CHANNELS = 3


def init_game_channels_table() -> None:
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS group_game_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )


def get_allowed_game_channels(guild_id: int) -> list[int]:
    init_game_channels_table()
    with connect() as con:
        rows = con.execute(
            "SELECT channel_id FROM group_game_channels WHERE guild_id=? ORDER BY channel_id",
            (guild_id,),
        ).fetchall()
    return [int(row[0]) for row in rows]


def is_group_game_channel_allowed(guild_id: int, channel_id: int) -> bool:
    channels = get_allowed_game_channels(guild_id)
    # Backward-compatible mode: until an admin configures channels, games remain available anywhere.
    return not channels or channel_id in channels


def add_game_channel(guild_id: int, channel_id: int) -> tuple[bool, str]:
    channels = get_allowed_game_channels(guild_id)
    if channel_id in channels:
        return False, "⚠️ هاد الروم مضاف من قبل."
    if len(channels) >= MAX_GAME_CHANNELS:
        return False, f"❌ وصلتي للحد الأقصى: **{MAX_GAME_CHANNELS} رومات** للألعاب الجماعية."

    with connect() as con:
        con.execute(
            "INSERT INTO group_game_channels(guild_id, channel_id) VALUES(?, ?)",
            (guild_id, channel_id),
        )
    return True, "✅ تزاد الروم بنجاح."


def remove_game_channel(guild_id: int, channel_id: int) -> bool:
    with connect() as con:
        cur = con.execute(
            "DELETE FROM group_game_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        return cur.rowcount > 0


class GameChannels(commands.GroupCog, group_name="game-channels"):
    """Administration controls for the channels where group games may run."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_game_channels_table()

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message("❌ هاد الأمر خاص بالسيرفرات.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ غير الإدارة تقدر تبدل إعدادات رومات الألعاب.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="add", description="أضف روم مسموح للألعاب الجماعية")
    @app_commands.describe(channel="الروم اللي بغيتي تسمح فيه بالألعاب الجماعية")
    async def add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        assert interaction.guild is not None
        ok, text = add_game_channel(interaction.guild.id, channel.id)
        if ok:
            count = len(get_allowed_game_channels(interaction.guild.id))
            text += f"\n📊 دابا مسموح بـ **{count}/{MAX_GAME_CHANNELS}** رومات."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="remove", description="حيد روم من رومات الألعاب الجماعية")
    @app_commands.describe(channel="الروم اللي بغيتي تحيد")
    async def remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        assert interaction.guild is not None
        if remove_game_channel(interaction.guild.id, channel.id):
            await interaction.response.send_message(f"✅ تحيد {channel.mention} من رومات الألعاب.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ هاد الروم ما كانش مضاف.", ephemeral=True)

    @app_commands.command(name="list", description="شوف رومات الألعاب الجماعية المسموح بها")
    async def list_channels(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        ids = get_allowed_game_channels(interaction.guild.id)
        if not ids:
            await interaction.response.send_message(
                "ℹ️ مازال ما تحددو حتى روم. الألعاب الجماعية دابا مسموحة بشكل عادي فالرومات.",
                ephemeral=True,
            )
            return

        lines = []
        for index, channel_id in enumerate(ids, start=1):
            channel = interaction.guild.get_channel(channel_id)
            lines.append(f"**{index}.** {channel.mention if channel else f'<#{channel_id}>'}")
        await interaction.response.send_message(
            f"🎮 **رومات الألعاب الجماعية** — {len(ids)}/{MAX_GAME_CHANNELS}\n" + "\n".join(lines),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GameChannels(bot))
