from __future__ import annotations

import asyncio
import io
import math
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from cogs.game_channels import is_group_game_channel_allowed
from database import connect


MIN_PLAYERS = 4
MAX_PLAYERS = 15
LOBBY_SECONDS = 30
DECISION_SECONDS = 15
WINNER_REWARD = 5

ELIMINATION_GIF_URL = (
    "https://cdn.discordapp.com/attachments/1476446187656708178/1540328111667941416/"
    "line_1787313239426.gif?ex=6a898dd7&is=6a883c57&hm=d0f114e4e11144e4cb6eca06654f2e963ab10b5032d70f1a8c7a63a9c961a5d5&"
)


@dataclass
class RouletteSession:
    guild_id: int
    channel_id: int
    starter_id: int
    players: list[int] = field(default_factory=list)
    active: bool = False
    round_number: int = 0
    board_message: discord.Message | None = None
    decision_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: tuple[str, int | None] | None = None
    selected_id: int | None = None


class RouletteLobbyView(discord.ui.View):
    def __init__(self, game: "FizboStyleRoulette", session: RouletteSession):
        super().__init__(timeout=LOBBY_SECONDS + 5)
        self.game = game
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        current = self.game.sessions.get(self.game.key(self.session))
        if current is not self.session or self.session.active:
            await interaction.response.send_message("❌ التسجيل سالا، اللعبة بدات.", ephemeral=True)
            return False
        if interaction.user.bot:
            await interaction.response.send_message("❌ البوتات ما كيدخلوش.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="دخول إلى اللعبة", style=discord.ButtonStyle.success, emoji="🎮", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.session.players:
            return await interaction.response.send_message("⚠️ راك داخل اللعبة أصلاً.", ephemeral=True)
        if len(self.session.players) >= MAX_PLAYERS:
            return await interaction.response.send_message("❌ وصلنا للحد الأقصى: 15 لاعب.", ephemeral=True)
        self.session.players.append(uid)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)

    @discord.ui.button(label="خروج من اللعبة", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.session.players:
            return await interaction.response.send_message("⚠️ راك ماشي داخل اللعبة.", ephemeral=True)
        self.session.players.remove(uid)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)


