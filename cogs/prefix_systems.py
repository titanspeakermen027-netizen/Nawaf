from __future__ import annotations

import contextlib

import discord
from discord.ext import commands

from database import connect, get_config


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
    """Prefix utilities unrelated to the retired economy system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(self, message: discord.Message, content: str, **kwargs):
        kwargs.setdefault("mention_author", False)
        return await message.reply(content, **kwargs)

    async def handle_rating_command(self, message: discord.Message):
        content = message.content.strip()
        if content not in {"-تقييم", "-تقييم التكت"}:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return True
        with connect() as con:
            row = con.execute(
                "SELECT owner_id, closed_by, rating FROM tickets WHERE channel_id=?",
                (message.channel.id,),
            ).fetchone()
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
        await self._reply(
            message,
            "⭐ اختار التقييم، ومن بعد كتب الملاحظة ديالك فالنموذج اللي غادي يبان.",
            view=RatingView(message.channel.id, message.author.id, max_rating),
        )
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
        except (discord.Forbidden, discord.HTTPException):
            await self._reply(message, "❌ البوت ما قدرش ينفذ العملية. تأكد من صلاحياته.")
        except Exception as exc:
            print(f"[JAIL PREFIX ERROR] {type(exc).__name__}: {exc}")
            await self._reply(message, "❌ وقع خطأ داخلي أثناء تنفيذ أمر السجن.")
        return True

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return False
        content = message.content.strip()
        if content in {"-تقييم", "-تقييم التكت"}:
            return await self.handle_rating_command(message)
        if content == "سجن" or content.startswith("سجن ") or content == "عفو" or content.startswith("عفو "):
            return await self.handle_jail_shortcuts(message)
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.handle_message(message)


async def setup(bot):
    await bot.add_cog(PrefixSystems(bot))
