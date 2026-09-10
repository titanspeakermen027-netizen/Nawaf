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

from cogs.access_control import can_manage_events
from cogs.game_channels import is_group_game_channel_allowed
from cogs.premium import get_roulette_max
from cogs.points import add_category_points

MIN_PLAYERS = 4
LOBBY_SECONDS = 30
DECISION_SECONDS = 15
PLAYERS_PER_PAGE = 10
WINNER_REWARD = 5

ELIMINATION_GIF_URL = (
    "https://cdn.discordapp.com/attachments/1476446187656708178/1540328111667941416/"
    "line_1787313239426.gif?ex=6a898dd7&is=6a883c57&hm=d0f114e4e11144e4cb6eca06654f2e963ab10b5032d70f1a8c7a63a9c961a5d5&"
)


def get_server_max(guild_id: int) -> int:
    return get_roulette_max(guild_id)


def add_roulette_points(guild_id: int, user_id: int, amount: int) -> None:
    add_category_points(guild_id, user_id, amount, "roulette")


@dataclass
class Session:
    guild_id: int
    channel_id: int
    starter_id: int
    players: list[int] = field(default_factory=list)
    max_players: int = 12
    active: bool = False
    cancelled: bool = False
    round: int = 0
    lobby_message: discord.WebhookMessage | None = None
    lobby_view: "LobbyView | None" = None
    lobby_task: asyncio.Task | None = None
    game_task: asyncio.Task | None = None
    decision_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: tuple[str, int | None] | None = None


