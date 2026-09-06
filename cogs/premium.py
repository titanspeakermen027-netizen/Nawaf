from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import connect

BOT_OWNER_ID = 1472570059367911587
MIN_ROULETTE_PLAYERS = 4
DEFAULT_ROULETTE_MAX = 15
ABSOLUTE_ROULETTE_MAX = 2000
SUPPORT_INVITE = "https://discord.gg/Tmnb2QBs2d"

DURATION_RE = re.compile(r"^(\d+)(y|mo|w|d|h|m|s)$", re.IGNORECASE)
DURATION_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
    "mo": 60 * 60 * 24 * 30,
    "y": 60 * 60 * 24 * 365,
}

UPSELL_TEXT = (
    "**يمكنك تغيير الحد الاقصى للاعبين هذه الميزة حصرية للبريميوم فقط**\n"
    "للتواصل لاخد البريميوم خش ذا السيرفر و منشن "
    f"<@{BOT_OWNER_ID}> و ادفع له بالطرق المتاحة و بيعطيك بريميوم بالمدة على حسب لي شريته انت\n"
    f"{SUPPORT_INVITE}"
)


def ensure_premium_table() -> None:
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_premium (
                guild_id INTEGER PRIMARY KEY,
                activated_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                activated_by INTEGER NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS roulette_premium_config (
                guild_id INTEGER PRIMARY KEY,
                maximum_players INTEGER NOT NULL DEFAULT 15
            )
            """
        )


def get_premium_expiry(guild_id: int) -> int | None:
    ensure_premium_table()
    with connect() as con:
        row = con.execute(
            "SELECT expires_at FROM guild_premium WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return None
    expiry = int(row[0])
    if expiry <= int(time.time()):
        with connect() as con:
            con.execute("DELETE FROM guild_premium WHERE guild_id=?", (guild_id,))
        return None
    return expiry


def is_premium(guild_id: int) -> bool:
    return get_premium_expiry(guild_id) is not None


def get_roulette_max(guild_id: int) -> int:
    if not is_premium(guild_id):
        return DEFAULT_ROULETTE_MAX
    with connect() as con:
        row = con.execute(
            "SELECT maximum_players FROM roulette_premium_config WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return DEFAULT_ROULETTE_MAX
    return max(MIN_ROULETTE_PLAYERS, min(ABSOLUTE_ROULETTE_MAX, int(row[0])))


def set_roulette_max(guild_id: int, maximum_players: int) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO roulette_premium_config(guild_id, maximum_players)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET maximum_players=excluded.maximum_players
            """,
            (guild_id, maximum_players),
        )


def parse_duration(value: str) -> int | None:
    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    amount = int(match.group(1))
    if amount <= 0:
        return None
    return amount * DURATION_SECONDS[match.group(2).lower()]


