from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from cogs.game_channels import is_group_game_channel_allowed
from database import connect


MAX_PLAYERS = 15
MIN_PLAYERS = 2
ROUNDS = 3


@dataclass
class DiceSession:
    guild_id: int
    channel_id: int
    starter_id: int
    reward: int = 5
    max_players: int = MAX_PLAYERS
    players: list[int] = field(default_factory=list)
    teams: dict[int, int] = field(default_factory=dict)
    scores: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0})
    round_number: int = 0
    active: bool = False
    message: discord.Message | None = None
    blocked_next_round: set[int] = field(default_factory=set)


class DiceLobbyView(discord.ui.View):
    def __init__(self, game: "DiceUpgrade", session: DiceSession):
        super().__init__(timeout=None)
        self.game = game
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        current = self.game.sessions.get(self.game.key(self.session))
        if current is not self.session:
            await interaction.response.send_message("❌ هاد اللوبي سالا.", ephemeral=True)
            return False
        if self.session.active:
            await interaction.response.send_message("❌ اللعبة بدات.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎲")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما كيدخلوش.", ephemeral=True)
        if interaction.user.id in self.session.players:
            return await interaction.response.send_message("⚠️ راك داخل أصلاً.", ephemeral=True)
        if len(self.session.players) >= self.session.max_players:
            return await interaction.response.send_message("❌ اللعبة عامرة.", ephemeral=True)
        self.session.players.append(interaction.user.id)
        await interaction.response.edit_message(embed=self.game.lobby_embed(self.session), view=self)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.session.starter_id:
            return await interaction.response.send_message("❌ مشغل اللعبة ما يقدرش يخرج؛ استعمل `!انهاء`.", ephemeral=True)
        if interaction.user.id not in self.session.players:
            return await interaction.response.send_message("❌ ماشي داخل اللعبة.", ephemeral=True)
        self.session.players.remove(interaction.user.id)
        await interaction.response.edit_message(embed=self.game.lobby_embed(self.session), view=self)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="▶️")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.session.starter_id and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ غير مشغل اللعبة أو الإدارة.", ephemeral=True)
        if len(self.session.players) < MIN_PLAYERS:
            return await interaction.response.send_message("❌ خاص على الأقل جوج لاعبين.", ephemeral=True)
        await interaction.response.defer()
        await self.game.start_match(self.session, interaction.channel, interaction.message)

    @discord.ui.button(label="الحالة", style=discord.ButtonStyle.secondary, emoji="📊")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=self.game.lobby_embed(self.session), ephemeral=True)


