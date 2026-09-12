from __future__ import annotations

import asyncio
import contextlib
import io
import random
import re
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from cogs.access_control import can_manage_events
from cogs import points as points_module
from cogs.points import add_category_points

ROULETTE_IMAGE_URL = "https://cdn.discordapp.com/attachments/1543608188975325268/1544727017243549826/a3a22b8922412e080f008b2177c2ba80c5ba947a7c003716e06b184059052e15.png?ex=6aa614e4&is=6aa4c364&hm=e2fdcf42e09aa1b6f1a41c777129b90697218bb2a03dc6b01d1ae460d03c5153&"
GROUP_BASE_POINTS = 7
WINNER_BONUS = 3
RACE_MIN = 2
RACE_MAX = 12
MAFIA_MIN = 5
MAFIA_MAX = 15
LOBBY_SECONDS = 30


def award_group_result(guild_id: int, participants: list[int], winners: list[int]) -> None:
    winner_set = set(winners)
    for uid in dict.fromkeys(participants):
        add_category_points(guild_id, uid, GROUP_BASE_POINTS, "group")
        if uid in winner_set:
            add_category_points(guild_id, uid, WINNER_BONUS, "group")


def _font(size: int, bold: bool = True):
    paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    )
    for path in paths:
        with contextlib.suppress(OSError):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _shape_ar(text: str) -> str:
    reshaper = getattr(points_module, "arabic_reshaper", None)
    bidi = getattr(points_module, "get_display", None)
    if reshaper and bidi:
        with contextlib.suppress(Exception):
            return bidi(reshaper.reshape(text))
    return text


def _rtl(draw, xy, text: str, font, fill=(250, 251, 255), anchor="ra"):
    draw.text(xy, _shape_ar(text), font=font, fill=fill, anchor=anchor)


def fixed_points_image(member: discord.Member, values: dict[str, int], avatar_bytes: bytes | None = None) -> discord.File:
    """RTL-safe points card: Arabic labels are rendered separately from numbers."""
    width, height = 1536, 700
    image = Image.new("RGB", (width, height), (3, 5, 15))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=64, fill=(15, 48, 158), outline=(45, 88, 220), width=4)
    draw.rounded_rectangle((58, 115, width - 58, 415), radius=42, fill=(22, 50, 138), outline=(11, 33, 105), width=3)

    if avatar_bytes:
        with contextlib.suppress(OSError, ValueError):
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(avatar, (190, 190), method=Image.Resampling.LANCZOS)
            image.paste(avatar, (94, 170))
    else:
        draw.ellipse((94, 170, 284, 360), fill=(35, 61, 140))

    name = (member.display_name or member.name).strip()[:24]
    if re.search(r"[\u0600-\u06ff]", name):
        _rtl(draw, (1410, 175), name, _font(48), anchor="ra")
    else:
        draw.text((330, 205), name, font=_font(58), fill=(250, 251, 255))
    _rtl(draw, (1410, 270), "نقاطي", _font(58), anchor="ra")
    _rtl(draw, (330, 320), "إجمالي النقاط", _font(32), fill=(221, 229, 255), anchor="la")
    draw.text((330, 350), f"{values['total']:,}", font=_font(58), fill=(255, 255, 255))

    draw.rounded_rectangle((58, 450, width - 58, 640), radius=34, fill=(10, 30, 96), outline=(25, 59, 158), width=3)
    cards = [
        ("الفردية", values["individual"], 88, 535),
        ("الجماعية", values["group"], 575, 1022),
        ("الروليت", values["roulette"], 1062, 1448),
    ]
    for index, (label, value, left, right) in enumerate(cards):
        if index:
            draw.line((left - 20, 478, left - 20, 610), fill=(35, 67, 157), width=2)
        center = (left + right) // 2
        _rtl(draw, (center, 500), label, _font(34), anchor="ma")
        draw.text((center, 565), f"{value:,}", font=_font(50), fill=(255, 255, 255), anchor="ma")

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="nawaf-points.png")


