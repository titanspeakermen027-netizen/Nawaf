from __future__ import annotations

import asyncio
import contextlib
import io
import math
import random
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from database import connect
from cogs.access_control import can_manage_events
from cogs.game_channels import is_group_game_channel_allowed
from cogs.premium import get_roulette_max
from cogs.points import add_category_points

MIN_PLAYERS = 4
DEFAULT_MAX_PLAYERS = 12
LOBBY_SECONDS = 30
DECISION_SECONDS = 12
PLAYERS_PER_PAGE = 10


@dataclass
class Session:
    guild_id: int
    channel_id: int
    starter_id: int
    players: list[int] = field(default_factory=list)
    active: bool = False
    stopped: bool = False
    message_id: int | None = None
    round_number: int = 0
    task: asyncio.Task | None = None
    lobby_message: discord.Message | None = None



def add_roulette_points(guild_id: int, user_id: int, amount: int) -> None:
    add_category_points(guild_id, user_id, amount, "roulette")


def make_wheel_image(names: list[str], chosen: str | None = None) -> discord.File:
    size = 900
    image = Image.new("RGB", (size, size), (9, 12, 25))
    draw = ImageDraw.Draw(image)
    center = size // 2
    radius = 330
    count = max(1, len(names))
    palette = [(33, 150, 243), (255, 193, 7), (76, 175, 80), (244, 67, 54), (156, 39, 176), (0, 188, 212)]
    for index, name in enumerate(names):
        start = index * 360 / count
        end = (index + 1) * 360 / count
        draw.pieslice((center-radius, center-radius, center+radius, center+radius), start=start, end=end, fill=palette[index % len(palette)], outline=(245, 245, 245), width=2)
    draw.ellipse((center-95, center-95, center+95, center+95), fill=(15, 18, 30), outline=(255, 255, 255), width=5)
    font = ImageFont.load_default()
    if chosen:
        text = chosen[:24]
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((center-(bbox[2]-bbox[0])/2, center-(bbox[3]-bbox[1])/2), text, fill=(255, 255, 255), font=font)
    pointer = [(center, 45), (center-18, 85), (center+18, 85)]
    draw.polygon(pointer, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="roulette-wheel.png")


