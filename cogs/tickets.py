import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from database import connect, get_config, set_config


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def get_config_from_channel(channel_id):
    with connect() as con:
        row = con.execute("SELECT guild_id FROM tickets WHERE channel_id=?", (channel_id,)).fetchone()
    if not row:
        return 10
    cfg = get_config(row["guild_id"])
    return max(1, min(10, int(cfg["ticket_rating_max"] or 10)))


class RatingModal(discord.ui.Modal):
    def __init__(self, channel_id):
        super().__init__(title="تقييم التذكرة")
        self.channel_id = channel_id
        self.max_rating = get_config_from_channel(channel_id)
        self.rating = discord.ui.TextInput(
            label=f"التقييم من 1 إلى {self.max_rating}",
            min_length=1,
            max_length=2,
            placeholder=str(self.max_rating),
        )
        self.note = discord.ui.TextInput(
            label="ملاحظتك",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            placeholder="كتب ملاحظتك على تجربة الدعم...",
        )
        self.add_item(self.rating)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.rating.value)
        except ValueError:
            value = 0
        if value < 1 or value > self.max_rating:
            return await interaction.response.send_message(
                f"❌ التقييم يجب أن يكون بين 1 و{self.max_rating}.", ephemeral=True
            )

        with connect() as con:
            row = con.execute(
                "SELECT owner_id, closed_by, rating FROM tickets WHERE channel_id=?",
                (self.channel_id,),
            ).fetchone()
            if not row or row["owner_id"] != interaction.user.id or not row["closed_by"]:
                return await interaction.response.send_message("❌ لا يمكنك تقييم هذه التذكرة.", ephemeral=True)
            if row["rating"] is not None:
                return await interaction.response.send_message("⚠️ سبق لك تقييم هذه التذكرة.", ephemeral=True)
            con.execute(
                "UPDATE tickets SET rating=?, note=? WHERE channel_id=?",
                (value, self.note.value.strip() or None, self.channel_id),
            )
        await interaction.response.send_message("⭐ شكراً على تقييمك! تم حفظ التقييم والملاحظة.", ephemeral=True)


class DeleteTicketView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🗑️ حذف التذكرة", style=discord.ButtonStyle.danger, custom_id="nawaf:ticket:delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message("❌ غير الإداري يقدر يحذف التذكرة.", ephemeral=True)
        await self.cog.delete_ticket(interaction)