class DiceUpgrade(commands.Cog):
    """Team Dice inspired by Fizbo's documented two-team, three-round format."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], DiceSession] = {}
        self._patched = False
        self.original_prefix_start = None
        self.original_prefix_join = None
        self.original_prefix_leave = None
        self.original_prefix_spin = None
        self.original_prefix_end = None

    @staticmethod
    def key(session: DiceSession) -> tuple[int, int]:
        return session.guild_id, session.channel_id

    @staticmethod
    def add_points(guild_id: int, user_id: int, amount: int) -> None:
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)",
                (guild_id, user_id),
            )
            con.execute(
                "UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )

    def lobby_embed(self, session: DiceSession) -> discord.Embed:
        lines = "\n".join(f"**{i + 1:02d}.** <@{uid}>" for i, uid in enumerate(session.players)) or "—"
        embed = discord.Embed(
            title="🎲 Nawaf Dice — Team Battle",
            description=(
                "جوج فرق كيتنافسو فـ **3 جولات**. كل لاعب كيرمي النرد، والنتيجة كتدخل فمجموع الفريق.\n"
                "فالرمية الثانية ممكن يبان تأثير خاص بحال **×2** أو تعديل فالنقاط أو منع لاعب من الجولة الجاية."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="👥 اللاعبين", value=f"**{len(session.players)}/{session.max_players}**", inline=True)
        embed.add_field(name="⭐ جائزة الفريق الفائز", value=f"**{session.reward} نقطة لكل لاعب**", inline=True)
        embed.add_field(name="🔄 الجولات", value=f"**{ROUNDS}**", inline=True)
        embed.add_field(name="🎟️ المشاركون", value=lines, inline=False)
        embed.set_footer(text="دخول: !دخول • خروج: !خروج • بدء: !ابدأ • إنهاء: !انهاء")
        return embed

    def teams_embed(self, session: DiceSession, title: str = "🎲 توزيع الفرق") -> discord.Embed:
        team_a = [uid for uid in session.players if session.teams.get(uid) == 1]
        team_b = [uid for uid in session.players if session.teams.get(uid) == 2]
        a = "\n".join(f"<@{uid}>" for uid in team_a) or "—"
        b = "\n".join(f"<@{uid}>" for uid in team_b) or "—"
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.add_field(name="🔵 الفريق 1", value=a, inline=True)
        embed.add_field(name="🔴 الفريق 2", value=b, inline=True)
        embed.add_field(name="📊 المجموع", value=f"🔵 **{session.scores[1]}**  |  🔴 **{session.scores[2]}**", inline=False)
        return embed

    def assign_teams(self, session: DiceSession) -> None:
        shuffled = list(session.players)
        random.shuffle(shuffled)
        session.teams.clear()
        for index, uid in enumerate(shuffled):
            session.teams[uid] = 1 if index % 2 == 0 else 2
        session.scores = {1: 0, 2: 0}

    @staticmethod
    def effect_for_roll() -> tuple[str, int, str | None]:
        effects = [
            ("x2", 2, None),
            ("plus2", 2, None),
            ("minus2", -2, None),
            ("minus4", -4, None),
            ("none", 0, None),
            ("extra", 0, None),
            ("block", 0, "block"),
        ]
        return random.choice(effects)

    def format_effect(self, effect: str, value: int, target: int | None = None) -> str:
        if effect == "x2":
            return "✨ **×2** — تضاعفات نتيجة الرمية الأولى."
        if effect == "plus2":
            return "➕ **+2** — تزادو جوج نقاط."
        if effect == "minus2":
            return "➖ **-2** — تنقصو جوج نقاط."
        if effect == "minus4":
            return "💥 **-4** — تنقصو 4 نقاط."
        if effect == "extra":
            return "🎯 **رمية إضافية** — رمية عادية إضافية."
        if effect == "block" and target is not None:
            return f"🚫 **منع الجولة الجاية** — <@{target}> ما غاديش يشارك فالجولة الموالية."
        return "Ø **بدون تأثير**."

    async def start(self, message: discord.Message, args: list[str]) -> None:
        if not message.guild or not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply("❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", mention_author=False)
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await message.reply("❌ غير الإدارة تقدر تشغل معركة النرد.", mention_author=False)
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاينة لعبة جماعية مفتوحة فهاد الروم.", mention_author=False)

        reward = 5
        maximum = MAX_PLAYERS
        if args and args[0].isdigit():
            reward = max(0, min(int(args[0]), 1000))
        if len(args) > 1 and args[1].isdigit():
            maximum = max(MIN_PLAYERS, min(int(args[1]), MAX_PLAYERS))

        session = DiceSession(message.guild.id, message.channel.id, message.author.id, reward, maximum, [message.author.id])
        self.sessions[key] = session
        msg = await message.channel.send(embed=self.lobby_embed(session), view=DiceLobbyView(self, session))
        session.message = msg

    async def join(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await message.reply("❌ ما كايناش معركة نرد مفتوحة هنا.", mention_author=False)
        if session.active:
            return await message.reply("❌ اللعبة بدات.", mention_author=False)
        if message.author.id in session.players:
            return await message.reply("⚠️ راك داخل أصلاً.", mention_author=False)
        if len(session.players) >= session.max_players:
            return await message.reply("❌ اللعبة عامرة.", mention_author=False)
        session.players.append(message.author.id)
        await session.message.edit(embed=self.lobby_embed(session), view=DiceLobbyView(self, session))
        await message.reply(f"✅ دخل {message.author.mention} للنرد. **{len(session.players)}/{session.max_players}**", mention_author=False)

    async def leave(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session or session.active or message.author.id not in session.players:
            return await message.reply("❌ ما نتايش داخل لوبي النرد هنا.", mention_author=False)
        if message.author.id == session.starter_id:
            return await message.reply("❌ مشغل اللعبة ما يقدرش يخرج؛ استعمل `!انهاء`.", mention_author=False)
        session.players.remove(message.author.id)
        await session.message.edit(embed=self.lobby_embed(session), view=DiceLobbyView(self, session))
        await message.reply(f"✅ خرج {message.author.mention} من اللعبة.", mention_author=False)

    async def end(self, message: discord.Message):
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await message.reply("❌ غير الإدارة تقدر تسالي اللعبة.", mention_author=False)
        session = self.sessions.pop((message.guild.id, message.channel.id), None)
        if not session:
            return await message.reply("❌ ما كايناش لعبة جماعية مفتوحة هنا.", mention_author=False)
        session.active = False
        await message.reply("🛑 تم إغلاق معركة النرد.", mention_author=False)

    async def spin(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await message.reply("❌ ما كايناش معركة نرد مفتوحة هنا.", mention_author=False)
        if message.author.id != session.starter_id and not message.author.guild_permissions.manage_guild:
            return await message.reply("❌ غير مشغل اللعبة أو الإدارة.", mention_author=False)
        if len(session.players) < MIN_PLAYERS:
            return await message.reply("❌ خاص على الأقل جوج لاعبين.", mention_author=False)
        if session.active:
            return await message.reply("⚠️ اللعبة خدامة دابا.", mention_author=False)
        await self.start_match(session, message.channel, session.message)

    async def start_match(self, session: DiceSession, channel: discord.abc.Messageable, message: discord.Message | None):
        if session.active:
            return
        session.active = True
        self.assign_teams(session)
        board = message or await channel.send(embed=self.teams_embed(session))
        session.message = board

        try:
            await board.edit(embed=self.teams_embed(session, "🎲 الفرق واجدين!"), view=None)
            await asyncio.sleep(1.2)

            for round_no in range(1, ROUNDS + 1):
                session.round_number = round_no
                active_players = [uid for uid in session.players if uid not in session.blocked_next_round]
                session.blocked_next_round.clear()

                round_lines: list[str] = []
                for uid in active_players:
                    first = random.randint(1, 6)
                    effect, modifier, special = self.effect_for_roll()
                    added = first
                    target = None

                    if special == "block":
                        opponents = [p for p in session.players if session.teams[p] != session.teams[uid] and p in active_players]
                        if opponents:
                            target = random.choice(opponents)
                            session.blocked_next_round.add(target)
                    elif effect == "x2":
                        added = first * 2
                    elif effect == "plus2":
                        added = first + 2
                    elif effect == "minus2":
                        added = first - 2
                    elif effect == "minus4":
                        added = first - 4
                    elif effect == "extra":
                        extra = random.randint(1, 6)
                        added = first + extra
                        modifier = extra

                    team = session.teams[uid]
                    session.scores[team] += added
                    effect_text = self.format_effect(effect, modifier, target)
                    extra_text = f" • رمية ثانية **{modifier}**" if effect == "extra" else ""
                    round_lines.append(
                        f"<@{uid}> **{first}** → {added} نقطة{extra_text}\n{effect_text}"
                    )

                    embed = discord.Embed(
                        title=f"🎲 الجولة {round_no}/{ROUNDS}",
                        description="\n\n".join(round_lines[-6:]),
                        color=discord.Color.gold(),
                    )
                    embed.add_field(name="🔵 الفريق 1", value=f"**{session.scores[1]}** نقطة", inline=True)
                    embed.add_field(name="🔴 الفريق 2", value=f"**{session.scores[2]}** نقطة", inline=True)
                    await board.edit(embed=embed, view=None)
                    await asyncio.sleep(0.55)

                blocked_text = ""
                if session.blocked_next_round and round_no < ROUNDS:
                    blocked_text = "\n🚫 الجولة الجاية خارجين مؤقتاً: " + ", ".join(f"<@{u}>" for u in session.blocked_next_round)
                summary = discord.Embed(
                    title=f"📊 نهاية الجولة {round_no}",
                    description=(
                        f"🔵 الفريق 1: **{session.scores[1]}**\n"
                        f"🔴 الفريق 2: **{session.scores[2]}**"
                        f"{blocked_text}"
                    ),
                    color=discord.Color.blurple(),
                )
                await board.edit(embed=summary, view=None)
                await asyncio.sleep(1.4)

            if session.scores[1] == session.scores[2]:
                winner_team = random.choice((1, 2))
                tie = True
            else:
                winner_team = 1 if session.scores[1] > session.scores[2] else 2
                tie = False

            winner_players = [uid for uid in session.players if session.teams[uid] == winner_team]
            for uid in winner_players:
                self.add_points(session.guild_id, uid, session.reward)

            final = discord.Embed(
                title="🏆 معركة النرد انتهات",
                description=(
                    f"🔵 الفريق 1: **{session.scores[1]} نقطة**\n"
                    f"🔴 الفريق 2: **{session.scores[2]} نقطة**\n\n"
                    f"🏆 الفائز: **الفريق {winner_team}**\n"
                    + ("🤝 تعادل، وتم اختيار الفريق الفائز عشوائياً بين المتعادلين.\n\n" if tie else "\n")
                    + "⭐ كل لاعب فالفريق الفائز ربح " + f"**{session.reward} نقطة**."
                ),
                color=discord.Color.green(),
            )
            final.add_field(name="👥 لاعبو الفريق الفائز", value="\n".join(f"<@{uid}>" for uid in winner_players), inline=False)
            final.set_footer(text="يمكن تشغيل مباراة جديدة فهاد الروم")
            await board.edit(embed=final, view=None)
        finally:
            session.active = False
            self.sessions.pop(self.key(session), None)

    def patch_games(self):
        games = self.bot.get_cog("Games")
        if not games or self._patched:
            return
        self.original_prefix_start = games._prefix_start
        self.original_prefix_join = games._prefix_join
        self.original_prefix_leave = games._prefix_leave
        self.original_prefix_spin = games._prefix_spin
        self.original_prefix_end = games._prefix_end

        async def prefix_start(message, game_type, args):
            if game_type == "dice_battle":
                return await self.start(message, args)
            return await self.original_prefix_start(message, game_type, args)

        async def prefix_join(message):
            if (message.guild.id, message.channel.id) in self.sessions:
                return await self.join(message)
            return await self.original_prefix_join(message)

        async def prefix_leave(message):
            if (message.guild.id, message.channel.id) in self.sessions:
                return await self.leave(message)
            return await self.original_prefix_leave(message)

        async def prefix_spin(message):
            if (message.guild.id, message.channel.id) in self.sessions:
                return await self.spin(message)
            return await self.original_prefix_spin(message)

        async def prefix_end(message):
            if (message.guild.id, message.channel.id) in self.sessions:
                return await self.end(message)
            return await self.original_prefix_end(message)

        games._prefix_start = prefix_start
        games._prefix_join = prefix_join
        games._prefix_leave = prefix_leave
        games._prefix_spin = prefix_spin
        games._prefix_end = prefix_end
        self._patched = True

    @app_commands.command(name="dice-start", description="تشغيل معركة نرد من 3 جولات بفريقين")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dice_start(self, interaction: discord.Interaction, reward: app_commands.Range[int, 0, 1000] = 5, max_players: app_commands.Range[int, 2, 15] = 15):
        if not interaction.guild or not is_group_game_channel_allowed(interaction.guild.id, interaction.channel.id):
            return await interaction.response.send_message("❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", ephemeral=True)
        key = (interaction.guild.id, interaction.channel.id)
        if key in self.sessions:
            return await interaction.response.send_message("❌ كاينة لعبة مفتوحة فهاد الروم.", ephemeral=True)
        session = DiceSession(interaction.guild.id, interaction.channel.id, interaction.user.id, reward, max_players, [interaction.user.id])
        self.sessions[key] = session
        await interaction.response.send_message(embed=self.lobby_embed(session), view=DiceLobbyView(self, session))
        session.message = await interaction.original_response()

    async def cog_load(self):
        self.patch_games()


async def setup(bot: commands.Bot):
    await bot.add_cog(DiceUpgrade(bot))
