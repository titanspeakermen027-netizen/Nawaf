import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from database import connect


GROUP_GAMES = {
    "roulette": {"name": "الروليت", "min": 2, "max": 15, "reward": 5},
    "dice_battle": {"name": "معركة النرد", "min": 2, "max": 15, "reward": 5},
}

ROULETTE_EMOJIS = ("🔴", "⚫", "🔴", "⚫", "🟢")


@dataclass
class GameSession:
    guild_id: int
    channel_id: int
    starter_id: int
    game_type: str
    reward: int
    min_players: int
    max_players: int
    players: list[int] = field(default_factory=list)
    seat_map: dict[int, int] = field(default_factory=dict)
    active: bool = False
    message_id: int | None = None
    round_number: int = 0


class GameLobbyView(discord.ui.View):
    def __init__(self, games: "Games", guild_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.games = games
        self.key = (guild_id, channel_id)

    def _session(self) -> GameSession | None:
        return self.games.sessions.get(self.key)

    async def refresh(self, interaction: discord.Interaction, session: GameSession):
        embed = self.games.build_lobby_embed(session)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self._session()
        if not session or session.active:
            return await interaction.response.send_message(
                "❌ اللوبي تسالى وبدات الجولة بالفعل.", ephemeral=True
            )
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما يقدروش يدخلوا.", ephemeral=True)
        if interaction.user.id in session.players:
            return await interaction.response.send_message("⚠️ راك داخل اللعبة أصلاً.", ephemeral=True)
        if len(session.players) >= session.max_players:
            return await interaction.response.send_message(
                f"❌ اللعبة عامرة — الحد الأقصى **{session.max_players}** لاعب.", ephemeral=True
            )

        session.players.append(interaction.user.id)
        self.games.assign_seats(session)
        await self.refresh(interaction, session)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self._session()
        if not session or session.active:
            return await interaction.response.send_message("❌ اللعبة بدات بالفعل.", ephemeral=True)
        if interaction.user.id not in session.players:
            return await interaction.response.send_message("❌ راك ماشي داخل اللعبة.", ephemeral=True)
        if interaction.user.id == session.starter_id:
            return await interaction.response.send_message(
                "❌ مشغل اللعبة ما يقدرش يخرج. استعمل `!انهاء`.", ephemeral=True
            )

        session.players.remove(interaction.user.id)
        self.games.assign_seats(session)
        await self.refresh(interaction, session)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="🎰", row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self._session()
        if not session:
            return await interaction.response.send_message("❌ اللعبة سالات.", ephemeral=True)
        if interaction.user.id != session.starter_id and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "❌ غير مشغل اللعبة أو الإدارة يقدر يبدأ الروليت.", ephemeral=True
            )
        if session.active:
            return await interaction.response.send_message("⚠️ الجولة خدامة دابا.", ephemeral=True)
        if len(session.players) < session.min_players:
            return await interaction.response.send_message(
                f"❌ خاص على الأقل **{session.min_players} لاعبين**.", ephemeral=True
            )

        await interaction.response.defer()
        await self.games.run_roulette(interaction.channel, session, existing_message=interaction.message)

    @discord.ui.button(label="الحالة", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self._session()
        if not session:
            return await interaction.response.send_message("❌ ما بقاتش لعبة هنا.", ephemeral=True)
        await interaction.response.send_message(embed=self.games.build_lobby_embed(session), ephemeral=True)


class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], GameSession] = {}

    async def _reply(self, message: discord.Message, content: str, **kwargs):
        return await message.reply(content, mention_author=False, **kwargs)

    def add_points(self, guild_id: int, user_id: int, amount: int):
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)",
                (guild_id, user_id),
            )
            con.execute(
                "UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )

    def assign_seats(self, session: GameSession):
        """Assign stable-looking roulette seats, reshuffling only when the lobby changes."""
        seats = list(range(1, len(session.players) + 1))
        session.seat_map = dict(zip(session.players, seats))

    def player_lines(self, session: GameSession) -> str:
        if not session.players:
            return "—"
        lines = []
        for user_id in session.players:
            seat = session.seat_map.get(user_id, "?")
            lines.append(f"**{seat:02d}** ・ <@{user_id}>")
        return "\n".join(lines)

    def build_lobby_embed(self, session: GameSession) -> discord.Embed:
        spec = GROUP_GAMES[session.game_type]
        if session.game_type == "roulette":
            title = "🎰 الروليت — Survival Roulette"
            description = (
                "كل لاعب عندو خانة. منين كيبدا الدور، كتدور الروليت وكيتم إقصاء لاعب واحد كل جولة. "
                "آخر لاعب باقي هو الفائز."
            )
        else:
            title = "🎲 معركة النرد"
            description = "جميع اللاعبين كيرميو النرد، وأعلى نتيجة كتفوز بالجولة."

        color = discord.Color.red() if session.game_type == "roulette" else discord.Color.blurple()
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(
            name="👥 اللاعبين",
            value=f"**{len(session.players)} / {session.max_players}**",
            inline=True,
        )
        embed.add_field(name="📌 الحد الأدنى", value=f"**{session.min_players}**", inline=True)
        embed.add_field(name="⭐ الجائزة", value=f"**{session.reward} نقطة**", inline=True)
        embed.add_field(name="🎟️ الخانات", value=self.player_lines(session), inline=False)
        embed.set_footer(text="دخول وخروج بالأزرار • البدء لمشغل اللعبة أو الإدارة فقط")
        return embed

    def start_session(
        self,
        guild: discord.Guild,
        channel_id: int,
        starter_id: int,
        game_type: str,
        reward: int = 5,
        max_players: int = 15,
    ):
        key = (guild.id, channel_id)
        if key in self.sessions:
            return None, "❌ كاينة لعبة جماعية مفتوحة فهاد الروم."

        spec = GROUP_GAMES[game_type]
        maximum = max(spec["min"], min(max_players, spec["max"], 15))
        safe_reward = max(0, min(reward, 1000))
        session = GameSession(
            guild_id=guild.id,
            channel_id=channel_id,
            starter_id=starter_id,
            game_type=game_type,
            reward=safe_reward,
            min_players=spec["min"],
            max_players=maximum,
            players=[starter_id],
        )
        self.assign_seats(session)
        self.sessions[key] = session
        return session, None

    async def send_lobby(self, channel: discord.abc.Messageable, session: GameSession):
        view = GameLobbyView(self, session.guild_id, session.channel_id)
        message = await channel.send(embed=self.build_lobby_embed(session), view=view)
        session.message_id = message.id
        return message

    async def animate_roulette(
        self,
        message: discord.Message,
        session: GameSession,
        current_user_id: int | None = None,
        final: bool = False,
    ):
        if final:
            embed = discord.Embed(
                title="🎰 الروليت — النتيجة",
                description="✅ الجولة سالات.",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="🏆 الفائز",
                value=f"<@{session.players[0]}>\n⭐ ربح **{session.reward} نقطة**",
                inline=False,
            )
            await message.edit(embed=embed, view=None)
            return

        target_name = f"<@{current_user_id}>" if current_user_id else "جاري الاختيار..."
        round_text = max(1, session.round_number)
        embed = discord.Embed(
            title="🎰 الروليت كتدور...",
            description=(
                f"**الجولة {round_text}**\n\n"
                f"{random.choice(ROULETTE_EMOJIS)} {random.choice(ROULETTE_EMOJIS)} {random.choice(ROULETTE_EMOJIS)} "
                f"{random.choice(ROULETTE_EMOJIS)} {random.choice(ROULETTE_EMOJIS)}\n\n"
                f"🎯 الخانة الحالية: **{target_name}**"
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{len(session.players)} لاعبين باقيين")
        await message.edit(embed=embed, view=None)

    async def run_roulette(
        self,
        channel: discord.abc.Messageable,
        session: GameSession,
        existing_message: discord.Message | None = None,
    ):
        if session.active:
            return
        session.active = True
        session.round_number = 0
        session.players = list(dict.fromkeys(session.players))
        self.assign_seats(session)

        message = existing_message
        if message is None:
            if session.message_id and hasattr(channel, "fetch_message"):
                try:
                    message = await channel.fetch_message(session.message_id)
                except discord.HTTPException:
                    message = None
        if message is None:
            message = await channel.send("🎰 جاري تشغيل الروليت...")

        try:
            while len(session.players) > 1:
                session.round_number += 1

                # Brief rolling animation without spamming the channel.
                for tick in range(4):
                    candidate = random.choice(session.players)
                    await self.animate_roulette(message, session, candidate)
                    await asyncio.sleep(0.45 + tick * 0.08)

                eliminated = random.choice(session.players)
                seat = session.seat_map.get(eliminated, "?")
                session.players.remove(eliminated)
                self.assign_seats(session)

                result_embed = discord.Embed(
                    title="🎰 الروليت — إقصاء",
                    description=(
                        f"💥 الروليت وقفات على الخانة **{seat:02d}**\n\n"
                        f"❌ تم إقصاء <@{eliminated}>\n"
                        f"👥 المتبقون: **{len(session.players)}**"
                    ),
                    color=discord.Color.red(),
                )
                result_embed.add_field(name="📋 الجولة", value=f"**{session.round_number}**", inline=True)
                result_embed.add_field(name="🎯 الخانة", value=f"**{seat:02d}**", inline=True)
                result_embed.set_footer(text="الجولة التالية بعد لحظات...")
                await message.edit(embed=result_embed, view=None)
                await asyncio.sleep(1.25)

                if len(session.players) > 1:
                    await message.edit(embed=self.build_active_embed(session), view=None)
                    await asyncio.sleep(0.65)

            winner_id = session.players[0]
            self.add_points(session.guild_id, winner_id, session.reward)

            final_embed = discord.Embed(
                title="🏆 الروليت — انتهت اللعبة",
                description="آخر لاعب باقي هو الفائز.",
                color=discord.Color.green(),
            )
            final_embed.add_field(name="🏆 الفائز", value=f"<@{winner_id}>", inline=False)
            final_embed.add_field(name="⭐ الجائزة", value=f"**{session.reward} نقطة**", inline=True)
            final_embed.add_field(name="🔄 عدد الجولات", value=f"**{session.round_number}**", inline=True)
            final_embed.add_field(name="👥 عدد المشاركين", value=f"**{len(session.seat_map)}**", inline=True)
            final_embed.set_footer(text="يمكن تشغيل لعبة جديدة في نفس الروم")
            await message.edit(embed=final_embed, view=None)
        finally:
            self.sessions.pop((session.guild_id, session.channel_id), None)

    def build_active_embed(self, session: GameSession) -> discord.Embed:
        embed = discord.Embed(
            title="🎰 الروليت — اللعبة مستمرة",
            description="الجولة القادمة غادي تبدأ دابا...",
            color=discord.Color.orange(),
        )
        embed.add_field(name="🔄 الجولة", value=f"**{session.round_number}**", inline=True)
        embed.add_field(name="👥 المتبقون", value=f"**{len(session.players)}**", inline=True)
        embed.add_field(name="⭐ جائزة الفائز", value=f"**{session.reward} نقطة**", inline=True)
        embed.add_field(name="🎟️ اللاعبين", value=self.player_lines(session), inline=False)
        return embed

    async def finish_dice(self, channel: discord.abc.Messageable, session: GameSession):
        rolls = {uid: random.randint(1, 6) for uid in session.players}
        best = max(rolls.values())
        winners = [uid for uid, value in rolls.items() if value == best]
        winner_id = random.choice(winners)
        result = "🎲 " + " | ".join(f"<@{uid}>: **{value}**" for uid, value in rolls.items())
        if len(winners) > 1:
            result += f"\n🤝 تعادل، وتم اختيار <@{winner_id}> من المتعادلين."
        self.add_points(session.guild_id, winner_id, session.reward)
        self.sessions.pop((session.guild_id, session.channel_id), None)
        return f"{result}\n\n🏆 الفائز: <@{winner_id}>\n⭐ ربح **{session.reward} نقطة**."

    @app_commands.command(name="game-start", description="تشغيل لعبة جماعية للأعضاء")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(
        game=[
            app_commands.Choice(name="الروليت", value="roulette"),
            app_commands.Choice(name="معركة النرد", value="dice_battle"),
        ]
    )
    async def game_start(
        self,
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        reward: app_commands.Range[int, 0, 1000] = 5,
        max_players: app_commands.Range[int, 2, 15] = 15,
    ):
        session, error = self.start_session(
            interaction.guild,
            interaction.channel.id,
            interaction.user.id,
            game.value,
            reward,
            max_players,
        )
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.send_message("🎮 تم إنشاء لوبي اللعبة.")
        await self.send_lobby(interaction.channel, session)

    @app_commands.command(name="game-join", description="الدخول في اللعبة الجماعية الحالية")
    async def game_join(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.channel.id)
        session = self.sessions.get(key)
        if not session:
            return await interaction.response.send_message("❌ ما كايناش لعبة جماعية مفتوحة هنا.", ephemeral=True)
        if session.active:
            return await interaction.response.send_message("❌ الجولة بدات، ما يمكنش تدخل دابا.", ephemeral=True)
        if interaction.user.id in session.players:
            return await interaction.response.send_message("⚠️ أنت داخل اللعبة أصلاً.", ephemeral=True)
        if len(session.players) >= session.max_players:
            return await interaction.response.send_message(
                f"❌ اللعبة عامرة. الحد الأقصى هو **{session.max_players}** لاعب.", ephemeral=True
            )
        session.players.append(interaction.user.id)
        self.assign_seats(session)
        await interaction.response.send_message(f"✅ دخل {interaction.user.mention} للعبة. **{len(session.players)}/{session.max_players}**")

    @app_commands.command(name="game-leave", description="الخروج من اللعبة الجماعية الحالية")
    async def game_leave(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.channel.id)
        session = self.sessions.get(key)
        if not session or session.active or interaction.user.id not in session.players:
            return await interaction.response.send_message("❌ ما نتايش داخل لوبـي مفتوح هنا.", ephemeral=True)
        if interaction.user.id == session.starter_id:
            return await interaction.response.send_message("❌ مشغل اللعبة ما يقدرش يخرج؛ استعمل `/game-end`.", ephemeral=True)
        session.players.remove(interaction.user.id)
        self.assign_seats(session)
        await interaction.response.send_message(f"✅ خرج {interaction.user.mention} من اللعبة.")

    @app_commands.command(name="game-spin", description="بدء الجولة وتحديد الفائز")
    async def game_spin(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.channel.id)
        session = self.sessions.get(key)
        if not session:
            return await interaction.response.send_message("❌ ما كايناش لعبة جماعية مفتوحة هنا.", ephemeral=True)
        if interaction.user.id != session.starter_id and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ غير مشغل اللعبة أو الإدارة يقدر يبدأ الجولة.", ephemeral=True)
        if len(session.players) < session.min_players:
            return await interaction.response.send_message(
                f"❌ خاص على الأقل **{session.min_players} لاعبين** باش تبدأ اللعبة.", ephemeral=True
            )
        if session.active:
            return await interaction.response.send_message("⚠️ الجولة خدامة دابا.", ephemeral=True)
        if session.game_type == "roulette":
            await interaction.response.defer()
            await self.run_roulette(interaction.channel, session)
        else:
            await interaction.response.send_message(await self.finish_dice(interaction.channel, session))

    @app_commands.command(name="game-end", description="إغلاق اللعبة الجماعية الحالية")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def game_end(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.channel.id)
        session = self.sessions.pop(key, None)
        if not session:
            return await interaction.response.send_message("❌ ما كايناش لعبة جماعية مفتوحة هنا.", ephemeral=True)
        await interaction.response.send_message("🛑 تم إغلاق اللعبة الجماعية.")

    async def _prefix_start(self, message: discord.Message, game_type: str, args: list[str]):
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await self._reply(message, "❌ غير الإدارة تقدر تشغل الألعاب الجماعية.")
        reward = 5
        maximum = 15
        if args and args[0].isdigit():
            reward = max(0, min(int(args[0]), 1000))
        if len(args) > 1 and args[1].isdigit():
            maximum = max(2, min(int(args[1]), 15))

        session, error = self.start_session(
            message.guild,
            message.channel.id,
            message.author.id,
            game_type,
            reward,
            maximum,
        )
        if error:
            return await self._reply(message, error)
        await self.send_lobby(message.channel, session)

    async def _prefix_join(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await self._reply(message, "❌ ما كايناش لعبة جماعية مفتوحة هنا.")
        if session.active:
            return await self._reply(message, "❌ الجولة بدات، ما يمكنش تدخل دابا.")
        if message.author.id in session.players:
            return await self._reply(message, "⚠️ أنت داخل اللعبة أصلاً.")
        if len(session.players) >= session.max_players:
            return await self._reply(message, f"❌ اللعبة عامرة. الحد الأقصى هو {session.max_players} لاعب.")
        session.players.append(message.author.id)
        self.assign_seats(session)
        await self._reply(message, f"✅ دخل {message.author.mention} للعبة. **{len(session.players)}/{session.max_players}**")

    async def _prefix_leave(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session or session.active or message.author.id not in session.players:
            return await self._reply(message, "❌ ما نتايش داخل لوبي لعبة جماعية هنا.")
        if message.author.id == session.starter_id:
            return await self._reply(message, "❌ مشغل اللعبة ما يقدرش يخرج؛ استعمل `!انهاء`.")
        session.players.remove(message.author.id)
        self.assign_seats(session)
        await self._reply(message, f"✅ خرج {message.author.mention} من اللعبة.")

    async def _prefix_spin(self, message: discord.Message):
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await self._reply(message, "❌ ما كايناش لعبة جماعية مفتوحة هنا.")
        if session.active:
            return await self._reply(message, "⚠️ الجولة خدامة دابا.")
        if not isinstance(message.author, discord.Member) or (
            message.author.id != session.starter_id and not message.author.guild_permissions.manage_guild
        ):
            return await self._reply(message, "❌ غير مشغل اللعبة أو الإدارة يقدر يبدأ الجولة.")
        if len(session.players) < session.min_players:
            return await self._reply(message, f"❌ خاص على الأقل **{session.min_players} لاعبين** باش تبدأ اللعبة.")

        if session.game_type == "roulette":
            session.active = True
            await self._reply(message, "🎰 جاري تدوير الروليت...")
            # The new lobby/message remains the main game board; find it when possible.
            channel = message.channel
            board = None
            if session.message_id:
                try:
                    board = await channel.fetch_message(session.message_id)
                except discord.HTTPException:
                    board = None
            if board:
                session.active = False
                await self.run_roulette(channel, session, existing_message=board)
            else:
                session.active = False
                await self.run_roulette(channel, session)
        else:
            await self._reply(message, await self.finish_dice(message.channel, session))

    async def _prefix_end(self, message: discord.Message):
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await self._reply(message, "❌ غير الإدارة تقدر تسالي اللعبة.")
        if not self.sessions.pop((message.guild.id, message.channel.id), None):
            return await self._reply(message, "❌ ما كايناش لعبة جماعية مفتوحة هنا.")
        await self._reply(message, "🛑 تم إغلاق اللعبة الجماعية.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if content.startswith("!روليت"):
            await self._prefix_start(message, "roulette", content.split()[1:])
        elif content.startswith("!نرد"):
            await self._prefix_start(message, "dice_battle", content.split()[1:])
        elif content == "!دخول":
            await self._prefix_join(message)
        elif content == "!خروج":
            await self._prefix_leave(message)
        elif content in {"!ابدأ", "!بدء"}:
            await self._prefix_spin(message)
        elif content in {"!انهاء", "!إنهاء"}:
            await self._prefix_end(message)
        elif content == "!العاب":
            await self._reply(
                message,
                "🎮 `!روليت [النقاط] [الحد]`\n"
                "🎲 `!نرد [النقاط] [الحد]`\n"
                "👥 `!دخول` — `!خروج`\n"
                "▶️ `!ابدأ` — 🛑 `!انهاء`\n\n"
                "🎰 الروليت الآن Survival متعددة الجولات، من 2 حتى 15 لاعب.\n"
                "⭐ نقاط الفوز غير مرتبطة بالألعاب الفردية.",
            )

    @app_commands.command(name="games", description="عرض الألعاب الجماعية والفردية")
    async def games(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🎮 **الجماعية:** الروليت، معركة النرد — الإدارة تشغلها، من 2 حتى 15 لاعب.\n"
            "🎯 **الفردية:** /coinflip و /solo-dice و /rps — بدون نقاط."
        )

    @app_commands.command(name="coinflip", description="لعبة فردية: وجه أو كتابة")
    async def coinflip(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🪙 النتيجة: **{random.choice(('وجه', 'كتابة'))}**")

    @app_commands.command(name="solo-dice", description="لعبة فردية: رمية نرد")
    async def solo_dice(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🎲 رميتك: **{random.randint(1, 6)}**")

    @app_commands.command(name="rps", description="لعبة فردية: حجر ورق مقص")
    @app_commands.choices(
        choice=[
            app_commands.Choice(name="حجر", value="حجر"),
            app_commands.Choice(name="ورق", value="ورق"),
            app_commands.Choice(name="مقص", value="مقص"),
        ]
    )
    async def rps(self, interaction: discord.Interaction, choice: app_commands.Choice[str]):
        bot_choice = random.choice(("حجر", "ورق", "مقص"))
        if choice.value == bot_choice:
            result = "تعادل"
        elif (choice.value, bot_choice) in (("حجر", "مقص"), ("ورق", "حجر"), ("مقص", "ورق")):
            result = "فزت"
        else:
            result = "خسرت"
        await interaction.response.send_message(
            f"✊ اختيارك: **{choice.value}** | 🤖: **{bot_choice}**\n**{result}** — بدون نقاط."
        )

    @app_commands.command(name="points", description="عرض نقاطك أو نقاط عضو")
    async def points(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        with connect() as con:
            row = con.execute(
                "SELECT points FROM points WHERE guild_id=? AND user_id=?",
                (interaction.guild.id, member.id),
            ).fetchone()
        value = row["points"] if row else 0
        await interaction.response.send_message(f"⭐ نقاط {member.mention}: **{value}**")


async def setup(bot):
    await bot.add_cog(Games(bot))
