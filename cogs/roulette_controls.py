from __future__ import annotations

import asyncio

import discord
from discord.ext import commands


class RouletteControls(commands.Cog):
    """Controls for cancelling an active roulette without deleting its messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_session(self, guild_id: int, channel_id: int):
        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is None:
            return None
        return roulette.sessions.get((guild_id, channel_id))

    async def cancel(self, session) -> None:
        session.cancelled = True
        session.decision = None
        try:
            session.decision_event.set()
        except Exception:
            pass

        task = getattr(session, "lobby_task", None)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()

        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is not None:
            roulette.sessions.pop((session.guild_id, session.channel_id), None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.strip() != "-توقيف":
            return

        session = self.get_session(message.guild.id, message.channel.id)
        if session is None:
            return

        await self.cancel(session)
        await message.channel.send("✅ تم توقيف الروليت الحالية بدون حذف أي رسالة.")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is None:
            return

        for session in list(roulette.sessions.values()):
            lobby = getattr(session, "lobby_message", None)
            if lobby is None or lobby.id != payload.message_id:
                continue
            if getattr(session, "active", False) or getattr(session, "cancelled", False):
                continue

            await self.cancel(session)
            channel = self.bot.get_channel(payload.channel_id)
            if channel is not None:
                await channel.send("**تم الغاء عشان في حد حذف الرسالة حق اللوبي**")
            break


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteControls(bot))
