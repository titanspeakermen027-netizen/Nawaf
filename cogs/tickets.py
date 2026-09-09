import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from database import connect, get_config, set_config
from cogs.access_control import can_control_bot


def now():
    return datetime.now(timezone.utc).isoformat()


def is_staff(member: discord.Member) -> bool:
    return can_control_bot(member) or member.guild_permissions.manage_channels


def ensure_ticket_schema():
    with connect() as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tickets)")}
        for name, definition in (
            ("claimed_by", "INTEGER"),
            ("claimed_at", "TEXT"),
            ("rating_at", "TEXT"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE tickets ADD COLUMN {name} {definition}")


def ticket_embed(title, description, color=discord.Color.blurple()):
    return discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())


class RatingModal(discord.ui.Modal):
    def __init__(self, cog, channel_id: int):
        super().__init__(title="⭐ تقييم الدعم")
        self.cog = cog
        self.channel_id = channel_id
        self.rating = discord.ui.TextInput(label="التقييم من 1 إلى 10", placeholder="مثال: 10", min_length=1, max_length=2)
        self.note = discord.ui.TextInput(label="ملاحظتك على الدعم", required=False, style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(self.rating)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.rating.value)
        except ValueError:
            value = 0
        with connect() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (self.channel_id,)).fetchone()
        if not row or row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ هذا التقييم مخصص لصاحب التذكرة.", ephemeral=True)
        maximum = max(1, min(10, int(get_config(row["guild_id"])["ticket_rating_max"] or 10)))
        if not 1 <= value <= maximum:
            return await interaction.response.send_message(f"❌ التقييم يجب أن يكون بين 1 و{maximum}.", ephemeral=True)
        if row["rating"] is not None:
            return await interaction.response.send_message("⚠️ تم إرسال تقييمك بالفعل.", ephemeral=True)
        if not row["closed_by"]:
            return await interaction.response.send_message("❌ يجب إغلاق التذكرة أولاً.", ephemeral=True)

        note = self.note.value.strip() or None
        with connect() as con:
            con.execute("UPDATE tickets SET rating=?, note=?, rating_at=? WHERE channel_id=?", (value, note, now(), self.channel_id))
            if row["claimed_by"]:
                con.execute("INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)", (row["guild_id"], row["claimed_by"]))
                con.execute("UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?", (value, row["guild_id"], row["claimed_by"]))

        await self.cog.send_rating_log(interaction.guild, row, value, note)
        await interaction.response.send_message("✅ تم إرسال تقييمك بنجاح. شكراً لك!", ephemeral=True)

        channel = interaction.channel
        if channel:
            with contextlib.suppress(discord.HTTPException):
                await channel.send(embed=ticket_embed("⭐ تم استلام التقييم", f"تم استلام تقييم صاحب التذكرة: **{value}/{maximum}**\nيمكن للإدارة الآن حذف التذكرة." , discord.Color.green()))