class LobbyView(discord.ui.View):
    def __init__(self, game: "RouletteMultiMessage", session: Session):
        super().__init__(timeout=LOBBY_SECONDS + 10)
        self.game = game
        self.session = session
        self.page = 0

    def pages(self) -> int:
        return max(1, math.ceil(len(self.session.players) / PLAYERS_PER_PAGE))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.game.sessions.get(self.game.key(self.session)) is not self.session or self.session.cancelled or self.session.active:
            await interaction.response.send_message("❌ انتهى التسجيل أو بدأت اللعبة بالفعل.", ephemeral=True)
            return False
        if interaction.user.bot:
            await interaction.response.send_message("❌ البوتات لا يمكنها المشاركة.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="دخول إلى اللعبة", style=discord.ButtonStyle.success, emoji="🎮", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.session.players:
            return await interaction.response.send_message("❌ أنت أصلا مشارك بالفعالية", ephemeral=True)
        if len(self.session.players) >= get_server_max(self.session.guild_id):
            return await interaction.response.send_message(
                f"❌ وصلنا للحد الأقصى ديال **{get_server_max(self.session.guild_id)} لاعب**.", ephemeral=True
            )
        self.session.players.append(interaction.user.id)
        self.page = self.pages() - 1
        await interaction.response.defer()
        await self.game.update_lobby(self.session, self.page)

    @discord.ui.button(label="خروج من اللعبة", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.session.players:
            return await interaction.response.send_message("❌ أنت لست مشاركًا في الفعالية.", ephemeral=True)
        self.session.players.remove(interaction.user.id)
        self.page = min(self.page, self.pages() - 1)
        await interaction.response.defer()
        await self.game.update_lobby(self.session, self.page)

    @discord.ui.button(label="السابق", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page <= 0:
            return await interaction.response.send_message("❌ هذه أول صفحة.", ephemeral=True)
        self.page -= 1
        await interaction.response.defer()
        await self.game.update_lobby(self.session, self.page)

    @discord.ui.button(label="التالي", style=discord.ButtonStyle.secondary, emoji="▶️", row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page >= self.pages() - 1:
            return await interaction.response.send_message("❌ هذه آخر صفحة.", ephemeral=True)
        self.page += 1
        await interaction.response.defer()
        await self.game.update_lobby(self.session, self.page)

    @discord.ui.button(label="بدء", style=discord.ButtonStyle.primary, emoji="🎰", row=1)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يبدأ الفعالية.", ephemeral=True)
        if len(self.session.players) < MIN_PLAYERS:
            return await interaction.response.send_message(f"❌ خاص على الأقل **{MIN_PLAYERS}** لاعبين.", ephemeral=True)
        if self.session.active:
            return await interaction.response.send_message("⚠️ الفعالية خدامة دابا.", ephemeral=True)
        self.session.active = True
        if self.session.lobby_task and not self.session.lobby_task.done():
            self.session.lobby_task.cancel()
        self.stop()
        await interaction.response.defer()
        self.session.game_task = asyncio.create_task(self.game.run_game(self.session, interaction.channel))

    @discord.ui.button(label="توقيف", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def stop_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.stop_from_interaction(interaction, self.session)


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
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.danger, emoji="🎯", row=index // 5)
                async def callback(interaction: discord.Interaction, target_id: int = uid):
                    await self.resolve(interaction, "kick", target_id)
                button.callback = callback
                self.add_item(button)
        else:
            select = discord.ui.UserSelect(placeholder="اختر اللاعب الذي تريد إقصاءه", min_values=1, max_values=1, row=0)
            async def select_callback(interaction: discord.Interaction):
                chosen = select.values[0] if select.values else None
                target_id = getattr(chosen, "id", None)
                if target_id not in self.session.players or target_id == self.selected_id:
                    return await interaction.response.send_message("❌ اختر لاعبًا مشاركًا غير اللاعب المختار.", ephemeral=True)
                await self.resolve(interaction, "kick", target_id)
            select.callback = select_callback
            self.add_item(select)
        random_button = discord.ui.Button(label="إقصاء عشوائي", style=discord.ButtonStyle.primary, emoji="🎲", row=4)
        withdraw_button = discord.ui.Button(label="انسحاب", style=discord.ButtonStyle.secondary, emoji="🚪", row=4)
        random_button.callback = self.random_kick
        withdraw_button.callback = self.withdraw
        self.add_item(random_button)
        self.add_item(withdraw_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.session.cancelled or self.game.sessions.get(self.game.key(self.session)) is not self.session:
            await interaction.response.send_message("❌ توقفت اللعبة.", ephemeral=True)
            return False
        if interaction.user.id != self.selected_id:
            await interaction.response.send_message("❌ هذا القرار متاح فقط للاعب الذي اختارته العجلة.", ephemeral=True)
            return False
        return True

    async def resolve(self, interaction: discord.Interaction, action: str, target_id: int | None = None):
        if self.done:
            return
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
    """Professional non-gambling elimination roulette with server-named webhook."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], Session] = {}
        self.webhooks: dict[tuple[int, int], discord.Webhook] = {}

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

    def lobby_art(self, guild: discord.Guild, players: int, maximum: int) -> discord.File:
        width, height = 1200, 675
        image = Image.new("RGB", (width, height), (18, 16, 23))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((45, 45, width - 45, height - 45), radius=42, fill=(28, 24, 35), outline=(120, 74, 210), width=5)
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 112)
            server_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
            count_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 78)
        except OSError:
            title_font = server_font = count_font = ImageFont.load_default()
        def centered(text, y, font, fill):
            box = draw.textbbox((0, 0), text, font=font)
            draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)
        centered("روليت", 105, title_font, (250, 250, 252))
        centered(self.short_name(guild.name, 34), 270, server_font, (216, 207, 228))
        count_text = f"{players} / {maximum}"
        box = draw.textbbox((0, 0), count_text, font=count_font)
        count_w = box[2] - box[0]
        draw.rounded_rectangle(((width-count_w)/2-44,402,(width+count_w)/2+44,532), radius=34, fill=(20,18,27), outline=(245,245,248), width=4)
        centered(count_text, 420, count_font, (255,255,255))
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        buffer.seek(0)
        return discord.File(buffer, filename="roulette-lobby.png")

    def lobby_content(self, session: Session, page: int = 0, remaining: int = LOBBY_SECONDS) -> str:
        total_pages = max(1, math.ceil(len(session.players) / PLAYERS_PER_PAGE))
        page = max(0, min(page, total_pages - 1))
        start = page * PLAYERS_PER_PAGE
        guild = self.bot.get_guild(session.guild_id)
        rows = []
        for pos, uid in enumerate(session.players[start:start+PLAYERS_PER_PAGE], start=start+1):
            member = guild.get_member(uid) if guild else None
            rows.append(f"**{pos}.** {member.name if member else uid} • <@{uid}>")
        players = "\n".join(rows) if rows else "—"
        return (
            "**🎰 روليت**\n\n"
            f"**عدد المشاركين:** `{len(session.players)} / {session.max_players}`\n"
            f"**الحد الأدنى:** `{MIN_PLAYERS}`\n"
            f"**الوقت المتبقي:** `{max(0, remaining)} ثانية`\n\n"
            f"**المشاركون:**\n{players}\n\n"
            f"**صفحة:** `{page+1}/{total_pages}`"
        )

    async def get_webhook(self, guild: discord.Guild, channel: discord.TextChannel) -> discord.Webhook | None:
        key = (guild.id, channel.id)
        if key in self.webhooks:
            return self.webhooks[key]
        try:
            for hook in await channel.webhooks():
                if hook.name == self.short_name(guild.name, 80) and hook.user and self.bot.user and hook.user.id == self.bot.user.id:
                    self.webhooks[key] = hook
                    return hook
        except discord.HTTPException:
            pass
        try:
            avatar = None
            if guild.icon:
                with contextlib.suppress(Exception):
                    avatar = await guild.icon.replace(size=256).read()
            hook = await channel.create_webhook(name=self.short_name(guild.name, 80), avatar=avatar, reason="Nawaf roulette")
            self.webhooks[key] = hook
            return hook
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def send(self, session: Session, channel: discord.TextChannel, **kwargs):
        hook = await self.get_webhook(channel.guild, channel)
        if hook:
            try:
                return await hook.send(allowed_mentions=discord.AllowedMentions(users=True), wait=True, **kwargs)
            except (discord.Forbidden, discord.HTTPException):
                self.webhooks.pop((session.guild_id, session.channel_id), None)
        return await channel.send(allowed_mentions=discord.AllowedMentions(users=True), **kwargs)

    async def update_lobby(self, session: Session, page: int = 0, remaining: int = LOBBY_SECONDS):
        if not session.lobby_message:
            return
        guild = self.bot.get_guild(session.guild_id)
        if not guild:
            return
        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
            file = self.lobby_art(guild, len(session.players), session.max_players)
            await session.lobby_message.edit(content=self.lobby_content(session, page, remaining), attachments=[file], view=session.lobby_view)

    async def start_lobby(self, message, internal: bool = False):
        if not message.guild:
            return
        if not internal and (not isinstance(message.author, discord.Member) or not can_manage_events(message.author)):
            return await message.reply("❌ غير الإدارة أو رئيس الفعاليات يقدر يسوي الفعاليات.", mention_author=False)
        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply("❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", mention_author=False)
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاينة روليت مفتوحة فهاد الروم.", mention_author=False)
        session = Session(message.guild.id, message.channel.id, getattr(message.author, "id", message.guild.owner_id), max_players=get_server_max(message.guild.id))
        session.players.append(getattr(message.author, "id", message.guild.owner_id))
        self.sessions[key] = session
        session.lobby_view = LobbyView(self, session)
        webhook = await self.get_webhook(message.guild, message.channel)
        if webhook:
            try:
                session.lobby_message = await webhook.send(content=self.lobby_content(session, 0, LOBBY_SECONDS), file=self.lobby_art(message.guild, len(session.players), session.max_players), view=session.lobby_view, allowed_mentions=discord.AllowedMentions(users=True), wait=True)
            except (discord.Forbidden, discord.HTTPException):
                session.lobby_message = None
        if session.lobby_message is None:
            session.lobby_message = await message.channel.send(content=self.lobby_content(session,0,LOBBY_SECONDS), file=self.lobby_art(message.guild,len(session.players),session.max_players), view=session.lobby_view)
        session.lobby_task = asyncio.create_task(self.lobby_countdown(session))

    async def lobby_countdown(self, session: Session):
        try:
            for remaining in range(LOBBY_SECONDS-1, -1, -1):
                await asyncio.sleep(1)
                if session.cancelled or session.active or self.sessions.get(self.key(session)) is not session:
                    return
                if remaining <= 5 or remaining % 5 == 0:
                    await self.update_lobby(session, session.lobby_view.page if session.lobby_view else 0, remaining)
            if len(session.players) < MIN_PLAYERS:
                self.sessions.pop(self.key(session), None)
                await self.send(session, self.bot.get_channel(session.channel_id), content=f"❌ انتهى وقت التسجيل ولم يصل العدد إلى **{MIN_PLAYERS}** لاعبين.\n**تم إلغاء الروليت.**")
                with contextlib.suppress(discord.HTTPException): await session.lobby_message.edit(view=None)
                return
            session.active = True
            if session.lobby_view: session.lobby_view.stop()
            await self.send(session, self.bot.get_channel(session.channel_id), content="**اللاعبين تجهزو اللعبة راح تبدا بعد شوي**")
            await asyncio.sleep(1)
            session.game_task = asyncio.create_task(self.run_game(session, self.bot.get_channel(session.channel_id)))
            await session.game_task
        except asyncio.CancelledError:
            return
        finally:
            current = self.sessions.get(self.key(session))
            if current is session and not session.active:
                self.sessions.pop(self.key(session), None)

    async def run_game(self, session: Session, channel: discord.TextChannel):
        try:
            while len(session.players) > 2 and not session.cancelled:
                session.round += 1
                selected_id = random.choice(session.players)
                session.decision_event = asyncio.Event()
                session.decision = None
                view = DecisionView(self, session, selected_id)
                await self.send(session, channel, file=self.wheel_file(session, selected_id))
                await self.send(session, channel, content=f"**<@{selected_id}>، اختر الشخص لي بدك تطرده**\n⏳ عندك **{DECISION_SECONDS} ثانية**.", view=view)
                try:
                    await asyncio.wait_for(session.decision_event.wait(), timeout=DECISION_SECONDS)
                except asyncio.TimeoutError:
                    if selected_id in session.players: session.players.remove(selected_id)
                    await self.send(session, channel, content=f"**تم طرد <@{selected_id}> بسبب الخمول**")
                    await asyncio.sleep(0.6)
                    continue
                if session.cancelled: return
                action, target = session.decision or ("withdraw", None)
                if action == "withdraw":
                    if selected_id in session.players: session.players.remove(selected_id)
                    await self.send(session, channel, content=f"**انسحب <@{selected_id}> من اللعبة، ستبدأ الجولة التالية بعد قليل.**")
                else:
                    candidates=[uid for uid in session.players if uid!=selected_id]
                    if action=="random" or target not in candidates:
                        target=random.choice(candidates) if candidates else None
                    if target is None: break
                    session.players.remove(target)
                    await self.send(session, channel, content=f"**تم طرد <@{target}> من اللعبة، سيتم بدأ الجولة التالية بعد قليل.**")
                await asyncio.sleep(0.8)
            if session.cancelled: return
            if len(session.players)==2:
                winner=random.choice(session.players)
                await self.send(session, channel, file=self.wheel_file(session,winner,final=True))
                await asyncio.sleep(1)
                add_roulette_points(session.guild_id,winner,WINNER_REWARD)
                await self.send(session,channel,content=f"🏆 **الفائز فالروليت هو <@{winner}>!**\n⭐ ربح **{WINNER_REWARD} نقطة**.")
        finally:
            session.active=False
            self.sessions.pop(self.key(session),None)

    def wheel_file(self, session: Session, selected_id: int, final: bool = False) -> discord.File:
        members=[self.member(session.guild_id,uid) for uid in session.players]
        members=[m for m in members if m is not None]
        size=1000; image=Image.new("RGB",(size,size),(20,22,28)); draw=ImageDraw.Draw(image); cx=cy=size//2; radius=390; count=max(1,len(members)); step=360/count
        selected_index=next((i for i,m in enumerate(members) if m.id==selected_id),0); rotation=-90-((selected_index+0.5)*step)
        palette=[(70,91,132),(109,72,123),(64,125,105),(141,89,59),(77,108,145),(125,75,94)]
        for i,_ in enumerate(members): draw.pieslice((cx-radius,cy-radius,cx+radius,cy+radius),start=rotation+i*step,end=rotation+(i+1)*step,fill=palette[i%len(palette)],outline=(240,240,240),width=4)
        draw.ellipse((cx-100,cy-100,cx+100,cy+100),fill=(30,33,42),outline=(245,198,66),width=8)
        try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",27)
        except OSError: font=ImageFont.load_default()
        for i,m in enumerate(members):
            angle=math.radians(rotation+(i+0.5)*step); x=int(cx+math.cos(angle)*285); y=int(cy+math.sin(angle)*285)
            draw.ellipse((x-45,y-45,x+45,y+45),outline=(255,85,85) if m.id==selected_id else (255,255,255),width=6)
            label=self.short_name(m.display_name,12); box=draw.textbbox((0,0),label,font=font); tw=box[2]-box[0]
            draw.rounded_rectangle((x-tw/2-7,y+48,x+tw/2+7,y+81),radius=8,fill=(10,12,16)); draw.text((x-tw/2,y+50),label,fill=(255,255,255),font=font)
        draw.polygon([(cx,15),(cx-32,85),(cx+32,85)],fill=(255,70,70),outline=(255,230,230))
        selected=self.member(session.guild_id,selected_id); result_name=self.short_name(selected.display_name if selected else "Selected",22); box=draw.textbbox((0,0),result_name,font=font); tw=box[2]-box[0]
        draw.rounded_rectangle((cx-tw/2-16,size-63,cx+tw/2+16,size-25),radius=12,fill=(245,198,66)); draw.text((cx-tw/2,size-55),result_name,fill=(20,22,28),font=font)
        if final: draw.text((25,25),"FINAL",fill=(245,198,66),font=font)
        buf=io.BytesIO(); image.save(buf,"PNG",optimize=True); buf.seek(0); return discord.File(buf,filename="roulette-wheel.png")

    async def stop_from_interaction(self, interaction: discord.Interaction, session: Session):
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            return await interaction.response.send_message("❌ غير الإدارة أو رئيس الفعاليات يقدر يوقف الفعالية.", ephemeral=True)
        await interaction.response.defer()
        await self.cancel_session(session, interaction.channel, notify=True)

    async def cancel_session(self, session: Session, channel: discord.TextChannel | None = None, notify: bool = True):
        session.cancelled=True
        for task in (session.lobby_task, session.game_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await task
        self.sessions.pop(self.key(session),None)
        if session.lobby_message:
            with contextlib.suppress(discord.HTTPException): await session.lobby_message.edit(content="**⏹️ تم إيقاف اللعبة**",attachments=[],view=None)
        if notify and channel:
            await self.send(session,channel,content="**تم إيقاف اللعبة**")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        content=message.content.strip()
        if content=="-روليت": await self.start_lobby(message)
        elif content=="-توقيف":
            session=self.sessions.get((message.guild.id,message.channel.id))
            if session: await self.cancel_session(session,message.channel,notify=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        for session in list(self.sessions.values()):
            if session.lobby_message and session.lobby_message.id==payload.message_id and session.channel_id==payload.channel_id and not session.active:
                channel=self.bot.get_channel(payload.channel_id)
                await self.cancel_session(session,channel,notify=True)
                if channel: await channel.send("⚠️ تم إلغاء الفعالية لأن رسالة التسجيل تم حذفها.")
                break


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteMultiMessage(bot))