def format_dt(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Premium(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_premium_table()

    async def cog_load(self):
        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is None:
            return

        module = __import__(
            "cogs.roulette_multi_message",
            fromlist=["Session", "LobbyView", "DecisionView"],
        )
        original_session_init = module.Session.__init__
        original_decision_view = module.DecisionView

        def session_init(session, guild_id: int, channel_id: int, starter_id: int):
            original_session_init(session, guild_id, channel_id, starter_id)
            session.max_players = get_roulette_max(guild_id)

        module.Session.__init__ = session_init

        def dynamic_lobby_text(session, remaining: int):
            names = []
            for index, uid in enumerate(session.players, 1):
                member = roulette.member(session.guild_id, uid)
                names.append(f"{index}. {member.mention if member else f'<@{uid}>'}")
            roster = "\n".join(names) or "مازال حتى لاعب."
            maximum = getattr(session, "max_players", DEFAULT_ROULETTE_MAX)
            return (
                "🎰 **روليت الإقصاء**\n\n"
                f"👥 المشاركين: **{len(session.players)}/{maximum}**\n"
                f"✅ الحد الأدنى: **{MIN_ROULETTE_PLAYERS} لاعبين**\n"
                f"⏳ البداية التلقائية بعد **{remaining} ثانية**\n\n"
                f"{roster}\n\n"
                "اضغط على **دخول إلى اللعبة** للمشاركة أو **خروج من اللعبة** للانسحاب."
            )

        roulette.lobby_text = dynamic_lobby_text

        async def dynamic_join(view, interaction: discord.Interaction, button: discord.ui.Button):
            maximum = getattr(view.session, "max_players", DEFAULT_ROULETTE_MAX)
            if interaction.user.id in view.session.players:
                return await interaction.response.send_message("⚠️ راك داخل اللعبة أصلاً.", ephemeral=True)
            if len(view.session.players) >= maximum:
                return await interaction.response.send_message(
                    f"❌ وصلنا للحد الأقصى ديال **{maximum} لاعب**.", ephemeral=True
                )
            view.session.players.append(interaction.user.id)
            await interaction.response.defer()
            await view.game.update_lobby(view.session)

        module.LobbyView.join.callback = dynamic_join

        class PremiumDecisionView(original_decision_view):
            def __init__(self, game, session, selected_id: int):
                discord.ui.View.__init__(self, timeout=module.DECISION_SECONDS)
                self.game = game
                self.session = session
                self.selected_id = selected_id
                self.done = False

                targets = [uid for uid in session.players if uid != selected_id]
                if len(targets) <= 14:
                    for index, uid in enumerate(targets):
                        member = game.member(session.guild_id, uid)
                        label = game.short_name(member.display_name if member else str(uid))
                        button = discord.ui.Button(
                            label=label,
                            style=discord.ButtonStyle.danger,
                            emoji="🎯",
                            row=index // 5,
                        )

                        async def callback(interaction: discord.Interaction, target_id: int = uid):
                            await self.resolve(interaction, "kick", target_id)

                        button.callback = callback
                        self.add_item(button)
                else:
                    select = discord.ui.UserSelect(
                        placeholder="اختار اللاعب لي بدك تطرده",
                        min_values=1,
                        max_values=1,
                        row=0,
                    )

                    async def select_callback(interaction: discord.Interaction):
                        chosen = select.values[0] if select.values else None
                        target_id = getattr(chosen, "id", None)
                        if target_id not in self.session.players or target_id == self.selected_id:
                            return await interaction.response.send_message(
                                "❌ خاصك تختار لاعب مشارك وماشي اللاعب اللي اختارتو العجلة.",
                                ephemeral=True,
                            )
                        await self.resolve(interaction, "kick", target_id)

                    select.callback = select_callback
                    self.add_item(select)

                random_button = discord.ui.Button(
                    label="طرد عشوائي", style=discord.ButtonStyle.primary, emoji="🎲", row=4
                )
                withdraw_button = discord.ui.Button(
                    label="انسحاب", style=discord.ButtonStyle.secondary, emoji="🚪", row=4
                )
                random_button.callback = self.random_kick
                withdraw_button.callback = self.withdraw
                self.add_item(random_button)
                self.add_item(withdraw_button)

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.selected_id:
                    await interaction.response.send_message(
                        "❌ هاد القرار غير للاعب اللي اختارتو العجلة.", ephemeral=True
                    )
                    return False
                if self.done:
                    await interaction.response.send_message("❌ القرار سالا.", ephemeral=True)
                    return False
                return True

            async def resolve(self, interaction: discord.Interaction, action: str, target_id: int | None = None):
                if self.done:
                    return
                self.done = True
                self.session.decision = (action, target_id)
                self.session.decision_event.set()
                for item in self.children:
                    if hasattr(item, "disabled"):
                        item.disabled = True
                await interaction.response.edit_message(view=self)

            async def random_kick(self, interaction: discord.Interaction):
                await self.resolve(interaction, "random")

            async def withdraw(self, interaction: discord.Interaction):
                await self.resolve(interaction, "withdraw")

        module.DecisionView = PremiumDecisionView

        # The webhook must own the roulette session. Do not start a game when
        # Manage Webhooks/Create Webhooks is unavailable, and never fall back
        # to sending game messages as the normal bot identity.
        original_start_lobby = roulette.start_lobby
        original_game_send = roulette.game_send

        async def webhook_only_start_lobby(message: discord.Message):
            if not message.guild:
                return await original_start_lobby(message)
            webhook = await roulette.get_game_webhook(message.guild, message.channel)
            if webhook is None:
                return await message.reply(
                    "❌ خاص البوت تكون عندو صلاحية **Manage Webhooks** باش تبدأ لعبة الروليت.",
                    mention_author=False,
                )
            return await original_start_lobby(message)

        roulette.start_lobby = webhook_only_start_lobby

        async def webhook_only_game_send(
            session,
            channel,
            *,
            content=None,
            embed=None,
            file=None,
            view=None,
        ):
            guild = self.bot.get_guild(session.guild_id)
            if not guild:
                return None
            webhook = await roulette.get_game_webhook(guild, channel)
            if webhook is None:
                return None
            try:
                return await webhook.send(
                    content=content,
                    embed=embed,
                    file=file,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    wait=True,
                )
            except (discord.Forbidden, discord.HTTPException):
                roulette.game_webhooks.pop((session.guild_id, session.channel_id), None)
                return None

        roulette.game_send = webhook_only_game_send

    @app_commands.command(
        name="maximum-number-players-roullete",
        description="تحديد الحد الأقصى للاعبين في روليت السيرفر للبريميوم",
    )
    @app_commands.describe(maximum="الحد الأقصى من 4 إلى 2000 لاعب")
    async def maximum_number_players_roullete(
        self, interaction: discord.Interaction, maximum: app_commands.Range[int, 4, 2000]
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ هاد الأمر خاص بالسيرفرات.", ephemeral=True)
        if not is_premium(interaction.guild.id):
            return await interaction.response.send_message(UPSELL_TEXT, ephemeral=True)
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ غير صاحب السيرفر يقدر يبدل الحد الأقصى للاعبين.", ephemeral=True
            )

        set_roulette_max(interaction.guild.id, int(maximum))
        await interaction.response.send_message(
            f"✅ تم تحديد الحد الأقصى للروليت في **{maximum} لاعب**.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content.lower().startswith("prm "):
            return
        if message.author.id != BOT_OWNER_ID:
            return

        duration_text = content[4:].strip()
        seconds = parse_duration(duration_text)
        if seconds is None:
            await message.reply(
                "❌ الصيغة غير صحيحة. استعمل مثلاً: `prm 1mo` أو `prm 1y` أو `prm 2w`.",
                mention_author=False,
            )
            return

        now = int(time.time())
        expires = now + seconds
        with connect() as con:
            con.execute(
                """
                INSERT INTO guild_premium(guild_id, activated_at, expires_at, activated_by)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    activated_at=excluded.activated_at,
                    expires_at=excluded.expires_at,
                    activated_by=excluded.activated_by
                """,
                (message.guild.id, now, expires, BOT_OWNER_ID),
            )

        embed = discord.Embed(
            title="💎 تم تفعيل البريميوم",
            description=(
                f"**تم تفعيل البريميوم لسيرفر {message.guild.name}**\n\n"
                f"**تاريخ تفعيل:** {format_dt(now)}\n"
                f"**تاريخ انتهاء:** {format_dt(expires)}\n"
                f"**ايدي صاحب السيرفر:** {message.guild.owner_id}\n"
                f"**ايدي سيرفر:** {message.guild.id}"
            ),
            color=discord.Color.gold(),
        )
        await message.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Premium(bot))