class TicketControls(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="استلام التذكرة", style=discord.ButtonStyle.success, emoji="🙋", custom_id="nawaf:ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.claim_ticket(interaction)

    @discord.ui.button(label="إغلاق", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="nawaf:ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_ticket(interaction)

    @discord.ui.button(label="حذف", style=discord.ButtonStyle.secondary, emoji="🗑️", custom_id="nawaf:ticket:delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.delete_ticket(interaction)


class TicketPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="فتح تذكرة", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="nawaf:ticket:open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_ticket(interaction)


class RatingView(discord.ui.View):
    def __init__(self, cog, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

    @discord.ui.button(label="إرسال التقييم", style=discord.ButtonStyle.success, emoji="⭐")
    async def rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        with connect() as con:
            row = con.execute("SELECT owner_id,rating FROM tickets WHERE channel_id=?", (self.channel_id,)).fetchone()
        if not row or row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ زر التقييم مخصص لصاحب التذكرة.", ephemeral=True)
        if row["rating"] is not None:
            return await interaction.response.send_message("⚠️ سبق إرسال التقييم.", ephemeral=True)
        await interaction.response.send_modal(RatingModal(self.cog, self.channel_id))


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        ensure_ticket_schema()
        self.bot.add_view(TicketPanel(self))
        self.bot.add_view(TicketControls(self))
        with connect() as con:
            rows = con.execute("SELECT channel_id FROM tickets WHERE closed_by IS NOT NULL AND rating IS NULL").fetchall()
        for row in rows:
            self.bot.add_view(RatingView(self, row["channel_id"]))

    async def open_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفر.", ephemeral=True)
        with connect() as con:
            old = con.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND closed_by IS NULL", (guild.id, interaction.user.id)).fetchone()
        if old:
            return await interaction.response.send_message(f"❌ لديك تذكرة مفتوحة بالفعل: <#{old['channel_id']}>", ephemeral=True)

        cfg = get_config(guild.id)
        category = guild.get_channel(cfg["ticket_category"]) if cfg["ticket_category"] else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        }
        for role in guild.roles:
            if role.permissions.manage_channels or role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", category=category, overwrites=overwrites, reason=f"Ticket opened by {interaction.user}")
        with connect() as con:
            con.execute("INSERT INTO tickets(channel_id,guild_id,owner_id,created_at) VALUES(?,?,?,?)", (channel.id, guild.id, interaction.user.id, now()))

        embed = ticket_embed("🎫 تذكرة جديدة", f"مرحباً {interaction.user.mention}\n\nسيقوم أحد أعضاء الإدارة باستلام التذكرة عبر زر **استلام التذكرة**. بعد استلامها يصبح المسؤول عنها واضحاً للجميع.", discord.Color.blurple())
        embed.add_field(name="📌 الحالة", value="بانتظار استلام أحد الإداريين", inline=False)
        await channel.send(embed=embed, view=TicketControls(self))
        await interaction.response.send_message(f"✅ تم فتح تذكرتك: {channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ الاستلام مخصص للإدارة.", ephemeral=True)
        with connect() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (interaction.channel_id,)).fetchone()
            if not row or row["closed_by"]:
                return await interaction.response.send_message("❌ هذه ليست تذكرة مفتوحة.", ephemeral=True)
            if row["owner_id"] == interaction.user.id:
                return await interaction.response.send_message("❌ لا يمكنك استلام تذكرتك الخاصة.", ephemeral=True)
            if row["claimed_by"] and row["claimed_by"] != interaction.user.id:
                return await interaction.response.send_message(f"❌ تم استلام هذه التذكرة بالفعل بواسطة <@{row['claimed_by']}>.", ephemeral=True)
            if not row["claimed_by"]:
                con.execute("UPDATE tickets SET claimed_by=?, claimed_at=? WHERE channel_id=?", (interaction.user.id, now(), interaction.channel_id))
        await interaction.response.send_message(f"🙋 تم استلام التذكرة بواسطة {interaction.user.mention}. أصبح هو المسؤول الأساسي عن متابعتها.", ephemeral=False)

    async def close_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ الإغلاق مخصص للإدارة.", ephemeral=True)
        with connect() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (interaction.channel_id,)).fetchone()
            if not row or row["closed_by"]:
                return await interaction.response.send_message("❌ هذه ليست تذكرة مفتوحة.", ephemeral=True)
            if not row["claimed_by"]:
                return await interaction.response.send_message("❌ يجب استلام التذكرة أولاً قبل إغلاقها.", ephemeral=True)
            if row["claimed_by"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message(f"❌ التذكرة مستلمة بواسطة <@{row['claimed_by']}> ولا يمكن إغلاقها إلا من المسؤول عنها أو الإدارة.", ephemeral=True)
            con.execute("UPDATE tickets SET closed_by=?, closed_at=? WHERE channel_id=?", (interaction.user.id, now(), interaction.channel_id))

        rating_view = RatingView(self, interaction.channel_id)
        self.bot.add_view(rating_view)
        await interaction.response.send_message("🔒 تم إغلاق التذكرة.", ephemeral=True)
        await interaction.channel.send(
            content=f"{interaction.guild.get_member(row['owner_id']).mention if interaction.guild.get_member(row['owner_id']) else f'<@{row['owner_id']}>'}",
            embed=ticket_embed("⭐ التقييم إلزامي", "تم إغلاق تذكرتك. يرجى إرسال تقييمك الآن قبل أن يتم حذف التذكرة. هذا التقييم يظهر للإدارة ويُحتسب للمسؤول الذي استلم تذكرتك.", discord.Color.orange()),
            view=rating_view,
        )

    async def delete_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ الحذف مخصص للإدارة.", ephemeral=True)
        with connect() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (interaction.channel_id,)).fetchone()
        if not row:
            return await interaction.response.send_message("❌ هذا الروم ليس تذكرة.", ephemeral=True)
        if row["closed_by"] and row["rating"] is None:
            return await interaction.response.send_message("❌ لا يمكن حذف التذكرة قبل أن يرسل صاحبها التقييم الإلزامي.", ephemeral=True)
        if not row["closed_by"]:
            return await interaction.response.send_message("❌ يجب إغلاق التذكرة أولاً.", ephemeral=True)
        with connect() as con:
            con.execute("DELETE FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")

    async def send_rating_log(self, guild, row, rating, note):
        cfg = get_config(row["guild_id"])
        log = guild.get_channel(cfg["ticket_log_channel"]) if cfg["ticket_log_channel"] else None
        if not log:
            return
        maximum = max(1, min(10, int(cfg["ticket_rating_max"] or 10)))
        stars = "⭐" * min(rating, 10)
        embed = ticket_embed("⭐ تقييم جديد للتذكرة", f"{stars}\n**التقييم:** {rating}/{maximum}", discord.Color.gold())
        embed.add_field(name="صاحب التذكرة", value=f"<@{row['owner_id']}>", inline=True)
        embed.add_field(name="المسؤول المستلم", value=f"<@{row['claimed_by']}>" if row["claimed_by"] else "غير محدد", inline=True)
        embed.add_field(name="أغلق التذكرة", value=f"<@{row['closed_by']}>", inline=True)
        if note:
            embed.add_field(name="ملاحظة العميل", value=note[:1024], inline=False)
        if row["claimed_by"]:
            embed.set_footer(text=f"تم منح المسؤول {rating} نقاط تلقائياً حسب تقييم العميل")
        await log.send(embed=embed)

    @app_commands.command(name="ticket-panel", description="إرسال لوحة فتح التذاكر")
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة.", ephemeral=True)
        cfg = get_config(interaction.guild.id)
        embed = ticket_embed(cfg["ticket_panel_title"] or "🎫 الدعم الفني", cfg["ticket_panel_description"] or "اضغط على الزر لفتح تذكرة.")
        msg = await channel.send(embed=embed, view=TicketPanel(self))
        set_config(interaction.guild.id, ticket_panel_channel=channel.id, ticket_panel_message=msg.id)
        await interaction.response.send_message("✅ تم إرسال لوحة التذاكر.", ephemeral=True)

    @app_commands.command(name="ticket-settings", description="إعداد لوحة التذاكر والتقييم")
    async def settings(self, interaction: discord.Interaction, title: str, description: str, rating_max: app_commands.Range[int, 1, 10] = 10, log_channel: discord.TextChannel | None = None):
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة.", ephemeral=True)
        set_config(interaction.guild.id, ticket_panel_title=title[:256], ticket_panel_description=description[:4000], ticket_rating_max=rating_max, ticket_log_channel=log_channel.id if log_channel else None)
        await interaction.response.send_message("✅ تم حفظ إعدادات التذاكر والتقييم.", ephemeral=True)

    @app_commands.command(name="ticket-category", description="تحديد فئة التذاكر")
    async def category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة.", ephemeral=True)
        set_config(interaction.guild.id, ticket_category=category.id)
        await interaction.response.send_message(f"✅ تم تحديد الفئة: {category.name}", ephemeral=True)


async def setup(bot):
    import contextlib
    await bot.add_cog(Tickets(bot))
