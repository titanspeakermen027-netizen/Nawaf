from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from database import connect

ELIMINATION_STICKER = (
    "https://cdn.discordapp.com/attachments/1543608188975325268/1544412966642917508/50-3.gif"
    "?ex=6a98629e&is=6a9718a9&hm=10b89e168dfecb8ff3a53107fabce46e7cf12101c286dfe25e118445ccc40a78&"
)


@dataclass
class RouletteRound:
    guild_id: int
    channel_id: int
    starter_id: int
    reward: int = 5
    max_players: int = 15
    players: list[int] = field(default_factory=list)
    active: bool = False
    round_number: int = 0
    message: discord.Message | None = None
    chosen_id: int | None = None
    action_event: asyncio.Event = field(default_factory=asyncio.Event)
    chosen_action: str | None = None


class RouletteActionView(discord.ui.View):
    def __init__(self, game: "RouletteUpgrade", session: RouletteRound, selected_id: int):
        super().__init__(timeout=30)
        self.game = game
        self.session = session
        self.selected_id = selected_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.selected_id:
            await interaction.response.send_message(
                "❌ هاد القرار خاص باللاعب اللي اختارتو العجلة.", ephemeral=True
            )
            return False
        if not self.session.active or self.game.sessions.get(self.game.key(self.session)) is not self.session:
            await interaction.response.send_message("❌ هاد الجولة سالات.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="طرد عشوائي", style=discord.ButtonStyle.danger, emoji="🎯")
    async def kick_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.chosen_action = "kick"
        self.session.action_event.set()
        await interaction.response.edit_message(view=self.game.disabled_view(self))

    @discord.ui.button(label="انسحاب", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.chosen_action = "withdraw"
        self.session.action_event.set()
        await interaction.response.edit_message(view=self.game.disabled_view(self))


class RouletteUpgrade(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], RouletteRound] = {}
        self.originals: dict[str, Callable[..., Awaitable]] = {}

    @staticmethod
    def key(session: RouletteRound) -> tuple[int, int]:
        return session.guild_id, session.channel_id

    @staticmethod
    def disabled_view(view: discord.ui.View) -> discord.ui.View:
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        return view

    def add_points(self, guild_id: int, user_id: int, amount: int) -> None:
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)",
                (guild_id, user_id),
            )
            con.execute(
                "UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )

    def active_session(self, guild_id: int, channel_id: int) -> RouletteRound | None:
        session = self.sessions.get((guild_id, channel_id))
        if session and session.active:
            return session
        return session

    def lobby_embed(self, session: RouletteRound) -> discord.Embed:
        players = "\n".join(
            f"**{idx:02d}** ・ <@{uid}>" for idx, uid in enumerate(session.players, start=1)
        ) or "—"
        embed = discord.Embed(
            title="🎰 Nawaf Roulette",
            description=(
                "لعبة إقصاء جماعية بدون رهانات.\n"
                "منين كتوقف العجلة على لاعب، هو اللي كيقرر: **طرد عشوائي** أو **انسحاب**.\n"
                "إلى بقاو غير جوج، العجلة كتختار الفائز."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name="👥 اللاعبين", value=f"**{len(session.players)}/{session.max_players}**", inline=True)
        embed.add_field(name="⭐ جائزة الفائز", value=f"**{session.reward} نقطة**", inline=True)
        embed.add_field(name="🔄 الجولة", value=f"**{session.round_number or 1}**", inline=True)
        embed.add_field(name="🎟️ المشاركون", value=players, inline=False)
        embed.set_footer(text="دخول: !دخول • خروج: !خروج • بدء: !ابدأ • إنهاء: !انهاء")
        return embed

    def action_embed(self, session: RouletteRound, selected_id: int) -> discord.Embed:
        embed = discord.Embed(
            title="🎰 العجلة اختارت!",
            description=(
                f"🎯 الدور على <@{selected_id}>\n\n"
                "شنو القرار؟\n"
                "**طرد عشوائي**: يتم اختيار لاعب آخر عشوائياً وإقصاؤه.\n"
                "**انسحاب**: اللاعب المختار ينسحب من الجولة."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="👥 المتبقون", value=f"**{len(session.players)}**", inline=True)
        embed.add_field(name="🔄 الجولة", value=f"**{session.round_number}**", inline=True)
        embed.set_footer(text="الاختيار متاح فقط للاعب الذي اختارته العجلة • المهلة 30 ثانية")
        return embed

    def result_embed(self, title: str, description: str, color: discord.Color) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    async def send_lobby(self, channel: discord.TextChannel, session: RouletteRound) -> discord.Message:
        message = await channel.send(embed=self.lobby_embed(session), view=RouletteLobbyView(self, session))
        session.message = message
        return message

    async def start(self, message: discord.Message, args: list[str]) -> None:
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await message.reply("❌ غير الإدارة تقدر تشغل الروليت الجماعية.", mention_author=False)

        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاينة روليت مفتوحة فهاد الروم.", mention_author=False)

        reward = 5
        max_players = 15
        if args and args[0].isdigit():
            reward = max(0, min(int(args[0]), 1000))
        if len(args) > 1 and args[1].isdigit():
            max_players = max(2, min(int(args[1]), 15))

        session = RouletteRound(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            starter_id=message.author.id,
            reward=reward,
            max_players=max_players,
            players=[message.author.id],
        )
        self.sessions[key] = session
        await self.send_lobby(message.channel, session)

    async def join(self, message: discord.Message) -> None:
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await message.reply("❌ ما كايناش روليت مفتوحة هنا.", mention_author=False)
        if session.active:
            return await message.reply("❌ الجولة بدات بالفعل.", mention_author=False)
        if message.author.id in session.players:
            return await message.reply("⚠️ راك داخل اللعبة أصلاً.", mention_author=False)
        if len(session.players) >= session.max_players:
            return await message.reply(
                f"❌ اللعبة عامرة — الحد الأقصى هو **{session.max_players}** لاعب.", mention_author=False
            )
        session.players.append(message.author.id)
        await self.update_lobby_message(session)
        await message.reply(
            f"✅ دخل {message.author.mention} للروليت. **{len(session.players)}/{session.max_players}**",
            mention_author=False,
        )

    async def leave(self, message: discord.Message) -> None:
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session or session.active or message.author.id not in session.players:
            return await message.reply("❌ ما نتايش داخل روليت مفتوحة هنا.", mention_author=False)
        if message.author.id == session.starter_id:
            return await message.reply("❌ مشغل اللعبة ما يقدرش يخرج؛ استعمل `!انهاء`.", mention_author=False)
        session.players.remove(message.author.id)
        await self.update_lobby_message(session)
        await message.reply(f"✅ خرج {message.author.mention} من اللعبة.", mention_author=False)

    async def end(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.manage_guild:
            return await message.reply("❌ غير الإدارة تقدر تسالي الروليت.", mention_author=False)
        session = self.sessions.pop((message.guild.id, message.channel.id), None)
        if not session:
            return await message.reply("❌ ما كايناش روليت مفتوحة هنا.", mention_author=False)
        session.active = False
        await message.reply("🛑 تم إغلاق الروليت.", mention_author=False)

    async def spin(self, message: discord.Message) -> None:
        session = self.sessions.get((message.guild.id, message.channel.id))
        if not session:
            return await message.reply("❌ ما كايناش روليت مفتوحة هنا.", mention_author=False)
        if not isinstance(message.author, discord.Member) or (
            message.author.id != session.starter_id and not message.author.guild_permissions.manage_guild
        ):
            return await message.reply("❌ غير مشغل اللعبة أو الإدارة يقدر يبدأ العجلة.", mention_author=False)
        if session.active:
            return await message.reply("⚠️ الروليت خدامة دابا.", mention_author=False)
        if len(session.players) < 2:
            return await message.reply("❌ خاص على الأقل **2 لاعبين**.", mention_author=False)

        session.active = True
        await self.run(session, message.channel)

    async def run(self, session: RouletteRound, channel: discord.TextChannel) -> None:
        if session.message:
            game_message = session.message
        else:
            game_message = await channel.send(embed=self.lobby_embed(session))
            session.message = game_message

        try:
            while len(session.players) > 2:
                session.round_number += 1
                selected = await self.spin_wheel(game_message, session)
                session.chosen_id = selected
                session.action_event = asyncio.Event()
                session.chosen_action = None

                await game_message.edit(embed=self.action_embed(session, selected), view=RouletteActionView(self, session, selected))
                try:
                    await asyncio.wait_for(session.action_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    session.chosen_action = "withdraw"

                action = session.chosen_action
                if action == "kick":
                    targets = [uid for uid in session.players if uid != selected]
                    if not targets:
                        continue
                    eliminated = random.choice(targets)
                    session.players.remove(eliminated)
                    embed = self.result_embed(
                        "🎯 طرد عشوائي",
                        f"<@{selected}> اختار **طرد عشوائي**.\n\n❌ تم إقصاء <@{eliminated}>.\n👥 المتبقون: **{len(session.players)}**",
                        discord.Color.red(),
                    )
                    embed.set_image(url=ELIMINATION_STICKER)
                else:
                    session.players.remove(selected)
                    embed = self.result_embed(
                        "🚪 انسحاب",
                        f"<@{selected}> اختار **الانسحاب**.\n\n👋 خرج من الجولة.\n👥 المتبقون: **{len(session.players)}**",
                        discord.Color.dark_grey(),
                    )

                embed.set_footer(text=f"الجولة {session.round_number} • الجولة القادمة بعد لحظات")
                await game_message.edit(embed=embed, view=None)
                await asyncio.sleep(1.8)

            if len(session.players) == 2:
                session.round_number += 1
                await self.final_duel(game_message, session)
        finally:
            session.active = False
            self.sessions.pop(self.key(session), None)

    async def spin_wheel(self, message: discord.Message, session: RouletteRound) -> int:
        selected = session.players[0]
        for index in range(10):
            selected = random.choice(session.players)
            embed = discord.Embed(
                title="🎰 العجلة كتدور...",
                description=(
                    f"**الجولة {session.round_number}**\n\n"
                    "🔴  ⚫  🔴  🟢  ⚫\n\n"
                    f"➡️ {index + 1}/10  •  الخانة الحالية: <@{selected}>"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"{len(session.players)} لاعبين باقيين")
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(0.22 + index * 0.025)
        return selected

    async def final_duel(self, message: discord.Message, session: RouletteRound) -> None:
        first, second = session.players
        for index in range(12):
            selected = first if index % 2 == 0 else second
            embed = discord.Embed(
                title="🏁 الجولة النهائية",
                description=(
                    "بقاو غير جوج لاعبين!\n\n"
                    f"🎯 العجلة كتتنقل بين <@{first}> و <@{second}>\n\n"
                    f"➡️ الحركة {index + 1}/12: <@{selected}>"
                ),
                color=discord.Color.gold(),
            )
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(0.18 + index * 0.018)

        winner = random.choice((first, second))
        self.add_points(session.guild_id, winner, session.reward)
        embed = discord.Embed(
            title="🏆 الروليت انتهات",
            description=f"🎉 الفائز هو <@{winner}>!\n\n⭐ ربح **{session.reward} نقطة**.",
            color=discord.Color.green(),
        )
        embed.add_field(name="🔄 الجولات", value=f"**{session.round_number}**", inline=True)
        embed.add_field(name="👥 آخر متنافسين", value=f"<@{first}> و <@{second}>", inline=True)
        embed.set_footer(text="يمكن تشغيل روليت جديدة في نفس الروم")
        await message.edit(embed=embed, view=None)

    async def update_lobby_message(self, session: RouletteRound) -> None:
        if session.message:
            await session.message.edit(embed=self.lobby_embed(session), view=RouletteLobbyView(self, session))

    def patch_games(self) -> None:
        games = self.bot.get_cog("Games")
        if not games:
            return

        self.originals["start"] = games._prefix_start
        self.originals["join"] = games._prefix_join
        self.originals["leave"] = games._prefix_leave
        self.originals["spin"] = games._prefix_spin
        self.originals["end"] = games._prefix_end

        async def prefix_start(message: discord.Message, game_type: str, args: list[str]):
            if game_type == "roulette":
                return await self.start(message, args)
            return await self.originals["start"](message, game_type, args)

        async def prefix_join(message: discord.Message):
            key = (message.guild.id, message.channel.id)
            if key in self.sessions:
                return await self.join(message)
            return await self.originals["join"](message)

        async def prefix_leave(message: discord.Message):
            key = (message.guild.id, message.channel.id)
            if key in self.sessions:
                return await self.leave(message)
            return await self.originals["leave"](message)

        async def prefix_spin(message: discord.Message):
            key = (message.guild.id, message.channel.id)
            if key in self.sessions:
                return await self.spin(message)
            return await self.originals["spin"](message)

        async def prefix_end(message: discord.Message):
            key = (message.guild.id, message.channel.id)
            if key in self.sessions:
                return await self.end(message)
            return await self.originals["end"](message)

        games._prefix_start = prefix_start
        games._prefix_join = prefix_join
        games._prefix_leave = prefix_leave
        games._prefix_spin = prefix_spin
        games._prefix_end = prefix_end


class RouletteLobbyView(discord.ui.View):
    def __init__(self, game: RouletteUpgrade, session: RouletteRound):
        super().__init__(timeout=None)
        self.game = game
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        current = self.game.sessions.get(self.game.key(self.session))
        if current is not self.session:
            await interaction.response.send_message("❌ هاد الروليت سالات.", ephemeral=True)
            return False
        if self.session.active:
            await interaction.response.send_message("❌ الجولة بدات بالفعل.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما كيدخلوش للعبة.", ephemeral=True)
        if interaction.user.id in self.session.players:
            return await interaction.response.send_message("⚠️ راك داخل أصلاً.", ephemeral=True)
        if len(self.session.players) >= self.session.max_players:
            return await interaction.response.send_message("❌ اللعبة عامرة.", ephemeral=True)
        self.session.players.append(interaction.user.id)
        await interaction.response.edit_message(embed=self.game.lobby_embed(self.session), view=self)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.session.starter_id:
            return await interaction.response.send_message("❌ مشغل اللعبة ما يقدرش يخرج.", ephemeral=True)
        if interaction.user.id not in self.session.players:
            return await interaction.response.send_message("❌ ماشي داخل اللعبة.", ephemeral=True)
        self.session.players.remove(interaction.user.id)
        await interaction.response.edit_message(embed=self.game.lobby_embed(self.session), view=self)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="🎰")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.session.starter_id and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ غير مشغل اللعبة أو الإدارة.", ephemeral=True)
        if len(self.session.players) < 2:
            return await interaction.response.send_message("❌ خاص على الأقل 2 لاعبين.", ephemeral=True)
        await interaction.response.defer()
        self.session.active = True
        await self.game.run(self.session, interaction.channel)

    @discord.ui.button(label="الحالة", style=discord.ButtonStyle.secondary, emoji="📊")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=self.game.lobby_embed(self.session), ephemeral=True)


async def setup(bot: commands.Bot):
    cog = RouletteUpgrade(bot)
    await bot.add_cog(cog)
    cog.patch_games()