@dataclass
class Lobby:
    guild_id: int
    channel_id: int
    starter_id: int
    kind: str
    players: list[int] = field(default_factory=list)
    maximum: int = RACE_MAX
    message: discord.Message | None = None
    task: asyncio.Task | None = None
    active: bool = False


class JoinView(discord.ui.View):
    def __init__(self, cog: "EventGames", key: tuple[int, int]):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.cog = cog
        self.key = key

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.key)
        if not lobby or lobby.active:
            return await interaction.response.send_message("❌ اللعبة سالات أو بدات.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("❌ البوتات ما كيدخلوش.", ephemeral=True)
        if interaction.user.id in lobby.players:
            return await interaction.response.send_message("❌ راك داخل للعبة أصلاً.", ephemeral=True)
        if len(lobby.players) >= lobby.maximum:
            return await interaction.response.send_message("❌ اللوبي عامر.", ephemeral=True)
        lobby.players.append(interaction.user.id)
        await interaction.response.edit_message(embed=self.cog.lobby_embed(lobby), view=self)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.key)
        if not lobby or lobby.active:
            return await interaction.response.send_message("❌ اللعبة سالات أو بدات.", ephemeral=True)
        if interaction.user.id == lobby.starter_id:
            return await interaction.response.send_message("❌ صاحب الفعالية ما يقدرش يخرج. استعمل `-توقيف`.", ephemeral=True)
        if interaction.user.id not in lobby.players:
            return await interaction.response.send_message("❌ ماشي مشارك.", ephemeral=True)
        lobby.players.remove(interaction.user.id)
        await interaction.response.edit_message(embed=self.cog.lobby_embed(lobby), view=self)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="▶️")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.key)
        if not lobby:
            return await interaction.response.send_message("❌ اللعبة سالات.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يبدأ.", ephemeral=True)
        minimum = MAFIA_MIN if lobby.kind == "mafia" else RACE_MIN
        if len(lobby.players) < minimum:
            return await interaction.response.send_message(f"❌ خاص على الأقل **{minimum}** لاعبين.", ephemeral=True)
        await interaction.response.defer()
        await self.cog.begin(lobby)
        self.stop()


