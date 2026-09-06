from __future__ import annotations

import asyncio
import io
import random

import discord
from discord.ext import commands

from cogs.player_wheel import collect_avatar_bytes, render_player_wheel


class RouletteVisual(commands.Cog):
    """Replaces the text-only roulette animation with a generated player wheel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.original_spin_wheel = None
        self.original_final_duel = None

    async def cog_load(self):
        roulette = self.bot.get_cog("RouletteUpgrade")
        if roulette:
            self.patch(roulette)

    @staticmethod
    def _members(roulette, session):
        guild = roulette.bot.get_guild(session.guild_id)
        if not guild:
            return []
        return [member for uid in session.players if (member := guild.get_member(uid)) is not None]

    async def _send_wheel(self, message: discord.Message, session, selected: int, avatars):
        members = self._members(self.roulette, session)
        if not members:
            return
        png = render_player_wheel(members, selected, avatars, session.round_number)
        file = discord.File(io.BytesIO(png), filename="nawaf-wheel.png")
        embed = discord.Embed(
            title="🎯 العجلة كتدور...",
            description=f"**الجولة {session.round_number}**\n\nالمؤشر كيتحرك بين جميع المشاركين.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="👤 الاختيار الحالي", value=f"<@{selected}>", inline=True)
        embed.add_field(name="👥 المشاركون", value=f"**{len(session.players)}**", inline=True)
        embed.set_image(url="attachment://nawaf-wheel.png")
        embed.set_footer(text="عجلة أصلية داخل Nawaf • الصورة كتتبدل مع كل حركة")
        await message.edit(embed=embed, view=None, attachments=[file])

    async def spin_with_image(self, message: discord.Message, session) -> int:
        members = self._members(self, session)
        if not members:
            return random.choice(session.players)
        avatars = await collect_avatar_bytes(members)
        selected = random.choice(session.players)
        steps = max(10, min(16, len(session.players) + 5))
        for index in range(steps):
            selected = random.choice(session.players)
            await self._send_wheel(message, session, selected, avatars)
            await asyncio.sleep(0.16 + index * 0.018)
        return selected

    async def final_with_image(self, message: discord.Message, session) -> None:
        members = self._members(self, session)
        first, second = session.players
        avatars = await collect_avatar_bytes(members)
        selected = first
        for index in range(10):
            selected = first if index % 2 == 0 else second
            await self._send_wheel(message, session, selected, avatars)
            await asyncio.sleep(0.17 + index * 0.02)

        winner = random.choice((first, second))
        self.roulette.add_points(session.guild_id, winner, session.reward)
        png = render_player_wheel(members, winner, avatars, session.round_number)
        file = discord.File(io.BytesIO(png), filename="nawaf-wheel.png")
        embed = discord.Embed(
            title="🏆 الروليت انتهات",
            description=f"🎉 الفائز هو <@{winner}>!\n\n⭐ ربح **{session.reward} نقطة**.",
            color=discord.Color.green(),
        )
        embed.add_field(name="🔄 الجولات", value=f"**{session.round_number}**", inline=True)
        embed.add_field(name="👥 آخر متنافسين", value=f"<@{first}> و <@{second}>", inline=True)
        embed.set_image(url="attachment://nawaf-wheel.png")
        embed.set_footer(text="العجلة وقفت بصرياً على الفائز")
        await message.edit(embed=embed, view=None, attachments=[file])

    def patch(self, roulette):
        self.roulette = roulette
        self.original_spin_wheel = roulette.spin_wheel
        self.original_final_duel = roulette.final_duel
        roulette.spin_wheel = self.spin_with_image
        roulette.final_duel = self.final_with_image


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteVisual(bot))
