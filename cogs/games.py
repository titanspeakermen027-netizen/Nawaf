from __future__ import annotations

import asyncio
import contextlib
import random
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from cogs.access_control import can_manage_events
from cogs.game_channels import is_group_game_channel_allowed
from cogs.points import add_category_points

MIN_PLAYERS = 2
DEFAULT_MAX_PLAYERS = 12
LOBBY_SECONDS = 30


@dataclass
class GameSession:
    guild_id: int
    channel_id: int
    starter_id: int
    game_type: str
    reward: int = 5
    max_players: int = DEFAULT_MAX_PLAYERS
    players: list[int] = field(default_factory=list)
    message_id: int | None = None
    active: bool = False
    stop_requested: bool = False
    task: asyncio.Task | None = None
    lobby_message: discord.Message | None = None


class DiceLobbyView(discord.ui.View):
    def __init__(self, games: "Games", key: tuple[int, int]):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.games = games
        self.key = key

    def session(self) -> GameSession | None:
        return self.games.sessions.get(self.key)

    async def refresh(self, interaction: discord.Interaction) -> None:
        session = self.session()
        if not session:
            return await interaction.response.send_message("❌ اللعبة انتهت.", ephemeral=True)
        await interaction.response.edit_message(embed=self.games.lobby_embed(session), view=self)

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session()
        if not session or session.active:
            return await interaction.response.send_message("❌ اللعبة بدات بالفعل.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما يقدروش يدخلو.", ephemeral=True)
        if interaction.user.id in session.players:
            return await interaction.response.send_message("❌ أنت أصلا مشارك بالفعالية", ephemeral=True)
        if len(session.players) >= session.max_players:
            return await interaction.response.send_message(f"❌ اللعبة عامرة. الحد الأقصى **{session.max_players}** لاعب.", ephemeral=True)
        session.players.append(interaction.user.id)
        await self.refresh(interaction)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session()
        if not session or session.active:
            return await interaction.response.send_message("❌ اللعبة بدات بالفعل.", ephemeral=True)
        if interaction.user.id not in session.players:
            return await interaction.response.send_message("❌ أنت ماشي مشارك.", ephemeral=True)
        if interaction.user.id == session.starter_id:
            return await interaction.response.send_message("❌ مشغل الفعالية ما يقدرش يخرج. استعمل `-توقيف`.", ephemeral=True)
        session.players.remove(interaction.user.id)
        await self.refresh(interaction)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="🎲")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session()
        if not session:
            return await interaction.response.send_message("❌ اللعبة سالات.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يبدأ اللعبة.", ephemeral=True)
        if len(session.players) < MIN_PLAYERS:
            return await interaction.response.send_message(f"❌ خاص على الأقل **{MIN_PLAYERS} لاعبين**.", ephemeral=True)
        if session.active:
            return await interaction.response.send_message("⚠️ اللعبة خدامة دابا.", ephemeral=True)

        countdown_task = session.task
        if countdown_task and not countdown_task.done() and countdown_task is not asyncio.current_task():
            countdown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await countdown_task
        session.active = True
        self.stop()
        await interaction.response.defer()
        session.task = asyncio.create_task(self.games.run_dice(interaction.channel, session, interaction.message))