class RouletteDecisionView(discord.ui.View):
    def __init__(self, game: "FizboStyleRoulette", session: RouletteSession, selected_id: int):
        super().__init__(timeout=DECISION_SECONDS)
        self.game = game
        self.session = session
        self.selected_id = selected_id
        self.resolved = False
        self.target_buttons: list[discord.ui.Button] = []

        members = game.get_members(session)
        for member in members:
            button = discord.ui.Button(
                label=game.short_name(member.display_name),
                style=discord.ButtonStyle.danger,
                emoji="🎯",
                row=min(3, len(self.target_buttons) // 5),
            )
            button.disabled = member.id == selected_id

            async def callback(interaction: discord.Interaction, target_id: int = member.id):
                await self.choose_target(interaction, target_id)

            button.callback = callback
            self.add_item(button)
            self.target_buttons.append(button)

        random_button = discord.ui.Button(
            label="طرد عشوائي",
            style=discord.ButtonStyle.primary,
            emoji="🎲",
            row=4,
        )
        withdraw_button = discord.ui.Button(
            label="انسحاب",
            style=discord.ButtonStyle.secondary,
            emoji="🚪",
            row=4,
        )
        random_button.callback = self.choose_random
        withdraw_button.callback = self.choose_withdraw
        self.add_item(random_button)
        self.add_item(withdraw_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.selected_id:
            await interaction.response.send_message(
                "❌ غير اللاعب اللي اختارتو العجلة يقدر يدير القرار.", ephemeral=True
            )
            return False
        if self.resolved:
            await interaction.response.send_message("❌ القرار تسالى.", ephemeral=True)
            return False
        return True

    async def resolve(self, interaction: discord.Interaction, decision: tuple[str, int | None]):
        if self.resolved:
            return
        self.resolved = True
        self.session.decision = decision
        self.session.decision_event.set()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)

    async def choose_target(self, interaction: discord.Interaction, target_id: int):
        await self.resolve(interaction, ("kick", target_id))

    async def choose_random(self, interaction: discord.Interaction):
        await self.resolve(interaction, ("random", None))

    async def choose_withdraw(self, interaction: discord.Interaction):
        await self.resolve(interaction, ("withdraw", None))


class FizboStyleRoulette(commands.Cog):
    """Multi-message elimination roulette with a 30s lobby and 15s decisions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], RouletteSession] = {}

    @staticmethod
    def key(session: RouletteSession) -> tuple[int, int]:
        return session.guild_id, session.channel_id

    @staticmethod
    def short_name(name: str, limit: int = 18) -> str:
        cleaned = " ".join(name.split()) or "لاعب"
        return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"

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

    def get_members(self, session: RouletteSession) -> list[discord.Member]:
        guild = self.bot.get_guild(session.guild_id)
        if guild is None:
            return []
        members: list[discord.Member] = []
        for uid in session.players:
            member = guild.get_member(uid)
            if member and not member.bot:
                members.append(member)
        return members

    def build_lobby_text(self, session: RouletteSession, seconds_left: int) -> str:
        guild = self.bot.get_guild(session.guild_id)
        names = "\n".join(
            f"{index}. {guild.get_member(uid).mention}"
            for index, uid in enumerate(session.players, start=1)
            if guild and guild.get_member(uid)
        ) or "مازال حتى لاعب."
        return (
            "🎰 **الروليت**\n\n"
            f"👥 المشاركين: **{len(session.players)}/{MAX_PLAYERS}**\n"
            f"✅ خاص على الأقل **{MIN_PLAYERS} لاعبين**\n"
            f"⏳ اللعبة غادي تبدا بعد **{seconds_left} ثانية**\n\n"
            f"{names}\n\n"
            "اضغط **دخول إلى اللعبة** باش تشارك، أو **خروج من اللعبة** باش تنسحب قبل البداية."
        )

    async def update_lobby(self, session: RouletteSession, seconds_left: int = LOBBY_SECONDS) -> None:
        if session.board_message:
            try:
                await session.board_message.edit(content=self.build_lobby_text(session, seconds_left))
            except discord.HTTPException:
                pass

    async def start_lobby(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply(
                "❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", mention_author=False
            )
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاينة روليت مفتوحة فهاد الروم.", mention_author=False)

        session = RouletteSession(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            starter_id=message.author.id,
        )
        self.sessions[key] = session
        session.board_message = await message.channel.send(
            content=self.build_lobby_text(session, LOBBY_SECONDS),
            view=RouletteLobbyView(self, session),
        )

        try:
            for seconds_left in range(LOBBY_SECONDS - 1, -1, -1):
                await asyncio.sleep(1)
                if session.active or self.sessions.get(key) is not session:
                    return
                await self.update_lobby(session, seconds_left)

            session.active = True
            if len(session.players) < MIN_PLAYERS:
                self.sessions.pop(key, None)
                await message.channel.send(
                    f"❌ تسالا وقت التسجيل، ولكن ما وصلناش للحد الأدنى ديال **{MIN_PLAYERS} لاعبين**.\n"
                    "تم إلغاء الروليت."
                )
                try:
                    await session.board_message.edit(view=None)
                except discord.HTTPException:
                    pass
                return

            try:
                await session.board_message.edit(view=None, content="✅ **سال وقت التسجيل — غادي تبدا الروليت دابا.**")
            except discord.HTTPException:
                pass
            await asyncio.sleep(1.0)
            await self.run_game(session, message.channel)
        except asyncio.CancelledError:
            self.sessions.pop(key, None)
            raise

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.strip() == "-روليت":
            await self.start_lobby(message)

    async def run_game(self, session: RouletteSession, channel: discord.TextChannel) -> None:
        try:
            while len(session.players) > 2:
                session.round_number += 1
                selected_id = random.choice(session.players)
                session.selected_id = selected_id
                session.decision_event = asyncio.Event()
                session.decision = None

                wheel_file = await self.make_wheel_file(session, selected_id)
                await channel.send(
                    content=f"🎰 **الجولة {session.round_number}** — العجلة اختارت <@{selected_id}>.",
                    file=wheel_file,
                )

                view = RouletteDecisionView(self, session, selected_id)
                await channel.send(
                    content=(
                        f"**<@{selected_id}>، اختر الشخص لي بدك تطرده:**\n"
                        "عندك **15 ثانية**. اختار اسم لاعب، أو **طرد عشوائي**، أو **انسحاب**."
                    ),
                    view=view,
                )

                try:
                    await asyncio.wait_for(session.decision_event.wait(), timeout=DECISION_SECONDS)
                except asyncio.TimeoutError:
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await channel.send(content=f"**تم طرد <@{selected_id}> بسبب الخمول**")
                    await self.send_gif(channel)
                    await asyncio.sleep(1.5)
                    await channel.send("**سيتم بدأ الجولة التالية بعد قليل.**")
                    continue

                decision = session.decision
                if not decision:
                    continue
                action, target_id = decision

                if action == "withdraw":
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await channel.send(
                        content=f"**انسحب <@{selected_id}> من اللعبة، ستبدأ الجولة التالية بعد قليل.**"
                    )
                else:
                    if action == "random":
                        targets = [uid for uid in session.players if uid != selected_id]
                        target_id = random.choice(targets) if targets else selected_id
                    if target_id not in session.players or target_id == selected_id:
                        targets = [uid for uid in session.players if uid != selected_id]
                        target_id = random.choice(targets) if targets else selected_id
                    if target_id != selected_id and target_id in session.players:
                        session.players.remove(target_id)
                    await channel.send(
                        content=f"**تم طرد <@{target_id}> من اللعبة، سيتم بدأ الجولة التالية بعد قليل.**"
                    )
                    await self.send_gif(channel)

                await asyncio.sleep(1.5)

            if len(session.players) == 2:
                await self.final_round(session, channel)
            elif len(session.players) == 1:
                await self.finish_winner(session, channel, session.players[0])
        finally:
            session.active = False
            self.sessions.pop(self.key(session), None)

    async def final_round(self, session: RouletteSession, channel: discord.TextChannel) -> None:
        first, second = session.players
        session.round_number += 1
        selected = random.choice((first, second))
        session.selected_id = selected
        wheel_file = await self.make_wheel_file(session, selected)
        await channel.send(content="🎰 **الجولة النهائية — العجلة كتختار الفائز...**", file=wheel_file)
        await asyncio.sleep(1.25)
        await self.finish_winner(session, channel, selected)

    async def finish_winner(self, session: RouletteSession, channel: discord.TextChannel, winner_id: int) -> None:
        self.add_points(session.guild_id, winner_id, WINNER_REWARD)
        await channel.send(
            content=(
                f"🏆 **الفائز فالروليت هو <@{winner_id}>!**\n"
                f"⭐ ربح **{WINNER_REWARD} نقطة**."
            )
        )

    async def send_gif(self, channel: discord.TextChannel) -> None:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(ELIMINATION_GIF_URL) as response:
                    if response.status == 200:
                        data = await response.read()
                        await channel.send(file=discord.File(io.BytesIO(data), filename="elimination.gif"))
                        return
        except Exception:
            pass
        await channel.send(ELIMINATION_GIF_URL)

    @staticmethod
    async def avatar_bytes(member: discord.Member) -> bytes | None:
        try:
            return await member.display_avatar.replace(size=128, static_format="png").read()
        except Exception:
            return None

    async def make_wheel_file(self, session: RouletteSession, selected_id: int) -> discord.File:
        size = 1100
        image = Image.new("RGB", (size, size), (20, 22, 28))
        draw = ImageDraw.Draw(image)
        cx = cy = size // 2
        radius = 440
        colors = [
            (72, 91, 135), (100, 72, 132), (62, 125, 116), (141, 92, 60),
            (80, 111, 145), (115, 77, 96), (72, 130, 91), (137, 108, 57),
        ]
        members = self.get_members(session)
        count = max(1, len(members))
        slice_angle = 360.0 / count
        selected_index = next((i for i, m in enumerate(members) if m.id == selected_id), 0)
        rotation = -90 - (selected_index + 0.5) * slice_angle

        draw.ellipse((cx - radius - 12, cy - radius - 12, cx + radius + 12, cy + radius + 12), fill=(245, 198, 66))
        for index, member in enumerate(members):
            start = rotation + index * slice_angle
            end = start + slice_angle
            draw.pieslice(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                start=start,
                end=end,
                fill=colors[index % len(colors)],
                outline=(238, 240, 244),
                width=4,
            )

        center_r = 115
        draw.ellipse(
            (cx - center_r, cy - center_r, cx + center_r, cy + center_r),
            fill=(30, 33, 42),
            outline=(245, 198, 66),
            width=8,
        )
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            name_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        except OSError:
            title_font = name_font = small_font = ImageFont.load_default()

        for index, member in enumerate(members):
            angle = math.radians(rotation + (index + 0.5) * slice_angle)
            avatar_r = max(170, int(radius * 0.66))
            ax = int(cx + math.cos(angle) * avatar_r)
            ay = int(cy + math.sin(angle) * avatar_r)
            avatar = await self.avatar_bytes(member)
            if avatar:
                try:
                    avatar_img = Image.open(io.BytesIO(avatar)).convert("RGB").resize((92, 92))
                    mask = Image.new("L", (92, 92), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, 91, 91), fill=255)
                    image.paste(avatar_img, (ax - 46, ay - 46), mask)
                    outline = (255, 85, 85) if member.id == selected_id else (255, 255, 255)
                    draw.ellipse((ax - 46, ay - 46, ax + 46, ay + 46), outline=outline, width=6)
                except Exception:
                    pass

            label = self.short_name(member.display_name, 13)
            bbox = draw.textbbox((0, 0), label, font=name_font)
            text_w = bbox[2] - bbox[0]
            text_y = ay + 52
            draw.rounded_rectangle(
                (ax - text_w / 2 - 8, text_y - 3, ax + text_w / 2 + 8, text_y + 31),
                radius=9,
                fill=(12, 14, 18),
            )
            draw.text((ax - text_w / 2, text_y), label, fill=(255, 255, 255), font=name_font)

        pointer = [(cx, 18), (cx - 34, 92), (cx + 34, 92)]
        draw.polygon(pointer, fill=(255, 75, 75), outline=(255, 230, 230))

        selected_member = next((m for m in members if m.id == selected_id), None)
        selected_name = self.short_name(selected_member.display_name, 22) if selected_member else "Selected"
        bbox = draw.textbbox((0, 0), selected_name, font=name_font)
        selected_width = bbox[2] - bbox[0]
        draw.rounded_rectangle(
            (cx - selected_width / 2 - 18, size - 78, cx + selected_width / 2 + 18, size - 30),
            radius=14,
            fill=(245, 198, 66),
        )
        draw.text((cx - selected_width / 2, size - 70), selected_name, fill=(20, 22, 28), font=name_font)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return discord.File(buffer, filename="roulette-wheel.png")


async def setup(bot: commands.Bot):
    await bot.add_cog(FizboStyleRoulette(bot))
