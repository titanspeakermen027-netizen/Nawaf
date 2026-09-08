from __future__ import annotations

import asyncio
import io
import math
import random
from dataclasses import dataclass, field

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
    "line_1787313239426.gif?ex=6a898dd7&is=6a883c57&"
    "hm=d0f114e4e11144e4cb6eca06654f2e963ab10b5032d70f1a8c7a63a9c961a5d5&"
)


def get_server_max(guild_id: int) -> int:
    try:
        from cogs.premium import get_roulette_max
        return get_roulette_max(guild_id)
    except Exception:
        return DEFAULT_MAX_PLAYERS


@dataclass
class Session:
    guild_id: int
    channel_id: int
    starter_id: int
    players: list[int] = field(default_factory=list)
    max_players: int = DEFAULT_MAX_PLAYERS
    active: bool = False
    cancelled: bool = False
    round: int = 0
    lobby_message: discord.Message | None = None
    lobby_webhook: discord.Webhook | None = None
    lobby_task: asyncio.Task | None = None
    decision_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: tuple[str, int | None] | None = None


class LobbyView(discord.ui.View):
    def __init__(self, game: "RouletteMultiMessage", session: Session):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.game = game
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        current = self.game.sessions.get(self.game.key(self.session))
        if current is not self.session or self.session.cancelled or self.session.active:
            await interaction.response.send_message(
                "❌ انتهى وقت التسجيل أو بدأت اللعبة بالفعل.", ephemeral=True
            )
            return False
        if interaction.user.bot:
            await interaction.response.send_message(
                "❌ لا يمكن للبوتات المشاركة.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="دخول إلى اللعبة", style=discord.ButtonStyle.success, emoji="🎮")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.session.players:
            return await interaction.response.send_message(
                "❌ أنت أصلا مشارك بالفعالية", ephemeral=True
            )

        if len(self.session.players) >= self.session.max_players:
            return await interaction.response.send_message(
                f"❌ تم الوصول إلى الحد الأقصى المسموح به: **{self.session.max_players} لاعبًا**.",
                ephemeral=True,
            )

        self.session.players.append(uid)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)

    @discord.ui.button(label="خروج من اللعبة", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.session.players:
            return await interaction.response.send_message(
                "❌ أنت لست مشاركًا في الفعالية.", ephemeral=True
            )

        self.session.players.remove(uid)
        await interaction.response.defer()
        await self.game.update_lobby(self.session)


class DecisionView(discord.ui.View):
    def __init__(self, game: "RouletteMultiMessage", session: Session, selected_id: int):
        super().__init__(timeout=DECISION_SECONDS + 2)
        self.game = game
        self.session = session
        self.selected_id = selected_id
        self.done = False
        targets = [uid for uid in session.players if uid != selected_id]

        if len(targets) <= 20:
            for index, uid in enumerate(targets):
                member = game.member(session.guild_id, uid)
                label = game.short_name(member.display_name if member else str(uid), 18)
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.danger,
                    emoji="🎯",
                    row=index // 5,
                )

                async def callback(
                    interaction: discord.Interaction,
                    target_id: int = uid,
                ):
                    await self.resolve(interaction, "kick", target_id)

                button.callback = callback
                self.add_item(button)
        else:
            select = discord.ui.UserSelect(
                placeholder="اختر لاعبًا لإقصائه",
                min_values=1,
                max_values=1,
                row=0,
            )

            async def select_callback(interaction: discord.Interaction):
                chosen = select.values[0] if select.values else None
                target_id = getattr(chosen, "id", None)
                if target_id not in self.session.players or target_id == self.selected_id:
                    return await interaction.response.send_message(
                        "❌ يجب اختيار لاعب مشارك غير اللاعب الذي اختارته العجلة.",
                        ephemeral=True,
                    )
                await self.resolve(interaction, "kick", target_id)

            select.callback = select_callback
            self.add_item(select)

        random_button = discord.ui.Button(
            label="إقصاء عشوائي",
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
        random_button.callback = self.random_kick
        withdraw_button.callback = self.withdraw
        self.add_item(random_button)
        self.add_item(withdraw_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.session.cancelled or self.game.sessions.get(self.game.key(self.session)) is not self.session:
            await interaction.response.send_message("❌ توقفت اللعبة.", ephemeral=True)
            return False
        if interaction.user.id != self.selected_id:
            await interaction.response.send_message(
                "❌ هذا القرار متاح فقط للاعب الذي اختارته العجلة.",
                ephemeral=True,
            )
            return False
        if self.done:
            await interaction.response.send_message("❌ انتهت مهلة القرار.", ephemeral=True)
            return False
        return True

    async def resolve(
        self,
        interaction: discord.Interaction,
        action: str,
        target_id: int | None = None,
    ):
        if self.done or self.session.cancelled:
            return

        if action == "kick" and (
            target_id is None
            or target_id not in self.session.players
            or target_id == self.selected_id
        ):
            return await interaction.response.send_message(
                "❌ يجب اختيار لاعب مشارك غير اللاعب الذي اختارته العجلة.",
                ephemeral=True,
            )

        self.done = True
        self.session.decision = (action, target_id)
        self.session.decision_event.set()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def random_kick(self, interaction: discord.Interaction):
        await self.resolve(interaction, "random")

    async def withdraw(self, interaction: discord.Interaction):
        await self.resolve(interaction, "withdraw")


class RouletteMultiMessage(commands.Cog):
    """احتفالية إقصاء جماعية بأسلوب واجهة نظيفة ومنظمة."""

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

    def member(self, guild_id: int, user_id: int) -> discord.Member | None:
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild else None

    @staticmethod
    def _font(size: int, bold: bool = False):
        path = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    def build_lobby_embed(self, session: Session, guild: discord.Guild) -> discord.Embed:
        mentions = []
        for uid in session.players:
            member = guild.get_member(uid)
            if member and not member.bot:
                mentions.append(f"- {member.mention}")

        participants = "\n".join(mentions) if mentions else "- لا يوجد مشاركون حتى الآن."
        expires_at = int(asyncio.get_running_loop().time()) + 1

        description = (
            "**شرح الفعالية:**\n"
            "1- اضغط على **دخول إلى اللعبة** للمشاركة.\n"
            "2- تختار العجلة لاعبًا عشوائيًا في كل جولة.\n"
            "3- اللاعب المختار يستطيع إقصاء لاعب آخر، أو اختيار الإقصاء العشوائي، أو الانسحاب.\n"
            "4- تستمر الجولات حتى تبقى المواجهة النهائية ويتم إعلان الفائز.\n\n"
            f"**عدد المشاركين ({len(session.players)}/{session.max_players}):**\n\n"
            f"{participants}\n\n"
            "⏳ يبدأ التسجيل تلقائيًا بعد **30 ثانية**."
        )

        embed = discord.Embed(
            title=guild.name,
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Nawaf • فعالية إقصاء جماعية")
        return embed

    async def get_game_webhook(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> discord.Webhook | None:
        key = (guild.id, channel.id)
        cached = self.game_webhooks.get(key)
        if cached is not None:
            return cached

        webhook_name = self.short_name(guild.name, 80)
        try:
            hooks = await channel.webhooks()
            for hook in hooks:
                if (
                    hook.name == webhook_name
                    and hook.user
                    and self.bot.user
                    and hook.user.id == self.bot.user.id
                ):
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
                    pass

            webhook = await channel.create_webhook(
                name=webhook_name,
                avatar=avatar_bytes,
                reason="Nawaf group game webhook",
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
        content: str | None = None,
        embed: discord.Embed | None = None,
        file: discord.File | None = None,
        view: discord.ui.View | None = None,
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
        if not session.lobby_message or session.cancelled:
            return

        guild = self.bot.get_guild(session.guild_id)
        if not guild:
            return

        try:
            embed = self.build_lobby_embed(session, guild)
            view = getattr(session, "lobby_view", None)
            await session.lobby_message.edit(embed=embed, view=view)
        except (discord.HTTPException, discord.Forbidden):
            pass

    async def cancel_session(self, session: Session):
        if session.cancelled:
            return
        session.cancelled = True
        session.active = False
        session.decision = None
        session.decision_event.set()
        task = session.lobby_task
        current = asyncio.current_task()
        if task and task is not current and not task.done():
            task.cancel()
        self.sessions.pop(self.key(session), None)

    async def start_lobby(self, message: discord.Message):
        if not message.guild or not isinstance(message.channel, discord.TextChannel):
            return

        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply(
                "❌ هذه القناة غير مسموح فيها بالفعاليات الجماعية.",
                mention_author=False,
            )

        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply(
                "❌ توجد فعالية روليت مفتوحة بالفعل في هذه القناة.",
                mention_author=False,
            )

        session = Session(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            starter_id=message.author.id,
            max_players=get_server_max(message.guild.id),
        )
        session.lobby_task = asyncio.current_task()
        session.lobby_view = LobbyView(self, session)
        self.sessions[key] = session

        webhook = await self.get_game_webhook(message.guild, message.channel)
        session.lobby_webhook = webhook

        embed = self.build_lobby_embed(session, message.guild)
        if webhook is not None:
            try:
                session.lobby_message = await webhook.send(
                    embed=embed,
                    view=session.lobby_view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    wait=True,
                )
            except (discord.Forbidden, discord.HTTPException):
                self.game_webhooks.pop(key, None)

        if session.lobby_message is None:
            session.lobby_message = await message.channel.send(
                embed=embed,
                view=session.lobby_view,
            )

        try:
            for _ in range(LOBBY_SECONDS):
                await asyncio.sleep(1)
                if session.cancelled or self.sessions.get(key) is not session:
                    return

            session.active = True
            if len(session.players) < MIN_PLAYERS:
                self.sessions.pop(key, None)
                session.cancelled = True
                await session.lobby_message.edit(view=None)
                return await self.game_send(
                    session,
                    message.channel,
                    content=(
                        f"❌ انتهى وقت التسجيل ولم يكتمل الحد الأدنى وهو **{MIN_PLAYERS} لاعبين**.\n"
                        "تم إلغاء الفعالية."
                    ),
                )

            try:
                await session.lobby_message.edit(view=None)
            except discord.HTTPException:
                pass

            try:
                from cogs.premium import UPSELL_TEXT, is_premium
                if not is_premium(session.guild_id):
                    await self.game_send(session, message.channel, content=UPSELL_TEXT)
            except Exception:
                pass

            await asyncio.sleep(1)
            await self.run_game(session, message.channel)

        except asyncio.CancelledError:
            session.cancelled = True
            self.sessions.pop(key, None)
        finally:
            if self.sessions.get(key) is session:
                self.sessions.pop(key, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        content = message.content.strip()
        if content == "-روليت":
            await self.start_lobby(message)
            return

        if content == "-توقيف":
            session = self.sessions.get((message.guild.id, message.channel.id))
            if session is None:
                return
            await self.cancel_session(session)
            await message.channel.send(
                "✅ تم إيقاف الفعالية الحالية دون حذف أي رسالة."
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        for session in list(self.sessions.values()):
            if session.channel_id != payload.channel_id:
                continue
            if not session.lobby_message or session.lobby_message.id != payload.message_id:
                continue
            if session.active or session.cancelled:
                continue

            await self.cancel_session(session)
            channel = self.bot.get_channel(payload.channel_id)
            if channel is not None:
                await channel.send(
                    "⚠️ تم إلغاء الفعالية لأن رسالة التسجيل تم حذفها."
                )
            break

    async def run_game(self, session: Session, channel: discord.TextChannel):
        try:
            while len(session.players) > 2 and not session.cancelled:
                session.round += 1
                selected_id = random.choice(session.players)
                session.decision_event = asyncio.Event()
                session.decision = None

                await self.game_send(
                    session,
                    channel,
                    content=(
                        f"🎰 **الجولة {session.round}**\n"
                        f"العجلة اختارت <@{selected_id}>."
                    ),
                    file=await self.wheel_file(session, selected_id),
                )

                view = DecisionView(self, session, selected_id)
                await self.game_send(
                    session,
                    channel,
                    content=(
                        f"<@{selected_id}>، اختر لاعبًا لإقصائه، أو اختر **إقصاء عشوائي**، أو **انسحاب**.\n"
                        f"⏳ لديك **{DECISION_SECONDS} ثانية**."
                    ),
                    view=view,
                )

                try:
                    await asyncio.wait_for(
                        session.decision_event.wait(),
                        timeout=DECISION_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await self.game_send(
                        session,
                        channel,
                        content=(
                            f"⏰ انتهى الوقت، وتم إقصاء <@{selected_id}> بسبب عدم اتخاذ قرار."
                        ),
                    )
                    await self.send_elimination_gif(session, channel)
                    await asyncio.sleep(1.5)
                    continue

                if session.cancelled:
                    return

                action, target_id = session.decision or ("withdraw", None)

                if action == "withdraw":
                    if selected_id in session.players:
                        session.players.remove(selected_id)
                    await self.game_send(
                        session,
                        channel,
                        content=f"🚪 انسحب <@{selected_id}> من الفعالية."
                    )
                else:
                    if action == "random":
                        candidates = [uid for uid in session.players if uid != selected_id]
                        target_id = random.choice(candidates) if candidates else None

                    candidates = [uid for uid in session.players if uid != selected_id]
                    if target_id not in candidates:
                        if not candidates:
                            return
                        target_id = random.choice(candidates)

                    session.players.remove(target_id)
                    await self.game_send(
                        session,
                        channel,
                        content=(
                            f"❌ تم إقصاء <@{target_id}> من الفعالية.\n"
                            f"👥 المتبقون: **{len(session.players)}**"
                        ),
                    )
                    await self.send_elimination_gif(session, channel)

                await asyncio.sleep(1.5)

            if session.cancelled:
                return

            if len(session.players) == 2:
                winner = random.choice(session.players)
                await self.game_send(
                    session,
                    channel,
                    content="🏁 **المواجهة النهائية**\nتُحسم الفعالية الآن...",
                )
                await asyncio.sleep(1)
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
                    content=(
                        f"🏆 **الفائز في الفعالية هو <@{winner}>!**\n"
                        f"⭐ حصل على **{WINNER_REWARD} نقاط**."
                    ),
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
                            file=discord.File(
                                io.BytesIO(data),
                                filename="elimination.gif",
                            ),
                        )
                        return
        except Exception:
            pass

    async def avatar(self, member: discord.Member) -> bytes | None:
        try:
            return await member.display_avatar.replace(
                size=128,
                static_format="png",
            ).read()
        except Exception:
            return None

    async def wheel_file(
        self,
        session: Session,
        selected_id: int,
        final: bool = False,
    ) -> discord.File:
        size = 1000
        image = Image.new("RGB", (size, size), (20, 22, 28))
        draw = ImageDraw.Draw(image)
        cx = cy = size // 2
        radius = 390
        members = [
            self.member(session.guild_id, uid)
            for uid in session.players
        ]
        members = [m for m in members if m is not None]
        count = max(1, len(members))
        step = 360 / count
        selected_index = next(
            (i for i, member in enumerate(members) if member.id == selected_id),
            0,
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

        for index in range(count):
            start = rotation + index * step
            end = start + step
            draw.pieslice(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                start=start,
                end=end,
                fill=palette[index % len(palette)],
                outline=(240, 240, 240),
                width=4,
            )

        selected_member = next(
            (member for member in members if member.id == selected_id),
            None,
        )

        font = self._font(27, bold=True)
        if count <= 40:
            for index, member in enumerate(members):
                angle = math.radians(rotation + (index + 0.5) * step)
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
                    outline=(255, 215, 64) if member.id == selected_id else (255, 255, 255),
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
        else:
            info = f"{count} لاعبين"
            box = draw.textbbox((0, 0), info, font=font)
            draw.text(
                (cx - (box[2] - box[0]) / 2, cy + radius + 35),
                info,
                fill=(255, 255, 255),
                font=font,
            )

        draw.ellipse(
            (cx - 100, cy - 100, cx + 100, cy + 100),
            fill=(30, 33, 42),
            outline=(245, 198, 66),
            width=8,
        )
        draw.polygon(
            [(cx, 15), (cx - 32, 85), (cx + 32, 85)],
            fill=(255, 70, 70),
            outline=(255, 230, 230),
        )

        result_name = self.short_name(
            selected_member.display_name if selected_member else "اللاعب المختار",
            22,
        )
        result_box = draw.textbbox((0, 0), result_name, font=font)
        result_width = result_box[2] - result_box[0]
        footer_y = size - 55
        draw.rounded_rectangle(
            (
                cx - result_width / 2 - 16,
                footer_y - 8,
                cx + result_width / 2 + 16,
                footer_y + 30,
            ),
            radius=12,
            fill=(245, 198, 66),
        )
        draw.text(
            (cx - result_width / 2, footer_y),
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