class Games(commands.Cog):
    """Group games other than roulette. Roulette is owned by RouletteMultiMessage."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], GameSession] = {}

    def add_points(self, guild_id: int, user_id: int, amount: int) -> None:
        add_category_points(guild_id, user_id, amount, "group")

    def player_lines(self, session: GameSession, page: int = 0, per_page: int = 8) -> tuple[str, int]:
        pages = max(1, (len(session.players) + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        start = page * per_page
        chunk = session.players[start:start + per_page]
        if not chunk:
            return "—", pages
        guild = self.bot.get_guild(session.guild_id)
        lines = []
        for index, user_id in enumerate(chunk, start=start + 1):
            member = guild.get_member(user_id) if guild else None
            name = member.name if member else f"عضو {user_id}"
            lines.append(f"**{index}.** {name} • <@{user_id}>")
        return "\n".join(lines), pages

    def lobby_embed(self, session: GameSession, page: int = 0, remaining: int | None = None) -> discord.Embed:
        lines, pages = self.player_lines(session, page)
        timer = LOBBY_SECONDS if remaining is None else remaining
        embed = discord.Embed(
            title="🎲 معركة النرد",
            description="ادخل عبر الزر. من بعد البداية كل لاعب كيرمي النرد، وصاحب أعلى نتيجة كيربح.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="👥 المشاركون", value=f"**{len(session.players)} / {session.max_players}**", inline=True)
        embed.add_field(name="⏳ الوقت", value=f"**{max(0, timer)} ثانية**", inline=True)
        embed.add_field(name="⭐ الجائزة", value=f"**{session.reward} نقطة**", inline=True)
        embed.add_field(name="📋 الأسماء", value=lines, inline=False)
        embed.set_footer(text=f"صفحة {page + 1}/{pages} • ينتهي اللوبي بعد {max(0, timer)} ثانية")
        return embed

    async def start_lobby(self, message: discord.Message, game_type: str = "dice") -> None:
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

        session = GameSession(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            starter_id=message.author.id,
            game_type=game_type,
            reward=5,
            max_players=DEFAULT_MAX_PLAYERS,
            players=[message.author.id],
        )
        self.sessions[key] = session
        view = DiceLobbyView(self, key)
        lobby = await message.channel.send(embed=self.lobby_embed(session), view=view)
        session.message_id = lobby.id
        session.lobby_message = lobby
        session.task = asyncio.create_task(self.lobby_countdown(session, lobby, view))

    async def lobby_countdown(self, session: GameSession, lobby: discord.Message, view: DiceLobbyView) -> None:
        key = (session.guild_id, session.channel_id)
        try:
            for remaining in range(LOBBY_SECONDS, -1, -1):
                if session.stop_requested or session.active or key not in self.sessions:
                    return
                if remaining in {30, 25, 20, 15, 10, 5, 4, 3, 2, 1, 0}:
                    with contextlib.suppress(discord.HTTPException):
                        await lobby.edit(embed=self.lobby_embed(session, remaining=remaining), view=view)
                await asyncio.sleep(1)
            if session.stop_requested or session.active:
                return
            if len(session.players) < MIN_PLAYERS:
                await lobby.edit(embed=discord.Embed(title="❌ لم تبدأ الفعالية", description=f"انتهى الوقت ولم يصل العدد إلى **{MIN_PLAYERS}** لاعبين.", color=discord.Color.red()), view=None)
                return
            session.active = True
            view.stop()
            session.task = asyncio.create_task(self.run_dice(lobby.channel, session, lobby))
            await session.task
        except asyncio.CancelledError:
            return
        finally:
            current = self.sessions.get(key)
            if current is session and (session.task is None or session.task.done()):
                self.sessions.pop(key, None)

    async def run_dice(self, channel: discord.abc.Messageable, session: GameSession, lobby: discord.Message) -> None:
        session.active = True
        results = {user_id: random.randint(1, 6) for user_id in list(session.players)}
        if not results or session.stop_requested:
            return
        best = max(results.values())
        winners = [uid for uid, value in results.items() if value == best]
        winner_id = random.choice(winners)
        guild = self.bot.get_guild(session.guild_id)
        lines = []
        for user_id, value in results.items():
            member = guild.get_member(user_id) if guild else None
            name = member.name if member else f"عضو {user_id}"
            lines.append(f"**{name}** — 🎲 **{value}**")
        self.add_points(session.guild_id, winner_id, session.reward)
        embed = discord.Embed(title="🏆 معركة النرد — انتهت", description="\n".join(lines), color=discord.Color.green())
        embed.add_field(name="الفائز", value=f"<@{winner_id}>", inline=True)
        embed.add_field(name="الجائزة", value=f"**+{session.reward} نقطة**", inline=True)
        embed.set_footer(text="يمكنكم بدء فعالية جديدة")
        await lobby.edit(embed=embed, view=None)

    async def stop(self, message: discord.Message) -> bool:
        if not isinstance(message.author, discord.Member) or not can_manage_events(message.author):
            await message.reply("❌ غير الإدارة أو رئيس الفعاليات يقدر يوقف الفعاليات.", mention_author=False)
            return True
        key = (message.guild.id, message.channel.id)
        session = self.sessions.get(key)
        if not session:
            return False
        session.stop_requested = True
        task = session.task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.sessions.pop(key, None)
        if session.lobby_message:
            with contextlib.suppress(discord.HTTPException):
                await session.lobby_message.edit(embed=discord.Embed(title="⏹️ تم إيقاف اللعبة", description="✅ تم إيقاف اللعبة.", color=discord.Color.red()), view=None)
        await message.reply("✅ تم إيقاف اللعبة.", mention_author=False)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip().lower()
        if content in {"-نرد", "-dice", "-معركة النرد"}:
            await self.start_lobby(message)
        elif content == "-توقيف" and (message.guild.id, message.channel.id) in self.sessions:
            await self.stop(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
