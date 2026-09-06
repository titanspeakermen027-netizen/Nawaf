from __future__ import annotations

import contextlib

import discord
from discord.ext import commands

from database import connect


class DeleteAllTicketsView(discord.ui.View):
    def __init__(self, author_id: int, cog: "AdminPrefix"):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.cog = cog
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ غير الشخص اللي طلب العملية يقدر يأكدها.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="تأكيد الحذف", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not self.cog.is_admin(interaction.user):
            return await interaction.response.send_message("❌ ما عندكش الصلاحية.", ephemeral=True)
        await interaction.response.defer()
        deleted = await self.cog.delete_all_tickets(interaction.guild)
        for item in self.children:
            item.disabled = True
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)
        await interaction.followup.send(f"✅ تم حذف **{deleted}** تذكرة بنجاح.")
        self.stop()

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❌ تم إلغاء حذف جميع التكتات.", view=self)
        self.stop()


class AdminPrefix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def is_admin(member: discord.Member) -> bool:
        return member.guild_permissions.administrator or member.guild_permissions.manage_guild

    async def reply(self, message: discord.Message, content: str, **kwargs):
        kwargs.setdefault("mention_author", False)
        return await message.reply(content, **kwargs)

    def get_points(self, guild_id: int, user_id: int) -> int:
        with connect() as con:
            row = con.execute(
                "SELECT points FROM points WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
        return int(row["points"]) if row else 0

    def change_points(self, guild_id: int, user_id: int, amount: int):
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)",
                (guild_id, user_id),
            )
            con.execute(
                "UPDATE points SET points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )

    async def delete_all_tickets(self, guild: discord.Guild) -> int:
        with connect() as con:
            rows = con.execute(
                "SELECT channel_id FROM tickets WHERE guild_id=?",
                (guild.id,),
            ).fetchall()
        deleted = 0
        deleted_ids: list[int] = []
        for row in rows:
            channel_id = int(row["channel_id"])
            channel = guild.get_channel(channel_id)
            if channel is None:
                deleted_ids.append(channel_id)
                deleted += 1
                continue
            try:
                await channel.delete(reason="Nawaf !حذف - delete all tickets")
                deleted_ids.append(channel_id)
                deleted += 1
            except (discord.Forbidden, discord.HTTPException):
                continue
        if deleted_ids:
            with connect() as con:
                con.executemany("DELETE FROM tickets WHERE guild_id=? AND channel_id=?", [(guild.id, cid) for cid in deleted_ids])
        return deleted

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        content = message.content.strip()

        if content in {"-نقاطي", "-نقاط"}:
            value = self.get_points(message.guild.id, message.author.id)
            await self.reply(message, f"⭐ **{message.author.display_name}، عندك حالياً `{value}` نقطة.**")
            return

        if content.startswith("-تحويل"):
            if not self.is_admin(message.author):
                await self.reply(message, "❌ غير الإدارة اللي عندها الصلاحية تعطي نقاط.")
                return
            parts = content.split()
            target = message.mentions[0] if message.mentions else None
            if target is None and len(parts) >= 2 and parts[1].isdigit():
                target = message.guild.get_member(int(parts[1]))
            if not target or len(parts) < 3:
                await self.reply(message, "❌ الاستعمال: `-تحويل @user 10` أو `-تحويل ID 10`.")
                return
            try:
                amount = int(parts[2].replace(",", ""))
            except ValueError:
                amount = 0
            if amount <= 0:
                await self.reply(message, "❌ عدد النقاط خاصو يكون أكبر من 0.")
                return
            self.change_points(message.guild.id, target.id, amount)
            new_value = self.get_points(message.guild.id, target.id)
            await self.reply(message, f"✅ تم إعطاء {target.mention} **{amount} نقطة**. رصيده الآن: **{new_value} نقطة**.")
            return

        if content == "!حذف":
            if not self.is_admin(message.author):
                await self.reply(message, "❌ غير الإدارة تقدر تحذف جميع التكتات.")
                return
            with connect() as con:
                row = con.execute("SELECT COUNT(*) AS total FROM tickets WHERE guild_id=?", (message.guild.id,)).fetchone()
            total = int(row["total"])
            if total == 0:
                await self.reply(message, "ℹ️ ما كاين حتى تكت مسجل باش يتحذف.")
                return
            view = DeleteAllTicketsView(message.author.id, self)
            view.message = await self.reply(
                message,
                "**هل انت متاكد من انك تريد حذف جميع التكتات\nهذا الخيار ما يمدي التراجع عنه**",
                view=view,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.handle_message(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPrefix(bot))
