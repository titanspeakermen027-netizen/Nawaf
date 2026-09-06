from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from database import connect, get_config
from cogs.transaction_verify import build_code_image, generate_code

MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def resolve_member(guild: discord.Guild, token: str, message: discord.Message) -> discord.Member | None:
    token = token.strip()
    if message.mentions:
        return message.mentions[0]
    match = MENTION_RE.fullmatch(token)
    raw_id = match.group(1) if match else token
    if raw_id.isdigit():
        return guild.get_member(int(raw_id))
    return None


class RatingView(discord.ui.View):
    def __init__(self, channel_id: int, owner_id: int, max_rating: int):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.max_rating = max_rating
        self.selected = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ هاد التقييم مخصص لصاحب التذكرة.", ephemeral=True)
            return False
        return True

    @discord.ui.select(
        placeholder="اختار التقييم من 1 إلى 10",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label=str(i), value=str(i), emoji="⭐") for i in range(1, 11)],
    )
    async def rating(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = int(select.values[0])
        if value > self.max_rating:
            return await interaction.response.send_message(f"❌ التقييم الأقصى هو {self.max_rating}.", ephemeral=True)
        self.selected = value
        await interaction.response.send_modal(RatingNoteModal(self))


class RatingNoteModal(discord.ui.Modal):
    def __init__(self, view: RatingView):
        super().__init__(title="تقييم التذكرة")
        self.view_ref = view
        self.note = discord.ui.TextInput(
            label="ملاحظتك",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            placeholder="كتب ملاحظتك على تجربة الدعم...",
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        view = self.view_ref
        with connect() as con:
            row = con.execute(
                "SELECT owner_id, closed_by, rating FROM tickets WHERE channel_id=?",
                (view.channel_id,),
            ).fetchone()
            if not row or row["owner_id"] != interaction.user.id or not row["closed_by"]:
                return await interaction.response.send_message("❌ لا يمكنك تقييم هذه التذكرة.", ephemeral=True)
            if row["rating"] is not None:
                return await interaction.response.send_message("⚠️ سبق لك تقييم هذه التذكرة.", ephemeral=True)
            con.execute(
                "UPDATE tickets SET rating=?, note=? WHERE channel_id=?",
                (view.selected, self.note.value.strip() or None, view.channel_id),
            )
        await interaction.response.send_message("⭐ تم حفظ تقييمك وملاحظتك بنجاح.", ephemeral=True)


class PrefixSystems(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.transfer_locks: set[tuple[int, int]] = set()

    async def _reply(self, message: discord.Message, content: str, **kwargs):
        kwargs.setdefault("mention_author", False)
        return await message.reply(content, **kwargs)

    async def handle_c(self, message: discord.Message):
        content = message.content.strip()
        if not re.match(r"(?i)^c(?:\s|$)", content):
            return False
        tail = content[1:].strip()
        if not tail:
            await self.show_balance(message, message.author)
            return True
        parts = tail.split()
        if len(parts) == 1:
            member = resolve_member(message.guild, parts[0], message)
            if not member:
                await self._reply(message, "❌ العضو غير موجود. استعمل `C` أو `C @user` أو `C id`.")
                return True
            await self.show_balance(message, member)
            return True
        if len(parts) == 2:
            member = resolve_member(message.guild, parts[0], message)
            try:
                amount = int(parts[1].replace(",", ""))
            except ValueError:
                amount = 0
            if not member or amount <= 0:
                await self._reply(message, "❌ الاستعمال: `C @user المبلغ` أو `C id المبلغ`.")
                return True
            await self.start_transfer(message, member, amount)
            return True
        await self._reply(message, "❌ الاستعمال: `C` — `C @user` — `C @user المبلغ`.")
        return True

    async def show_balance(self, message: discord.Message, member: discord.Member):
        with connect() as con:
            row = con.execute("SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (message.guild.id, member.id)).fetchone()
            balance = row["balance"] if row else 0
        await self._reply(message, f"**{member.name}،رصيدك الحالي هو `{balance}$`.🏦**")

    async def start_transfer(self, message: discord.Message, target: discord.Member, amount: int):
        guild = message.guild
        sender = message.author
        key = (guild.id, sender.id)
        if key in self.transfer_locks:
            await self._reply(message, "❌ عندك عملية تحويل قيد التحقق بالفعل. كملها أو خليها تنتهي بعد 60 ثانية.")
            return
        if target.bot or target.id == sender.id:
            await self._reply(message, "❌ ما يمكنش تحول لنفسك أو لبوت.")
            return
        with connect() as con:
            row = con.execute("SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (guild.id, sender.id)).fetchone()
            sender_balance = row["balance"] if row else 0
        if sender_balance < amount:
            await self._reply(message, "❌ رصيدك غير كافي لإتمام التحويل.")
            return
        self.transfer_locks.add(key)
        verification_message = None
        try:
            code = generate_code()
            file = build_code_image(code)
            verification_message = await message.reply("🔐 **تحقق من عملية التحويل**\nاكتب الأرقام اللي فالصورة هنا خلال **60 ثانية**.", file=file, mention_author=False)
            deadline = asyncio.get_running_loop().time() + 60
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                def check(candidate: discord.Message):
                    return candidate.author.id == sender.id and candidate.channel.id == message.channel.id and not candidate.author.bot
                try:
                    candidate = await self.bot.wait_for("message", timeout=remaining, check=check)
                except asyncio.TimeoutError:
                    break
                if candidate.content.strip() == code:
                    with connect() as con:
                        sender_row = con.execute("SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (guild.id, sender.id)).fetchone()
                        live_balance = sender_row["balance"] if sender_row else 0
                        if live_balance < amount:
                            with contextlib.suppress(discord.HTTPException):
                                await candidate.delete()
                            await self._reply(message, "❌ الرصيد تبدل وما بقاش كافي لإتمام التحويل.")
                            return
                        con.execute("INSERT OR IGNORE INTO balances(guild_id,user_id,balance) VALUES(?,?,0)", (guild.id, target.id))
                        con.execute("UPDATE balances SET balance=balance-? WHERE guild_id=? AND user_id=?", (amount, guild.id, sender.id))
                        con.execute("UPDATE balances SET balance=balance+? WHERE guild_id=? AND user_id=?", (amount, guild.id, target.id))
                    with contextlib.suppress(discord.HTTPException):
                        await verification_message.delete()
                    with contextlib.suppress(discord.HTTPException):
                        await candidate.delete()
                    await self._reply(message, f"**{sender.name}, قام بتحويل `${amount}` لـ {target.mention}**. 💳")
                    with contextlib.suppress(discord.HTTPException):
                        await message.delete()
                    return
                with contextlib.suppress(discord.HTTPException):
                    await candidate.delete()
            if verification_message:
                with contextlib.suppress(discord.HTTPException):
                    await verification_message.delete()
            await self._reply(message, "⏱️ انتهت مهلة التحقق من التحويل، وما تمش تحويل أي مبلغ.")
        finally:
            self.transfer_locks.discard(key)

    async def handle_rating_command(self, message: discord.Message):
        content = message.content.strip()
        if content not in {"-تقييم", "-تقييم التكت"}:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return True
        with connect() as con:
            row = con.execute("SELECT owner_id, closed_by, rating FROM tickets WHERE channel_id=?", (message.channel.id,)).fetchone()
        if not row:
            await self._reply(message, "❌ هاد الروم ماشي تذكرة.")
            return True
        if row["owner_id"] != message.author.id:
            await self._reply(message, "❌ غير صاحب التذكرة يقدر يقيمها.")
            return True
        if not row["closed_by"]:
            await self._reply(message, "❌ خاص التكت تسد أولاً من طرف الإدارة قبل التقييم.")
            return True
        if row["rating"] is not None:
            await self._reply(message, "⚠️ سبق لك تقييم هاد التذكرة.")
            return True
        cfg = get_config(message.guild.id)
        max_rating = max(1, min(10, int(cfg["ticket_rating_max"] or 10)))
        await self._reply(message, "⭐ اختار التقييم، ومن بعد كتب الملاحظة ديالك فالنموذج اللي غادي يبان.", view=RatingView(message.channel.id, message.author.id, max_rating))
        return True

    async def handle_jail_shortcuts(self, message: discord.Message):
        content = message.content.strip()
        parts = content.split()
        if not parts or parts[0] not in {"سجن", "عفو"}:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return True
        if not (message.author.guild_permissions.manage_guild or message.author.guild_permissions.administrator):
            await self._reply(message, "❌ ما عندكش صلاحية استعمال هاد الاختصار.")
            return True
        target = message.mentions[0] if message.mentions else None
        if not target and len(parts) >= 2 and parts[1].isdigit():
            target = message.guild.get_member(int(parts[1]))
        if not target:
            await self._reply(message, f"❌ الاستعمال: `{parts[0]} @user` أو `{parts[0]} id`.")
            return True
        jail_cog = self.bot.get_cog("Jail")
        if not jail_cog:
            await self._reply(message, "❌ نظام السجن غير محمل.")
            return True
        try:
            if parts[0] == "سجن":
                ok, error = await jail_cog.jail_member(message.guild, target, message.author, None)
                if not ok:
                    await self._reply(message, error or "❌ فشل السجن.")
                else:
                    await self._reply(message, f"🔒 تم سجن {target.mention}.")
            else:
                found = await jail_cog.unjail_member(message.guild, target)
                if not found:
                    await self._reply(message, "❌ هاد العضو ماشي مسجون عندي.")
                else:
                    await self._reply(message, f"🔓 تم فك السجن عن {target.mention}.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self._reply(message, f"❌ البوت ما قدرش ينفذ العملية: `{exc}`")
        except Exception as exc:
            print(f"[JAIL PREFIX ERROR] {type(exc).__name__}: {exc}")
            await self._reply(message, "❌ وقع خطأ داخلي أثناء تنفيذ أمر السجن. تأكد من إعدادات البوت ثم عاود المحاولة.")
        return True

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return False
        content = message.content.strip()
        if re.match(r"(?i)^c(?:\s|$)", content):
            return await self.handle_c(message)
        if content in {"-تقييم", "-تقييم التكت"}:
            return await self.handle_rating_command(message)
        if content == "سجن" or content.startswith("سجن ") or content == "عفو" or content.startswith("عفو "):
            return await self.handle_jail_shortcuts(message)
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Prefix systems are message listeners, not normal Discord commands.
        # This is required for Arabic shortcuts such as "سجن @user" and "عفو @user".
        await self.handle_message(message)


async def setup(bot):
    await bot.add_cog(PrefixSystems(bot))