class VoteView(discord.ui.View):
    def __init__(self, cog: "EventGames", guild_id: int, living: list[int]):
        super().__init__(timeout=15)
        self.cog = cog
        self.living = living
        self.votes: dict[int, int] = {}
        for uid in living[:20]:
            member = cog.member(guild_id, uid)
            label = (member.display_name if member else str(uid))[:80]
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self.callback_for(uid)
            self.add_item(button)

    def callback_for(self, target_id: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id not in self.living:
                return await interaction.response.send_message("❌ خرجتي من اللعبة.", ephemeral=True)
            self.votes[interaction.user.id] = target_id
            await interaction.response.send_message(f"✅ تسجل تصويتك ضد <@{target_id}>.", ephemeral=True)
        return callback


class EventGames(commands.Cog):
    """Extra multiplayer events, point administration and UI fixes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lobbies: dict[tuple[int, int], Lobby] = {}
        self._patched = False

    async def cog_load(self):
        self.patch_existing_games()

    def patch_existing_games(self) -> None:
        if self._patched:
            return
        points_module.build_points_image = fixed_points_image

        try:
            import cogs.roulette_multi_message as roulette_module
            roulette = roulette_module.RouletteMultiMessage
            if not getattr(roulette, "_nawaf_event_v2_patched", False):
                original_lobby = roulette.lobby_embed
                original_run = roulette.run_game

                def lobby_with_image(cog, session, page=0):
                    embed = original_lobby(cog, session, page)
                    embed.set_image(url=ROULETTE_IMAGE_URL)
                    return embed

                async def run_with_points(cog, session, channel):
                    participants = list(session.players)
                    old_adder = roulette_module.add_roulette_points
                    roulette_module.add_roulette_points = lambda *args, **kwargs: None
                    try:
                        await original_run(cog, session, channel)
                    finally:
                        roulette_module.add_roulette_points = old_adder
                    winners = [uid for uid in session.players if uid in participants]
                    award_group_result(session.guild_id, participants, winners[:1])

                roulette.lobby_embed = lobby_with_image
                roulette.run_game = run_with_points
                roulette._nawaf_event_v2_patched = True
        except Exception as exc:
            print(f"[EVENTS_V2] Existing elimination game patch skipped: {exc!r}")

        try:
            import cogs.games as games_module
            games = games_module.Games
            if not getattr(games, "_nawaf_rewards_patched", False):
                original_dice = games.run_dice
                games.add_points = lambda *args, **kwargs: None

                async def run_dice_with_points(cog, channel, session, lobby):
                    participants = list(session.players)
                    await original_dice(cog, channel, session, lobby)
                    winners = [session.players[0]] if len(session.players) == 1 else []
                    award_group_result(session.guild_id, participants, winners)

                games.run_dice = run_dice_with_points
                games._nawaf_rewards_patched = True
        except Exception as exc:
            print(f"[EVENTS_V2] Dice reward patch skipped: {exc!r}")

        self._patched = True

    def member(self, guild_id: int, user_id: int):
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild else None

    def lobby_embed(self, lobby: Lobby) -> discord.Embed:
        title = "🕵️ المافيا" if lobby.kind == "mafia" else "🏁 السباق"
        minimum = MAFIA_MIN if lobby.kind == "mafia" else RACE_MIN
        names = "\n".join(f"- <@{uid}>" for uid in lobby.players) or "— لا يوجد مشاركون —"
        return discord.Embed(title=title, description=f"**الحد الأدنى:** {minimum}\n**المشاركون ({len(lobby.players)}/{lobby.maximum}):**\n{names}\n\nاضغط **دخول** للمشاركة.", color=discord.Color.blurple())

    async def start_lobby(self, message: discord.Message, kind: str):
        if not isinstance(message.author, discord.Member) or not can_manage_events(message.author):
            return await message.reply("❌ غير الإدارة أو رئيس الفعاليات يقدر يسوي الفعاليات.", mention_author=False)
        key = (message.guild.id, message.channel.id)
        if key in self.lobbies:
            return await message.reply("❌ كاينة فعالية مفتوحة فهاد الروم.", mention_author=False)
        maximum = MAFIA_MAX if kind == "mafia" else RACE_MAX
        lobby = Lobby(message.guild.id, message.channel.id, message.author.id, kind, [message.author.id], maximum)
        self.lobbies[key] = lobby
        lobby.message = await message.channel.send(embed=self.lobby_embed(lobby), view=JoinView(self, key))
        lobby.task = asyncio.create_task(self.countdown(lobby))

    async def countdown(self, lobby: Lobby):
        try:
            await asyncio.sleep(LOBBY_SECONDS)
            key = (lobby.guild_id, lobby.channel_id)
            if key not in self.lobbies or lobby.active:
                return
            minimum = MAFIA_MIN if lobby.kind == "mafia" else RACE_MIN
            if len(lobby.players) < minimum:
                with contextlib.suppress(discord.HTTPException):
                    await lobby.message.edit(embed=discord.Embed(title="❌ لم تبدأ الفعالية", description=f"انتهى الوقت قبل الوصول إلى **{minimum}** لاعبين.", color=discord.Color.red()), view=None)
                self.lobbies.pop(key, None)
                return
            await self.begin(lobby)
        except asyncio.CancelledError:
            return

    async def begin(self, lobby: Lobby):
        if lobby.active:
            return
        lobby.active = True
        if lobby.task and not lobby.task.done() and lobby.task is not asyncio.current_task():
            lobby.task.cancel()
        if lobby.kind == "race":
            await self.run_race(lobby)
        else:
            await self.run_mafia(lobby)
        self.lobbies.pop((lobby.guild_id, lobby.channel_id), None)

    async def run_race(self, lobby: Lobby):
        players = list(lobby.players)
        positions = {uid: 0 for uid in players}
        target = 30
        while max(positions.values(), default=0) < target:
            for uid in players:
                positions[uid] += random.randint(1, 6)
            rows = []
            for uid in players:
                filled = min(10, positions[uid] * 10 // target)
                rows.append(f"<@{uid}> {'🟩' * filled}{'⬜' * (10-filled)} **{positions[uid]}**")
            with contextlib.suppress(discord.HTTPException):
                await lobby.message.edit(embed=discord.Embed(title="🏁 السباق", description="\n".join(rows), color=discord.Color.blue()), view=None)
            await asyncio.sleep(2)
        top = max(positions.values())
        winners = [uid for uid, pos in positions.items() if pos == top]
        award_group_result(lobby.guild_id, players, winners)
        result = discord.Embed(title="🏆 انتهى السباق", description="\n".join(f"<@{uid}> — **{positions[uid]}**" for uid in players), color=discord.Color.green())
        result.add_field(name="الفائز", value=", ".join(f"<@{u}>" for u in winners), inline=False)
        result.add_field(name="النقاط", value="كل مشارك **+7** • الفائز **+10**", inline=False)
        with contextlib.suppress(discord.HTTPException):
            await lobby.message.edit(embed=result, view=None)

    async def run_mafia(self, lobby: Lobby):
        participants = list(lobby.players)
        alive = set(participants)
        shuffled = participants[:]
        random.shuffle(shuffled)
        mafia_count = max(1, len(participants) // 4)
        mafia = set(shuffled[:mafia_count])
        doctor = shuffled[mafia_count] if len(shuffled) > mafia_count else None
        detective = shuffled[mafia_count + 1] if len(participants) >= 7 and len(shuffled) > mafia_count + 1 else None
        roles = {uid: ("مافيا" if uid in mafia else "طبيب" if uid == doctor else "محقق" if uid == detective else "مواطن") for uid in participants}

        for uid, role in roles.items():
            member = self.member(lobby.guild_id, uid)
            if member:
                with contextlib.suppress(discord.HTTPException):
                    await member.send(f"🕵️ دورك في المافيا: **{role}**")

        with contextlib.suppress(discord.HTTPException):
            await lobby.message.edit(embed=discord.Embed(title="🕵️ المافيا بدأت", description="تم إرسال الأدوار في الخاص. تبدأ الجولة الأولى.", color=discord.Color.dark_blue()), view=None)

        while len(alive) > 2:
            mafia_alive = mafia & alive
            town_alive = alive - mafia
            if not mafia_alive or len(mafia_alive) >= len(town_alive):
                break
            victim = random.choice(list(town_alive))
            protected = doctor if doctor in alive and random.random() < 0.5 else None
            if victim != protected:
                alive.remove(victim)

            living = list(alive)
            vote = VoteView(self, lobby.guild_id, living)
            with contextlib.suppress(discord.HTTPException):
                await lobby.message.edit(embed=discord.Embed(title="☀️ نهار المافيا", description="صوّتوا على لاعب لإخراجه.", color=discord.Color.orange()), view=vote)
            await asyncio.sleep(15)
            if vote.votes:
                counts: dict[int, int] = {}
                for target in vote.votes.values():
                    if target in alive:
                        counts[target] = counts.get(target, 0) + 1
                if counts:
                    alive.discard(max(counts, key=counts.get))
            with contextlib.suppress(discord.HTTPException):
                await lobby.message.edit(embed=discord.Embed(title="🌙 ليل جديد", description="جولة جديدة بدأت...", color=discord.Color.dark_purple()), view=None)
            await asyncio.sleep(2)

        mafia_alive = mafia & alive
        town_alive = alive - mafia
        mafia_won = bool(mafia_alive) and len(mafia_alive) >= len(town_alive)
        winners = list(mafia_alive) if mafia_won else list(town_alive)
        award_group_result(lobby.guild_id, participants, winners)
        result = discord.Embed(title="🏆 انتهت المافيا", description=("الفريق الفائز: **المافيا**" if mafia_won else "الفريق الفائز: **المواطنون**") + "\n\n" + "\n".join(f"<@{uid}> — **{role}**" for uid, role in roles.items()), color=discord.Color.green())
        result.add_field(name="النقاط", value="كل مشارك **+7** • الفائز **+10**", inline=False)
        with contextlib.suppress(discord.HTTPException):
            await lobby.message.edit(embed=result, view=None)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventGames(bot))
