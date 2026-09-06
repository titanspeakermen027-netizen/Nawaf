from __future__ import annotations

import asyncio
import io
import math
import random
from typing import Optional

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from cogs.game_channels import is_group_game_channel_allowed
from database import connect

MIN_PLAYERS = 4
DEFAULT_MAX_PLAYERS = 15
ABSOLUTE_MAX_PLAYERS = 2000
LOBBY_SECONDS = 30
DECISION_SECONDS = 15
WINNER_REWARD = 5

ELIMINATION_GIF_URL = (
    "https://cdn.discordapp.com/attachments/1476446187656708178/1540328111667941416/"
    "line_1787313239426.gif?ex=6a898dd7&is=6a883c57&hm=d0f114e4e11144e4cb6eca06654f2e963ab10b5032d70f1a8c7a63a9c961a5d5&"
)


def get_server_max(guild_id: int) -> int:
    try:
        from cogs.premium import get_roulette_max
        return get_roulette_max(guild_id)
    except Exception:
        return DEFAULT_MAX_PLAYERS


class Session:
    def __init__(self, guild_id: int, channel_id: int, starter_id: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.starter_id = starter_id
        self.players: list[int] = []
        self.max_players = get_server_max(guild_id)
        self.active = False
        self.round = 0
        self.lobby_message: Optional[discord.Message] = None
        self.lobby_webhook: Optional[discord.Webhook] = None
        self.decision_event = asyncio.Event()
        self.decision: tuple[str, Optional[int]] | None = None


class LobbyView(discord.ui.View):
    def __init__(self, game: "RouletteMultiMessage", session: Session):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.game = game
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.game.sessions.get(self.game.key(self.session)) is not self.session or self.session.active:
            await interaction.response.send_message("❌ التسجيل سالا.", ephemeral=True)
            return False
        if interaction.user.bot:
            await interaction.response.send_message("❌ البوتات ما كيدخلوش.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="دخول إلى اللعبة", style=discord.ButtonStyle.success, emoji="🎮")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.session.players:
            return await interaction.response.send_message("⚠️ راك داخل اللعبة أصلاً.", ephemeral=True)
        if len(self.session.players) >= self.session.max_players:
            return await interaction.response.send_message(
                f"❌ وصلنا للحد الأقصى ديال **{self.session.max_players} لاعب**.",
                ephemeral=True,
            )
        self.session.players.append(interaction.user.id)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)

    @discord.ui.button(label="خروج من اللعبة", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.session.players:
            return await interaction.response.send_message("⚠️ راك ماشي داخل اللعبة.", ephemeral=True)
        self.session.players.remove(interaction.user.id)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)


class DecisionView(discord.ui.View):
    def __init__(self, game: "RouletteMultiMessage", session: Session, selected_id: int):
        super().__init__(timeout=DECISION_SECONDS)
        self.game = game
        self.session = session
        self.selected_id = selected_id
        self.done = False
        targets = [uid for uid in session.players if uid != selected_id]

        if len(targets) <= 14:
            for index, uid in enumerate(targets):
                member = game.member(session.guild_id, uid)
                label = game.short_name(member.display_name if member else str(uid), 18)
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.danger,
                    emoji="🎯",
                    row=min(3, index // 5),
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

    async def resolve(self, interaction: discord.Interaction, action: str, target_id: Optional[int] = None):
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


class RouletteMultiMessage(commands.Cog):
    """Fizbo/Clover-style elimination roulette using a server-named webhook."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], Session] = {}
        self.game_webhooks: dict[tuple[int, int], discord.Webhook] = {}

    @staticmethod
    def key(session: Session) -> tuple[int, int]:
        return session.guild_id, session.channel_id

    @staticmethod
    def short_name(name: str, limit: int = 18) -> str:
        name = " ".join(name.split()) or "لاعب"
        return name if len(name) <= limit else name[: limit - 1] + "…"

    def member(self, guild_id: int, user_id: int) -> Optional[discord.Member]:
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild else None

    def make_lobby_art(self, guild: discord.Guild, players: int, maximum: int) -> discord.File:
        """Generate the clean lobby card: only game name, server name and player count."""
        width, height = 1200, 675
        image = Image.new("RGB", (width, height), (18, 16, 23))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (45, 45, width - 45, height - 45),
            radius=42,
            fill=(28, 24, 35),
            outline=(120, 74, 210),
            width=5,
        )

        try:
            title_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 112
            )
            server_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46
            )
            count_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 78
            )
        except OSError:
            title_font = server_font = count_font = ImageFont.load_default()

        def centered(text: str, y: int, font: ImageFont.ImageFont, fill):
            box = draw.textbbox((0, 0), text, font=font)
            x = (width - (box[2] - box[0])) / 2
            draw.text((x, y), text, font=font, fill=fill)

        centered("روليت", 105, title_font, (250, 250, 252))
        centered(self.short_name(guild.name, 34), 270, server_font, (216, 207, 228))

        count_text = f"{players} / {maximum}"
        box = draw.textbbox((0, 0), count_text, font=count_font)
        count_w = box[2] - box[0]
        x1 = (width - count_w) / 2 - 44
        x2 = (width + count_w) / 2 + 44
        draw.rounded_rectangle(
            (x1, 402, x2, 532),
            radius=34,
            fill=(20, 18, 27),
            outline=(245, 245, 248),
            width=4,
        )
        centered(count_text, 420, count_font, (255, 255, 255))

        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        buffer.seek(0)
        return discord.File(buffer, filename="roulette-lobby.png")

    async def get_game_webhook(self, guild: discord.Guild, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        key = (guild.id, channel.id)
        cached = self.game_webhooks.get(key)
        if cached is not None:
            return cached
        webhook_name = self.short_name(guild.name, 80)
        try:
            hooks = await channel.webhooks()
            for hook in hooks:
                if hook.name == webhook_name and hook.user and self.bot.user and hook.user.id == self.bot.user.id:
                    self.game_webhooks[key] = hook
                    return hook
        except discord.HTTPException:
            pass
        try:
            avatar_bytes = None
            if guild.icon:
                try:
                    avatar_bytes = await guild.icon.replace(size=256).read()
                except Exception:
                    avatar_bytes = None
            webhook = await channel.create_webhook(
                name=webhook_name,
                avatar=avatar_bytes,
                reason="Nawaf games webhook",
            )
            self.game_webhooks[key] = webhook
            return webhook
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def game_send(
        self,
        session: Session,
        channel: discord.TextChannel,
        *,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        file: Optional[discord.File] = None,
        view: Optional[discord.ui.View] = None,
    ):
        guild = self.bot.get_guild(session.guild_id)
        webhook = await self.get_game_webhook(guild, channel) if guild else None
        allowed = discord.AllowedMentions(users=True)
        if webhook is not None:
            try:
                return await webhook.send(
                    content=content,
                    embed=embed,
                    file=file,
                    view=view,
                    allowed_mentions=allowed,
                    wait=True,
                )
            except (discord.Forbidden, discord.HTTPException):
                self.game_webhooks.pop((session.guild_id, session.channel_id), None)
        return await channel.send(
            content=content,
            embed=embed,
            file=file,
            view=view,
            allowed_mentions=allowed,
        )

    async def update_lobby(self, session: Session, remaining: int = LOBBY_SECONDS):
        if not session.lobby_message:
            return
        guild = self.bot.get_guild(session.guild_id)
        if not guild:
            return
        try:
            file = self.make_lobby_art(guild, len(session.players), session.max_players)
            embed = discord.Embed()
            embed.set_image(url="attachment://roulette-lobby.png")
            await session.lobby_message.edit(embed=embed, attachments=[file])
        except (discord.HTTPException, discord.Forbidden):
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.strip() == "-روليت":
            await self.start_lobby(message)

    async def start_lobby(self, message: discord.Message):
        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply(
                "❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", mention_author=False
            )
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاينة روليت مفتوحة فهاد الروم.", mention_author=False)

        session = Session(message.guild.id, message.channel.id, message.author.id)
        self.sessions[key] = session
        webhook = await self.get_game_webhook(message.guild, message.channel)
        session.lobby_webhook = webhook

        embed = discord.Embed()
        embed.set_image(url="attachment://roulette-lobby.png")
        view = LobbyView(self, session)
        file = self.make_lobby_art(message.guild, 0, session.max_players)

        if webhook is not None:
            try:
                session.lobby_message = await webhook.send(
                    embed=embed,
                    file=file,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    wait=True,
                )
            except (discord.Forbidden, discord.HTTPException):
                self.game_webhooks.pop(key, None)
                session.lobby_message = await message.channel.send(embed=embed, file=file, view=view)
        else:
            session.lobby_message = await message.channel.send(embed=embed, file=file, view=view)

        try:
            for remaining in range(LOBBY_SECONDS - 1, -1, -1):
                await asyncio.sleep(1)
                if self.sessions.get(key) is not session or session.active:
                    return
                await self.update_lobby(session, remaining)

            if len(session.players) < MIN_PLAYERS:
                self.sessions.pop(key, None)
                await self.game_send(
                    session,
                    message.channel,
                    content=f"❌ سال وقت التسجيل وما وصلناش لـ **{MIN_PLAYERS} لاعبين**.\n**تم إلغاء الروليت.**",
                )
                try:
                    await session.lobby_message.edit(view=None)
                except discord.HTTPException:
                    pass
                return

            session.active = True
            try:
                await session.lobby_message.edit(view=None)
            except discord.HTTPException:
                pass

            await session.lobby_message.reply(
                "**اللاعبين تجهزو اللعبة راح تبدا بعد شوي**",
                mention_author=False,
            )

            try:
                from cogs.premium import is_premium, UPSELL_TEXT
                if not is_premium(session.guild_id):
                    await self.game_send(session, message.channel, content=UPSELL_TEXT)
            except Exception:
                pass

            await asyncio.sleep(1)
            await self.run_game(session, message.channel)
        except asyncio.CancelledError:
            self.sessions.pop(key, None)
            raise

    async def run_game(self, session: Session, channel: discord.TextChannel):
        try:
            while len(session.players) > 2:
                session.round += 1
                selected_id = random.choice(session.players)
                session.decision_event = asyncio.Event()
                session.decision = None

                await self.game_send(
                    session,
                    channel,
                    file=await self.wheel_file(session, selected_id),
                )
                view = DecisionView(self, session, selected_id)
                await self.game_send(
                    session,
                    channel,
                    content=(
                        f"**<@{selected_id}>، اختر الشخص لي بدك تطرده**\n"
                        f"⏳ عندك **{DECISION_SECONDS} ثانية**."
                    ),
                    view=view,
                )
                try:
                    await asyncio.wait_for(session.decision_event.wait(), timeout=DECISION_SECONDS)
                except asyncio.TimeoutError:
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await self.game_send(
                        session,
                        channel,
                        content=f"**تم طرد <@{selected_id}> بسبب الخمول**",
                    )
                    await self.send_elimination_gif(session, channel)
                    await asyncio.sleep(1.5)
                    continue

                action, target_id = session.decision or ("withdraw", None)
                if action == "withdraw":
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await self.game_send(
                        session,
                        channel,
                        content=f"**انسحب <@{selected_id}> من اللعبة، ستبدأ الجولة التالية بعد قليل.**",
                    )
                else:
                    if action == "random":
                        candidates = [uid for uid in session.players if uid != selected_id]
                        if candidates:
                            target_id = random.choice(candidates)
                    candidates = [uid for uid in session.players if uid != selected_id]
                    if target_id not in candidates:
                        if not candidates:
                            break
                        target_id = random.choice(candidates)
                    session.players.remove(target_id)
                    await self.game_send(
                        session,
                        channel,
                        content=f"**تم طرد <@{target_id}> من اللعبة، سيتم بدأ الجولة التالية بعد قليل.**",
                    )
                    await self.send_elimination_gif(session, channel)
                await asyncio.sleep(1.5)

            if len(session.players) == 2:
                winner = random.choice(session.players)
                await self.game_send(
                    session,
                    channel,
                    file=await self.wheel_file(session, winner, final=True),
                )
                await asyncio.sleep(1)
                self.add_points(session.guild_id, winner, WINNER_REWARD)
                await self.game_send(
                    session,
                    channel,
                    content=f"🏆 **الفائز فالروليت هو <@{winner}>!**\n⭐ ربح **{WINNER_REWARD} نقطة**.",
                )
        finally:
            session.active = False
            self.sessions.pop(self.key(session), None)

    @staticmethod
    def add_points(guild_id: int, user_id: int, amount: int):
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)",
                (guild_id, user_id),
            )
            con.execute(
                "UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )

    async def send_elimination_gif(self, session: Session, channel: discord.TextChannel):
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(ELIMINATION_GIF_URL) as response:
                    if response.status == 200:
                        data = await response.read()
                        await self.game_send(
                            session,
                            channel,
                            file=discord.File(io.BytesIO(data), filename="elimination.gif"),
                        )
                        return
        except Exception:
            pass
        await self.game_send(session, channel, content=ELIMINATION_GIF_URL)

    async def avatar(self, member: discord.Member) -> Optional[bytes]:
        try:
            return await member.display_avatar.replace(size=128, static_format="png").read()
        except Exception:
            return None

    async def wheel_file(self, session: Session, selected_id: int, final: bool = False) -> discord.File:
        size = 1000
        image = Image.new("RGB", (size, size), (20, 22, 28))
        draw = ImageDraw.Draw(image)
        cx = cy = size // 2
        radius = 390
        members = [self.member(session.guild_id, uid) for uid in session.players]
        members = [member for member in members if member is not None]
        count = max(1, len(members))
        step = 360 / count
        selected_index = next(
            (i for i, member in enumerate(members) if member.id == selected_id), 0
        )
        rotation = -90 - ((selected_index + 0.5) * step)
        palette = [
            (70, 91, 132),
            (109, 72, 123),
            (64, 125, 105),
            (141, 89, 59),
            (77, 108, 145),
            (125, 75, 94),
        ]
        for i, _member in enumerate(members):
            start = rotation + i * step
            end = start + step
            draw.pieslice(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                start=start,
                end=end,
                fill=palette[i % len(palette)],
                outline=(240, 240, 240),
                width=4,
            )
        draw.ellipse(
            (cx - 100, cy - 100, cx + 100, cy + 100),
            fill=(30, 33, 42),
            outline=(245, 198, 66),
            width=8,
        )
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 27
            )
        except OSError:
            font = ImageFont.load_default()
        for i, member in enumerate(members):
            angle = math.radians(rotation + (i + 0.5) * step)
            distance = 285
            x = int(cx + math.cos(angle) * distance)
            y = int(cy + math.sin(angle) * distance)
            avatar = await self.avatar(member)
            if avatar:
                try:
                    av = Image.open(io.BytesIO(avatar)).convert("RGB").resize((84, 84))
                    mask = Image.new("L", (84, 84), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, 83, 83), fill=255)
                    image.paste(av, (x - 42, y - 42), mask)
                except Exception:
                    pass
            draw.ellipse(
                (x - 45, y - 45, x + 45, y + 45),
                outline=(255, 85, 85) if member.id == selected_id else (255, 255, 255),
                width=6,
            )
            label = self.short_name(member.display_name, 12)
            box = draw.textbbox((0, 0), label, font=font)
            tw = box[2] - box[0]
            ty = y + 50
            draw.rounded_rectangle(
                (x - tw / 2 - 7, ty - 2, x + tw / 2 + 7, ty + 31),
                radius=8,
                fill=(10, 12, 16),
            )
            draw.text((x - tw / 2, ty), label, fill=(255, 255, 255), font=font)
        draw.polygon(
            [(cx, 15), (cx - 32, 85), (cx + 32, 85)],
            fill=(255, 70, 70),
            outline=(255, 230, 230),
        )
        selected_member = next((member for member in members if member.id == selected_id), None)
        result_name = self.short_name(
            selected_member.display_name if selected_member else "Selected", 22
        )
        box = draw.textbbox((0, 0), result_name, font=font)
        tw = box[2] - box[0]
        footer_y = size - 55
        draw.rounded_rectangle(
            (cx - tw / 2 - 16, footer_y - 8, cx + tw / 2 + 16, footer_y + 30),
            radius=12,
            fill=(245, 198, 66),
        )
        draw.text(
            (cx - tw / 2, footer_y),
            result_name,
            fill=(20, 22, 28),
            font=font,
        )
        if final:
            draw.text((25, 25), "FINAL", fill=(245, 198, 66), font=font)
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        buffer.seek(0)
        return discord.File(buffer, filename="roulette-wheel.png")


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteMultiMessage(bot))