class LobbyView(discord.ui.View):
    def __init__(self, cog: "RouletteMultiMessage", session: Session):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.cog = cog
        self.session = session
        self.page = 0

    def pages(self) -> int:
        return max(1, math.ceil(len(self.session.players) / PLAYERS_PER_PAGE))

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page, self.pages() - 1)
        await interaction.response.edit_message(embed=self.cog.lobby_embed(self.session, self.page), view=self)

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.active or self.session.stopped:
            return await interaction.response.send_message("❌ الفعالية بدأت بالفعل.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما يقدروش يشاركو.", ephemeral=True)
        if interaction.user.id in self.session.players:
            return await interaction.response.send_message("❌ أنت أصلا مشارك بالفعالية", ephemeral=True)
        maximum = get_roulette_max(self.session.guild_id)
        if len(self.session.players) >= maximum:
            return await interaction.response.send_message(f"❌ وصلنا للحد الأقصى: **{maximum}** لاعب.", ephemeral=True)
        self.session.players.append(interaction.user.id)
        self.page = self.pages() - 1
        await self.refresh(interaction)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.active or self.session.stopped:
            return await interaction.response.send_message("❌ الفعالية بدأت بالفعل.", ephemeral=True)
        if interaction.user.id not in self.session.players:
            return await interaction.response.send_message("❌ أنت ماشي مشارك.", ephemeral=True)
        if interaction.user.id == self.session.starter_id:
            return await interaction.response.send_message("❌ مشغل الفعالية ما يقدرش يخرج. استعمل `-توقيف`.", ephemeral=True)
        self.session.players.remove(interaction.user.id)
        self.page = min(self.page, self.pages() - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="السابق", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page <= 0:
            return await interaction.response.send_message("❌ هذه أول صفحة.", ephemeral=True)
        self.page -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="التالي", style=discord.ButtonStyle.secondary, emoji="▶️", row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page >= self.pages() - 1:
            return await interaction.response.send_message("❌ هذه آخر صفحة.", ephemeral=True)
        self.page += 1
        await self.refresh(interaction)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="🎰", row=1)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يبدأ الفعالية.", ephemeral=True)
        if len(self.session.players) < MIN_PLAYERS:
            return await interaction.response.send_message(f"❌ خاص على الأقل **{MIN_PLAYERS}** لاعبين.", ephemeral=True)
        if self.session.active:
            return await interaction.response.send_message("⚠️ الفعالية خدامة دابا.", ephemeral=True)
        await interaction.response.defer()
        self.session.active = True
        self.stop()
        self.session.task = asyncio.create_task(self.cog.run(self.session, interaction.message))

    @discord.ui.button(label="توقيف", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def stop_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.stop_from_interaction(interaction, self.session)


class RouletteMultiMessage(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], Session] = {}

    def lobby_embed(self, session: Session, page: int = 0, remaining: int | None = None) -> discord.Embed:
        pages = max(1, math.ceil(len(session.players) / PLAYERS_PER_PAGE))
        page = max(0, min(page, pages - 1))
        start = page * PLAYERS_PER_PAGE
        ids = session.players[start:start + PLAYERS_PER_PAGE]
        guild = self.bot.get_guild(session.guild_id)
        lines = []
        for position, user_id in enumerate(ids, start=start + 1):
            member = guild.get_member(user_id) if guild else None
            username = member.name if member else f"عضو {user_id}"
            lines.append(f"**{position}.** {username} • <@{user_id}>")
        if not lines:
            lines.append("—")
        maximum = get_roulette_max(session.guild_id)
        timer = LOBBY_SECONDS if remaining is None else max(0, remaining)
        embed = discord.Embed(
            title="🎰 الروليت — فعالية إقصاء",
            description="انضم من الزر بالأسفل. بعد انتهاء العداد تبدأ الفعالية إذا اكتمل العدد الأدنى.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="👥 المشاركون", value=f"**{len(session.players)} / {maximum}**", inline=True)
        embed.add_field(name="✅ الحد الأدنى", value=f"**{MIN_PLAYERS}**", inline=True)
        embed.add_field(name="⏳ الوقت المتبقي", value=f"**{timer} ثانية**", inline=True)
        embed.add_field(name="📋 أسماء الحسابات", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"صفحة {page + 1}/{pages} • المتبقي قبل البداية: {timer} ثانية")
        return embed

    async def start_game(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not can_manage_events(message.author):
            await message.reply("❌ غير الإدارة أو رئيس الفعاليات يقدر يسوي الفعاليات.", mention_author=False)
            return
        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            await message.reply("❌ هاد الروم ما مسموحش فيه الفعاليات. استعمل روم محدد من الإدارة.", mention_author=False)
            return
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            await message.reply("❌ كاينة فعالية مفتوحة فهاد الروم.", mention_author=False)
            return
        session = Session(guild_id=message.guild.id, channel_id=message.channel.id, starter_id=message.author.id, players=[message.author.id])
        self.sessions[key] = session
        view = LobbyView(self, session)
        lobby = await message.channel.send(embed=self.lobby_embed(session), view=view)
        session.message_id = lobby.id
        session.lobby_message = lobby
        session.task = asyncio.create_task(self.lobby_countdown(session, lobby, view))

    async def lobby_countdown(self, session: Session, lobby: discord.Message, view: LobbyView) -> None:
        key = (session.guild_id, session.channel_id)
        try:
            for remaining in range(LOBBY_SECONDS, -1, -1):
                if session.stopped or session.active or key not in self.sessions:
                    return
                # Keep Discord edits low while the numeric timer still ticks visibly in the embed.
                if remaining in {30, 25, 20, 15, 10, 5, 4, 3, 2, 1, 0}:
                    with contextlib.suppress(discord.HTTPException):
                        await lobby.edit(embed=self.lobby_embed(session, view.page, remaining), view=view)
                await asyncio.sleep(1)
            if session.stopped or session.active:
                return
            if len(session.players) < MIN_PLAYERS:
                await lobby.edit(
                    embed=discord.Embed(title="❌ لم تبدأ الفعالية", description=f"انتهى الوقت ولم يصل العدد إلى **{MIN_PLAYERS}** لاعبين.", color=discord.Color.red()),
                    view=None,
                )
                return
            session.active = True
            view.stop()
            await self.run(session, lobby)
        finally:
            current = self.sessions.get(key)
            if current is session and not session.active:
                self.sessions.pop(key, None)

    async def run(self, session: Session, message: discord.Message) -> None:
        session.active = True
        session.players = list(dict.fromkeys(session.players))
        try:
            while len(session.players) > 1 and not session.stopped:
                session.round_number += 1
                names = []
                guild = self.bot.get_guild(session.guild_id)
                for user_id in session.players:
                    member = guild.get_member(user_id) if guild else None
                    names.append(member.name if member else str(user_id))

                for tick in range(6):
                    if session.stopped:
                        return
                    chosen = random.choice(names)
                    file = make_wheel_image(names, chosen)
                    embed = discord.Embed(
                        title="🎰 الروليت تدور...",
                        description=f"**الجولة {session.round_number}**\n🎯 الاختيار الحالي: **{chosen}**",
                        color=discord.Color.orange(),
                    )
                    embed.set_footer(text=f"👥 المتبقون: {len(session.players)}")
                    with contextlib.suppress(discord.HTTPException):
                        await message.edit(embed=embed, attachments=[file], view=None)
                    await asyncio.sleep(0.35 + tick * 0.04)

                eliminated = random.choice(session.players)
                member = guild.get_member(eliminated) if guild else None
                name = member.name if member else str(eliminated)
                session.players.remove(eliminated)
                result = discord.Embed(
                    title="🎰 تم الإقصاء",
                    description=f"❌ تم إقصاء **{name}**.\n\n👥 المتبقون: **{len(session.players)}**",
                    color=discord.Color.red(),
                )
                result.set_footer(text=f"الجولة {session.round_number}")
                with contextlib.suppress(discord.HTTPException):
                    await message.edit(embed=result, attachments=[], view=None)
                await asyncio.sleep(1.2)

            if session.stopped:
                return
            winner_id = session.players[0]
            add_roulette_points(session.guild_id, winner_id, 5)
            winner = guild.get_member(winner_id) if guild else None
            winner_name = winner.name if winner else str(winner_id)
            final = discord.Embed(
                title="🏆 الروليت — انتهت اللعبة",
                description=f"الفائز هو **{winner_name}** • <@{winner_id}>",
                color=discord.Color.green(),
            )
            final.add_field(name="⭐ الجائزة", value="**+5 نقطة**", inline=True)
            final.add_field(name="🔄 الجولات", value=f"**{session.round_number}**", inline=True)
            final.add_field(name="👥 المشاركون", value=f"**{len(session.players)}** متبقٍ", inline=True)
            final.set_footer(text="يمكن تشغيل فعالية جديدة بعد انتهاء هذه الفعالية.")
            with contextlib.suppress(discord.HTTPException):
                await message.edit(embed=final, attachments=[], view=None)
        except asyncio.CancelledError:
            raise
        finally:
            self.sessions.pop((session.guild_id, session.channel_id), None)

    async def stop_from_interaction(self, interaction: discord.Interaction, session: Session) -> None:
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يوقف الفعالية.", ephemeral=True)
        session.stopped = True
        task = session.task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.sessions.pop((session.guild_id, session.channel_id), None)
        await interaction.response.edit_message(
            embed=discord.Embed(title="⏹️ تم إيقاف اللعبة", description="✅ تم إيقاف اللعبة.", color=discord.Color.red()),
            view=None,
        )

    async def stop(self, message: discord.Message) -> bool:
        if not isinstance(message.author, discord.Member) or not can_manage_events(message.author):
            await message.reply("❌ غير الإدارة أو رئيس الفعاليات يقدر يوقف الفعاليات.", mention_author=False)
            return True
        key = (message.guild.id, message.channel.id)
        session = self.sessions.get(key)
        if not session:
            return False
        session.stopped = True
        task = session.task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.sessions.pop(key, None)
        if session.lobby_message:
            with contextlib.suppress(discord.HTTPException):
                await session.lobby_message.edit(
                    embed=discord.Embed(title="⏹️ تم إيقاف اللعبة", description="✅ تم إيقاف اللعبة.", color=discord.Color.red()),
                    view=None,
                )
        await message.reply("✅ تم إيقاف اللعبة.", mention_author=False)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip().lower()
        if content in {"-روليت", "-roulette"}:
            await self.start_game(message)
        elif content == "-توقيف":
            if (message.guild.id, message.channel.id) in self.sessions:
                await self.stop(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteMultiMessage(bot))