class CloseView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🔒 إغلاق التذكرة", style=discord.ButtonStyle.danger, custom_id="nawaf:ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_ticket(interaction)

    @discord.ui.button(label="🗑️ حذف التذكرة", style=discord.ButtonStyle.secondary, custom_id="nawaf:ticket:delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message("❌ غير الإداري يقدر يحذف التذكرة.", ephemeral=True)
        await self.cog.delete_ticket(interaction)


class TicketView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🎫 فتح تذكرة", style=discord.ButtonStyle.primary, custom_id="nawaf:ticket:open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_ticket(interaction)


class RatingButton(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        button = discord.ui.Button(
            label="⭐ تقييم التذكرة",
            style=discord.ButtonStyle.success,
            custom_id=f"nawaf:ticket:rating:{channel_id}",
        )
        button.callback = self.rate
        self.add_item(button)
        self.delete_view = DeleteTicketViewProxy(channel_id)
        delete_button = discord.ui.Button(
            label="🗑️ حذف التذكرة",
            style=discord.ButtonStyle.danger,
            custom_id=f"nawaf:ticket:delete:{channel_id}",
        )
        delete_button.callback = self.delete_ticket
        self.add_item(delete_button)

    async def rate(self, interaction: discord.Interaction):
        if interaction.channel_id != self.channel_id:
            return await interaction.response.send_message("❌ زر التقييم غير صالح هنا.", ephemeral=True)
        with connect() as con:
            row = con.execute(
                "SELECT owner_id, closed_by, rating FROM tickets WHERE channel_id=?",
                (self.channel_id,),
            ).fetchone()
        if not row or not row["closed_by"]:
            return await interaction.response.send_message("❌ التذكرة لم تُغلق بعد.", ephemeral=True)
        if row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ هذا الزر مخصص لصاحب التذكرة.", ephemeral=True)
        if row["rating"] is not None:
            return await interaction.response.send_message("⚠️ سبق لك تقييم هذه التذكرة.", ephemeral=True)
        await interaction.response.send_modal(RatingModal(self.channel_id))

    async def delete_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message("❌ غير الإداري يقدر يحذف التذكرة.", ephemeral=True)
        with connect() as con:
            row = con.execute("SELECT channel_id FROM tickets WHERE channel_id=?", (self.channel_id,)).fetchone()
            if not row:
                return await interaction.response.send_message("❌ التذكرة غير موجودة في قاعدة البيانات.", ephemeral=True)
            con.execute("DELETE FROM tickets WHERE channel_id=?", (self.channel_id,))
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.HTTPException:
            await interaction.response.send_message("❌ ما قدرتش نحذف روم التذكرة.", ephemeral=True)


class DeleteTicketViewProxy:
    def __init__(self, channel_id):
        self.channel_id = channel_id


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketView(self))
        self.bot.add_view(CloseView(self))
        with connect() as con:
            rows = con.execute(
                "SELECT channel_id FROM tickets WHERE closed_by IS NOT NULL AND rating IS NULL"
            ).fetchall()
        for row in rows:
            self.bot.add_view(RatingButton(row["channel_id"]))

    async def open_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ هاد الأمر خاص بالسيرفر.", ephemeral=True)

        with connect() as con:
            old = con.execute(
                "SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND closed_by IS NULL",
                (guild.id, interaction.user.id),
            ).fetchone()
        if old:
            return await interaction.response.send_message(
                f"❌ عندك تذكرة مفتوحة بالفعل: <#{old['channel_id']}>.", ephemeral=True
            )

        cfg = get_config(guild.id)
        category = guild.get_channel(cfg["ticket_category"]) if cfg["ticket_category"] else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        for role in guild.roles:
            if role.permissions.manage_channels:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.name}", category=category, overwrites=overwrites
        )
        with connect() as con:
            con.execute(
                "INSERT INTO tickets(channel_id,guild_id,owner_id,created_at) VALUES(?,?,?,?)",
                (channel.id, guild.id, interaction.user.id, utcnow_iso()),
            )

        embed = discord.Embed(
            title="🎫 تذكرتك مفتوحة",
            description=(
                f"مرحبا {interaction.user.mention}\n\n"
                "شرح المشكل أو الطلب ديالك بالتفصيل، وطاقم الإدارة غادي يتكلف به."
            ),
            color=discord.Color.blurple(),
        )
        await channel.send(embed=embed, view=CloseView(self))
        await interaction.response.send_message(f"✅ تم فتح التذكرة: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction):
        with connect() as con:
            row = con.execute(
                "SELECT guild_id, owner_id FROM tickets WHERE channel_id=? AND closed_by IS NULL",
                (interaction.channel.id,),
            ).fetchone()
            if not row:
                return await interaction.response.send_message("❌ هذه ليست تذكرة مفتوحة.", ephemeral=True)
            if interaction.user.id == row["owner_id"]:
                return await interaction.response.send_message(
                    "❌ صاحب التذكرة لا يمكنه إغلاقها بنفسه.", ephemeral=True
                )
            con.execute(
                "UPDATE tickets SET closed_by=?, closed_at=? WHERE channel_id=?",
                (interaction.user.id, utcnow_iso(), interaction.channel.id),
            )

        cfg = get_config(row["guild_id"])
        rating_view = RatingButton(interaction.channel.id)
        await interaction.channel.send(
            f"🔒 تم إغلاق التذكرة بواسطة {interaction.user.mention}.\n"
            "صاحب التذكرة يقدر الآن يكتب `-تقييم` أو يستعمل زر التقييم.",
            view=rating_view,
        )
        self.bot.add_view(rating_view)

        log_channel = interaction.guild.get_channel(cfg["ticket_log_channel"]) if cfg["ticket_log_channel"] else None
        if log_channel:
            embed = discord.Embed(
                title="🎫 Ticket Closed",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="صاحب التذكرة", value=f"<@{row['owner_id']}>", inline=True)
            embed.add_field(name="أغلقها", value=interaction.user.mention, inline=True)
            embed.add_field(name="الروم", value=interaction.channel.mention, inline=True)
            await log_channel.send(embed=embed)

        await interaction.response.send_message("✅ تم إغلاق التذكرة وتفعيل التقييم.", ephemeral=True)

    async def delete_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message("❌ غير الإداري يقدر يحذف التذكرة.", ephemeral=True)
        with connect() as con:
            exists = con.execute("SELECT channel_id FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
            if not exists:
                return await interaction.response.send_message("❌ هاد الروم ماشي تذكرة.", ephemeral=True)
            con.execute("DELETE FROM tickets WHERE channel_id=?", (interaction.channel.id,))
        await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")

    @app_commands.command(name="ticket-panel", description="إرسال Panel فتح التذاكر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = get_config(interaction.guild.id)
        embed = discord.Embed(
            title=cfg["ticket_panel_title"] or "🎫 الدعم الفني",
            description=cfg["ticket_panel_description"] or "اضغط على الزر لفتح تذكرة.",
            color=discord.Color.blurple(),
        )
        msg = await channel.send(embed=embed, view=TicketView(self))
        set_config(
            interaction.guild.id,
            ticket_panel_channel=channel.id,
            ticket_panel_message=msg.id,
        )
        await interaction.response.send_message("✅ تم إرسال Panel التذاكر.", ephemeral=True)

    @app_commands.command(name="ticket-settings", description="تعديل تفاصيل Panel التذاكر والتقييم")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        title="عنوان الـPanel",
        description="وصف الـPanel",
        rating_max="أقصى تقييم من 1 إلى 10",
        log_channel="روم سجل إغلاق التذاكر (اختياري)",
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        rating_max: app_commands.Range[int, 1, 10] = 10,
        log_channel: discord.TextChannel | None = None,
    ):
        set_config(
            interaction.guild.id,
            ticket_panel_title=title[:256],
            ticket_panel_description=description[:4000],
            ticket_rating_max=rating_max,
            ticket_log_channel=log_channel.id if log_channel else None,
        )
        await interaction.response.send_message(
            f"✅ تم تعديل التكت.\n⭐ التقييم: 1–{rating_max}", ephemeral=True
        )

    @app_commands.command(name="ticket-category", description="تحديد Category التذاكر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        set_config(interaction.guild.id, ticket_category=category.id)
        await interaction.response.send_message(f"✅ Category: {category.name}", ephemeral=True)

    @app_commands.command(name="ticket-delete", description="حذف التذكرة الحالية — للإدارة فقط")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def delete_command(self, interaction: discord.Interaction):
        await self.delete_ticket(interaction)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
