import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(os.getenv("MODERATION_BOT_DB", "moderation_bot/moderation.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

WARNING_LIMIT = 3
PREFIX = "!"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                ticket_category_id INTEGER,
                ticket_log_channel_id INTEGER,
                ticket_panel_channel_id INTEGER,
                ticket_panel_message_id INTEGER,
                mod_log_channel_id INTEGER,
                warning_log_channel_id INTEGER,
                jail_role_id INTEGER,
                jail_channel_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                related_user_id INTEGER,
                related_type TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jails (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                previous_roles TEXT NOT NULL DEFAULT '[]',
                jailed_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                claimed_by INTEGER,
                closed_by INTEGER,
                closed_at TEXT,
                rating INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                rater_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                note TEXT,
                ticket_channel_id INTEGER,
                created_at TEXT NOT NULL
            );
            """
        )

    # Migrate existing databases created before the dedicated warning log.
    with connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(guild_settings)").fetchall()
        }
        if "warning_log_channel_id" not in columns:
            con.execute(
                "ALTER TABLE guild_settings ADD COLUMN warning_log_channel_id INTEGER"
            )


def ensure_guild(guild_id: int) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)",
            (guild_id,),
        )


def get_settings(guild_id: int) -> sqlite3.Row:
    ensure_guild(guild_id)
    with connect() as con:
        return con.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?",
            (guild_id,),
        ).fetchone()


def set_setting(guild_id: int, **values) -> None:
    ensure_guild(guild_id)
    allowed = {
        "ticket_category_id",
        "ticket_log_channel_id",
        "ticket_panel_channel_id",
        "ticket_panel_message_id",
        "mod_log_channel_id",
        "warning_log_channel_id",
        "jail_role_id",
        "jail_channel_id",
    }
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    fields = ", ".join(f"{key}=?" for key in values)
    with connect() as con:
        con.execute(
            f"UPDATE guild_settings SET {fields} WHERE guild_id=?",
            (*values.values(), guild_id),
        )


def warning_count(guild_id: int, user_id: int) -> int:
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS count FROM warnings WHERE guild_id=? AND user_id=? AND active=1",
            (guild_id, user_id),
        ).fetchone()
    return int(row["count"])


def warning_rows(guild_id: int, user_id: int):
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM warnings
            WHERE guild_id=? AND user_id=? AND active=1
            ORDER BY id DESC
            """,
            (guild_id, user_id),
        ).fetchall()


def reason_options():
    return [
        ("سب / إهانة", "سب أو إهانة عضو"),
        ("تدخل في السياسة", "مخالفة قوانين النقاش السياسي"),
        ("تضارب / مشكلة", "افتعال تضارب أو مشكلة"),
        ("إزعاج متكرر", "إزعاج متكرر"),
        ("استفزاز / تخريب", "استفزاز أو تخريب أجواء السيرفر"),
        ("محتوى غير مناسب", "نشر محتوى مخالف"),
        ("مخالفة القوانين", "مخالفة عامة لقوانين السيرفر"),
        ("مشكلة للإدارة", "سبب مشكلة لأحد أفراد الإدارة"),
        ("مشكلة لعضو", "سبب مشكلة لعضو آخر"),
        ("سبب آخر", "سبب مخصص يكتبه المشرف"),
    ]


async def respond(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = False,
):
    kwargs = {"content": content, "ephemeral": ephemeral}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs)
    return await interaction.response.send_message(**kwargs)


async def send_mod_log(guild: discord.Guild, embed: discord.Embed) -> None:
    settings = get_settings(guild.id)
    channel_id = settings["mod_log_channel_id"]
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def send_warning_log(guild: discord.Guild, embed: discord.Embed) -> None:
    settings = get_settings(guild.id)
    channel_id = settings["warning_log_channel_id"] or settings["mod_log_channel_id"]
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def dm_user(user: discord.User, content: str) -> None:
    try:
        await user.send(content)
    except (discord.Forbidden, discord.HTTPException):
        pass


def is_manager(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return any(
        (
            perms.administrator,
            perms.manage_guild,
            perms.manage_messages,
            perms.moderate_members,
            perms.kick_members,
            perms.ban_members,
            perms.manage_roles,
            perms.manage_channels,
        )
    )


def can_manage_target(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
) -> tuple[bool, str]:
    if target.id == actor.id:
        return False, "❌ ما يمكنكش تستعمل هاد الإجراء على راسك."
    if target.id == guild.me.id:
        return False, "❌ ما يمكنش استعمال الإجراء على البوت نفسه."
    if target.top_role >= actor.top_role and not actor.guild_permissions.administrator:
        return False, "❌ رتبة العضو مساوية أو أعلى من رتبتك."
    me = guild.me
    if me and target.top_role >= me.top_role:
        return False, "❌ هاد العضو أعلى من أو مساوي لأعلى رتبة ديال البوت."
    return True, ""


def mention_user(user_id: int | None) -> str:
    return f"<@{user_id}>" if user_id else "غير محدد"


def parse_duration(value: str):
    match = re.fullmatch(r"(\d{1,3})(s|m|h|d)", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount
    if unit == "m":
        seconds *= 60
    elif unit == "h":
        seconds *= 3600
    elif unit == "d":
        seconds *= 86400
    if seconds <= 0 or seconds > 28 * 86400:
        return None
    return timedelta(seconds=seconds)


async def ensure_jail_role(guild: discord.Guild) -> discord.Role:
    settings = get_settings(guild.id)
    if settings["jail_role_id"]:
        role = guild.get_role(settings["jail_role_id"])
        if role:
            return role

    role = discord.utils.get(guild.roles, name="مسجون")
    if role is None:
        role = await guild.create_role(name="مسجون", reason="إنشاء رتبة السجن")
    set_setting(guild.id, jail_role_id=role.id)
    return role


class ActionReasonSelect(discord.ui.Select):
    def __init__(
        self,
        cog,
        action: str,
        target: discord.Member,
        author_id: int,
        ephemeral_result: bool = False,
    ):
        self.cog = cog
        self.action = action
        self.target = target
        self.author_id = author_id
        self.ephemeral_result = ephemeral_result
        options = [
            discord.SelectOption(label=label[:100], description=desc[:100], value=str(index))
            for index, (label, desc) in enumerate(reason_options())
        ]
        super().__init__(
            placeholder="اختر سبب الإجراء...",
            options=options,
            custom_id=f"modbot:reason:{action}:{target.id}:{author_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ هاد الاختيارات مخصصة للمشرف اللي بدأ العملية.",
                ephemeral=True,
            )

        label, _ = reason_options()[int(self.values[0])]

        if label == "سبب آخر":
            return await interaction.response.send_modal(
                CustomReasonModal(
                    self.cog,
                    self.action,
                    self.target,
                    self.author_id,
                    self.ephemeral_result,
                )
            )

        if label in {"مشكلة للإدارة", "مشكلة لعضو"} and self.action == "warn":
            return await interaction.response.send_message(
                f"اختر {('الإداري' if label == 'مشكلة للإدارة' else 'العضو')} المتضرر:",
                view=RelatedUserView(
                    self.cog,
                    self.action,
                    self.target,
                    self.author_id,
                    label,
                    self.ephemeral_result,
                ),
                ephemeral=True,
            )

        if self.action == "mute":
            return await interaction.response.send_message(
                f"السبب: **{label}**\nاختر مدة الكتم:",
                view=DurationView(
                    self.cog,
                    self.target,
                    self.author_id,
                    label,
                    self.ephemeral_result,
                ),
                ephemeral=True,
            )

        # Discord requires an initial interaction acknowledgement quickly.
        await interaction.response.defer(ephemeral=self.ephemeral_result)
        await self.cog.execute_action(
            interaction,
            self.action,
            self.target,
            label,
        )


class ActionReasonView(discord.ui.View):
    def __init__(
        self,
        cog,
        action: str,
        target: discord.Member,
        author_id: int,
        ephemeral_result: bool = False,
    ):
        super().__init__(timeout=180)
        self.add_item(
            ActionReasonSelect(
                cog,
                action,
                target,
                author_id,
                ephemeral_result,
            )
        )


class CustomReasonModal(discord.ui.Modal, title="اكتب سبب الإجراء"):
    reason = discord.ui.TextInput(
        label="السبب",
        placeholder="اكتب السبب هنا...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, cog, action, target, author_id, ephemeral_result=False):
        super().__init__(title="اكتب سبب الإجراء")
        self.cog = cog
        self.action = action
        self.target = target
        self.author_id = author_id
        self.ephemeral_result = ephemeral_result

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)
        await interaction.response.defer(ephemeral=self.ephemeral_result)
        await self.cog.execute_action(
            interaction,
            self.action,
            self.target,
            self.reason.value.strip() or "بدون سبب محدد",
        )


class RelatedUserSelect(discord.ui.UserSelect):
    def __init__(self, cog, target, author_id, related_type, ephemeral_result=False):
        self.cog = cog
        self.target = target
        self.author_id = author_id
        self.related_type = related_type
        self.ephemeral_result = ephemeral_result
        super().__init__(
            placeholder="اختر الشخص المتضرر من القائمة",
            min_values=1,
            max_values=1,
            custom_id=f"modbot:related:{target.id}:{author_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)

        selected = self.values[0]
        if isinstance(selected, discord.Member):
            if self.related_type == "مشكلة للإدارة" and not is_manager(selected):
                return await interaction.response.send_message(
                    "❌ السبب المختار هو مشكلة للإدارة، اختر عضواً من الإدارة.",
                    ephemeral=True,
                )
            if self.related_type == "مشكلة لعضو" and is_manager(selected):
                return await interaction.response.send_message(
                    "❌ السبب المختار هو مشكلة لعضو، اختر عضواً عادياً.",
                    ephemeral=True,
                )

        reason = f"{self.related_type} — المتضرر: {selected.mention}"
        await interaction.response.defer(ephemeral=self.ephemeral_result)
        await self.cog.apply_warning(
            interaction,
            self.target,
            reason,
            related_user_id=selected.id,
            related_type=self.related_type,
        )


class RelatedUserView(discord.ui.View):
    def __init__(
        self,
        cog,
        action,
        target,
        author_id,
        related_type,
        ephemeral_result=False,
    ):
        super().__init__(timeout=180)
        self.add_item(
            RelatedUserSelect(
                cog,
                target,
                author_id,
                related_type,
                ephemeral_result,
            )
        )


class DurationSelect(discord.ui.Select):
    def __init__(self, cog, target, author_id, reason, ephemeral_result=False):
        self.cog = cog
        self.target = target
        self.author_id = author_id
        self.reason = reason
        self.ephemeral_result = ephemeral_result
        options = [
            ("10 دقائق", "10m"),
            ("30 دقيقة", "30m"),
            ("ساعة", "1h"),
            ("6 ساعات", "6h"),
            ("يوم", "1d"),
            ("7 أيام", "7d"),
            ("28 يوم", "28d"),
        ]
        super().__init__(
            placeholder="اختر مدة الكتم...",
            options=[discord.SelectOption(label=a, value=b) for a, b in options],
            custom_id=f"modbot:duration:{target.id}:{author_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)

        duration = parse_duration(self.values[0])
        await interaction.response.defer(ephemeral=self.ephemeral_result)
        await self.cog.execute_mute(
            interaction,
            self.target,
            self.reason,
            duration,
            self.values[0],
        )


class DurationView(discord.ui.View):
    def __init__(self, cog, target, author_id, reason, ephemeral_result=False):
        super().__init__(timeout=180)
        self.add_item(
            DurationSelect(
                cog,
                target,
                author_id,
                reason,
                ephemeral_result,
            )
        )


class TicketPanelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="فتح تذكرة",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="modbot:ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.create_ticket(interaction)


class TicketControlsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="استلام",
        style=discord.ButtonStyle.success,
        emoji="🙋",
        custom_id="modbot:ticket:claim",
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.claim_ticket(interaction)

    @discord.ui.button(
        label="إغلاق",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="modbot:ticket:close",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_ticket(interaction)

    @discord.ui.button(
        label="حذف",
        style=discord.ButtonStyle.secondary,
        emoji="🗑️",
        custom_id="modbot:ticket:delete",
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.delete_ticket(interaction)


class TicketRatingButton(discord.ui.Button):
    def __init__(self, cog, channel_id: int, value: int):
        self.cog = cog
        self.channel_id = channel_id
        self.value = value
        super().__init__(
            label=str(value),
            style=discord.ButtonStyle.success if value >= 4 else discord.ButtonStyle.secondary,
            emoji="⭐",
            custom_id=f"modbot:ticket:rate:{channel_id}:{value}",
        )

    async def callback(self, interaction: discord.Interaction):
        await self.cog.rate_ticket(interaction, self.channel_id, self.value)


class TicketRatingView(discord.ui.View):
    def __init__(self, cog, channel_id: int):
        super().__init__(timeout=None)
        for value in range(1, 6):
            self.add_item(TicketRatingButton(cog, channel_id, value))


class RatingButton(discord.ui.Button):
    def __init__(self, cog, target_id: int, author_id: int, value: int):
        self.cog = cog
        self.target_id = target_id
        self.author_id = author_id
        self.value = value
        super().__init__(
            label=f"{value}/5",
            style=discord.ButtonStyle.success if value >= 4 else discord.ButtonStyle.secondary,
            emoji="⭐",
            custom_id=f"modbot:rating:{target_id}:{author_id}:{value}",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ هاد التقييم مخصص لصاحبه.", ephemeral=True)
        await interaction.response.send_modal(
            RatingNoteModal(self.cog, self.target_id, self.author_id, self.value)
        )


class RatingView(discord.ui.View):
    def __init__(self, cog, target_id: int, author_id: int):
        super().__init__(timeout=180)
        for value in range(1, 6):
            self.add_item(RatingButton(cog, target_id, author_id, value))


class RatingNoteModal(discord.ui.Modal, title="ملاحظات التقييم"):
    note = discord.ui.TextInput(
        label="ملاحظتك",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=800,
        placeholder="اكتب ملاحظة اختيارية...",
    )

    def __init__(self, cog, target_id, author_id, value):
        super().__init__(title="ملاحظات التقييم")
        self.cog = cog
        self.target_id = target_id
        self.author_id = author_id
        self.value = value

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)
        await self.cog.save_rating(
            interaction.guild,
            self.target_id,
            interaction.user.id,
            self.value,
            self.note.value.strip() or None,
            None,
        )
        await interaction.response.send_message("✅ تم حفظ التقييم.", ephemeral=True)


class MassDMConfirmView(discord.ui.View):
    def __init__(self, cog, author_id: int, content: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.content = content

    @discord.ui.button(label="تأكيد الإرسال", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        success, failed, skipped = await self.cog.mass_dm(interaction.guild, self.content)
        await interaction.followup.send(
            f"✅ انتهى الإرسال. تم: **{success}** | فشل: **{failed}** | تم تخطي البوتات: **{skipped}**",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ غير مسموح.", ephemeral=True)
        await interaction.response.send_message("تم إلغاء الإرسال.", ephemeral=True)
        self.stop()


class SecondBot(commands.Bot):
    async def setup_hook(self):
        init_db()
        self.add_view(TicketPanelView(self))
        self.add_view(TicketControlsView(self))

        with connect() as con:
            rows = con.execute(
                "SELECT channel_id FROM tickets WHERE closed_by IS NOT NULL AND rating IS NULL"
            ).fetchall()

        for row in rows:
            self.add_view(TicketRatingView(self, row["channel_id"]))

        try:
            synced = await self.tree.sync()
            print(f"[OK] تمت مزامنة {len(synced)} من أوامر Slash للبوت الثاني")
        except discord.HTTPException as exc:
            print(f"[ERROR] فشلت مزامنة أوامر Slash للبوت الثاني: {exc!r}")


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class ModerationBot(commands.Cog):
    def __init__(self, bot_instance: SecondBot):
        self.bot = bot_instance

    async def execute_action(
        self,
        interaction,
        action: str,
        target: discord.Member,
        reason: str,
    ):
        guild = interaction.guild
        actor = interaction.user

        if not guild or not isinstance(actor, discord.Member):
            return await respond(interaction, 
                "❌ الأمر خاص بالسيرفر.",
                ephemeral=True,
            )

        if action == "warn":
            ok, error = can_manage_target(guild, actor, target)
            if not ok:
                return await respond(interaction, error, ephemeral=True)
            await self.apply_warning(interaction, target, reason)
            return

        ok, error = can_manage_target(guild, actor, target)
        if not ok:
            return await respond(interaction, error, ephemeral=True)

        try:
            if action == "ban":
                if not guild.me or not guild.me.guild_permissions.ban_members:
                    return await respond(interaction, 
                        "❌ البوت ما عندوش صلاحية Ban Members.",
                        ephemeral=True,
                    )
                await target.ban(reason=reason, delete_message_seconds=0)
                text = f"🔨 تم حظر {target.mention}. السبب: **{reason}**"
                await respond(interaction, text)
                await send_mod_log(
                    guild,
                    discord.Embed(
                        title="🔨 حظر",
                        description=f"{target.mention}\nالمشرف: {actor.mention}\nالسبب: {reason}",
                        color=discord.Color.red(),
                    ),
                )
                await dm_user(
                    target,
                    f"تم حظرك من سيرفر {guild.name}.\nالسبب: {reason}",
                )
                return

            if action == "kick":
                if not guild.me or not guild.me.guild_permissions.kick_members:
                    return await respond(interaction, 
                        "❌ البوت ما عندوش صلاحية Kick Members.",
                        ephemeral=True,
                    )
                await target.kick(reason=reason)
                text = f"👢 تم طرد {target.mention}. السبب: **{reason}**"
                await respond(interaction, text)
                await send_mod_log(
                    guild,
                    discord.Embed(
                        title="👢 طرد",
                        description=f"{target.mention}\nالمشرف: {actor.mention}\nالسبب: {reason}",
                        color=discord.Color.orange(),
                    ),
                )
                await dm_user(
                    target,
                    f"تم طردك من سيرفر {guild.name}.\nالسبب: {reason}",
                )
                return

            if action == "jail":
                if not guild.me or not guild.me.guild_permissions.manage_roles:
                    return await respond(interaction, 
                        "❌ البوت ما عندوش Manage Roles.",
                        ephemeral=True,
                    )
                await self.jail_member(guild, target, actor, reason)
                await respond(interaction, 
                    f"🔒 تم إدخال {target.mention} إلى السجن. السبب: **{reason}**"
                )
                return

            await respond(interaction, "❌ إجراء غير معروف.", ephemeral=True)

        except discord.Forbidden:
            await respond(interaction, 
                "❌ Discord رفض العملية. تأكد من الصلاحيات وترتيب الرتب.",
                ephemeral=True,
            )
        except discord.HTTPException:
            await respond(interaction, 
                "❌ وقع خطأ من Discord أثناء تنفيذ العملية.",
                ephemeral=True,
            )
        except RuntimeError as exc:
            await respond(interaction, f"❌ {exc}", ephemeral=True)

    async def execute_mute(
        self,
        interaction,
        target: discord.Member,
        reason: str,
        duration,
        label: str,
    ):
        guild = interaction.guild
        actor = interaction.user

        if not guild or not isinstance(actor, discord.Member):
            return await respond(interaction, 
                "❌ الأمر خاص بالسيرفر.",
                ephemeral=True,
            )

        ok, error = can_manage_target(guild, actor, target)
        if not ok:
            return await respond(interaction, error, ephemeral=True)

        if not guild.me or not guild.me.guild_permissions.moderate_members:
            return await respond(interaction, 
                "❌ البوت ما عندوش Moderate Members.",
                ephemeral=True,
            )

        try:
            await target.timeout(duration, reason=reason)
            await respond(interaction, 
                f"🔇 تم كتم {target.mention} لمدة **{label}**. السبب: **{reason}**"
            )
            await send_mod_log(
                guild,
                discord.Embed(
                    title="🔇 كتم",
                    description=(
                        f"{target.mention}\n"
                        f"المشرف: {actor.mention}\n"
                        f"المدة: {label}\n"
                        f"السبب: {reason}"
                    ),
                    color=discord.Color.orange(),
                ),
            )
            await dm_user(
                target,
                f"تم كتمك في سيرفر {guild.name} لمدة {label}.\nالسبب: {reason}",
            )
        except discord.Forbidden:
            await respond(interaction, 
                "❌ Discord رفض الكتم. تأكد من صلاحية Moderate Members وترتيب الرتب.",
                ephemeral=True,
            )
        except discord.HTTPException:
            await respond(interaction, 
                "❌ وقع خطأ من Discord أثناء الكتم.",
                ephemeral=True,
            )

    async def apply_warning(
        self,
        interaction,
        target: discord.Member,
        reason: str,
        related_user_id: int | None = None,
        related_type: str | None = None,
    ):
        guild = interaction.guild
        actor = interaction.user

        try:
            with connect() as con:
                con.execute(
                    """
                    INSERT INTO warnings(
                        guild_id, user_id, moderator_id, reason,
                        related_user_id, related_type, active, created_at
                    )
                    VALUES(?,?,?,?,?,?,1,?)
                    """,
                    (
                        guild.id,
                        target.id,
                        actor.id,
                        reason,
                        related_user_id,
                        related_type,
                        utcnow(),
                    ),
                )
        except sqlite3.Error:
            return await respond(interaction, 
                "❌ تعذر حفظ التحذير.",
                ephemeral=True,
            )

        count = warning_count(guild.id, target.id)
        remaining = max(0, WARNING_LIMIT - count)

        if count < WARNING_LIMIT:
            text = (
                f"⚠️ {target.mention} أخذ تحذير رقم **{count}/{WARNING_LIMIT}**.\n"
                f"السبب: **{reason}**\n"
                f"باقي له **{remaining}** تحذير."
            )
        else:
            text = (
                f"🚨 {target.mention} وصل إلى **{WARNING_LIMIT}/{WARNING_LIMIT}** تحذيرات.\n"
                f"السبب الأخير: **{reason}**\n"
                "تم إدخاله السجن تلقائياً."
            )

        await send_warning_log(
            guild,
            discord.Embed(
                title="⚠️ تحذير جديد",
                description=text,
                color=discord.Color.yellow() if count < WARNING_LIMIT else discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            ),
        )

        await dm_user(
            target,
            (
                f"⚠️ أخذت تحذيراً في سيرفر {guild.name}.\n"
                f"السبب: {reason}\n"
                f"عدد تحذيراتك: {count}/{WARNING_LIMIT}.\n"
                f"المتبقي: {remaining}."
            )
            if count < WARNING_LIMIT
            else
            (
                f"🚨 وصلت إلى {WARNING_LIMIT}/{WARNING_LIMIT} تحذيرات في سيرفر {guild.name}.\n"
                "تم إدخالك السجن تلقائياً."
            ),
        )

        if count >= WARNING_LIMIT:
            try:
                await self.jail_member(
                    guild,
                    target,
                    actor,
                    f"بلوغ {WARNING_LIMIT} تحذيرات",
                )
            except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                return await respond(interaction, 
                    f"{text}\n⚠️ تعذر تنفيذ السجن تلقائياً: {exc}",
                )

        await respond(interaction, text)

    async def jail_member(
        self,
        guild: discord.Guild,
        target: discord.Member,
        actor: discord.Member,
        reason: str,
    ):
        if not guild.me or not guild.me.guild_permissions.manage_roles:
            raise RuntimeError("البوت يحتاج Manage Roles للسجن.")

        role = await ensure_jail_role(guild)

        with connect() as con:
            existing = con.execute(
                "SELECT * FROM jails WHERE guild_id=? AND user_id=?",
                (guild.id, target.id),
            ).fetchone()

        if not existing:
            previous = [
                old_role.id
                for old_role in target.roles
                if (
                    not old_role.is_default()
                    and not old_role.managed
                    and old_role.id != role.id
                    and old_role < guild.me.top_role
                )
            ]
            with connect() as con:
                con.execute(
                    """
                    INSERT OR REPLACE INTO jails(
                        guild_id, user_id, previous_roles, jailed_by, created_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        guild.id,
                        target.id,
                        json.dumps(previous),
                        actor.id,
                        utcnow(),
                    ),
                )

            removable = [
                old_role
                for old_role in target.roles
                if (
                    not old_role.is_default()
                    and not old_role.managed
                    and old_role != role
                    and old_role < guild.me.top_role
                )
            ]
            if removable:
                await target.remove_roles(
                    *removable,
                    reason=f"Jail: {reason}",
                )

        if role not in target.roles:
            await target.add_roles(
                role,
                reason=f"Jail: {reason}",
            )

        await send_mod_log(
            guild,
            discord.Embed(
                title="🔒 سجن",
                description=(
                    f"{target.mention}\n"
                    f"المشرف: {actor.mention}\n"
                    f"السبب: {reason}"
                ),
                color=discord.Color.dark_red(),
            ),
        )

    async def unjail_from_message(
        self,
        message: discord.Message,
        target: discord.Member,
    ):
        if not is_manager(message.author):
            return await message.reply("❌ الإدارة فقط.")
        if not message.guild.me or not message.guild.me.guild_permissions.manage_roles:
            return await message.reply("❌ البوت ما عندوش Manage Roles.")

        with connect() as con:
            row = con.execute(
                "SELECT * FROM jails WHERE guild_id=? AND user_id=?",
                (message.guild.id, target.id),
            ).fetchone()

        if not row:
            return await message.reply("❌ العضو ماشي مسجون عند البوت.")

        for role_id in json.loads(row["previous_roles"]):
            role = message.guild.get_role(role_id)
            if role and role < message.guild.me.top_role:
                try:
                    await target.add_roles(
                        role,
                        reason=f"Unjail by {message.author}",
                    )
                except discord.HTTPException:
                    pass

        settings = get_settings(message.guild.id)
        jail_role = (
            message.guild.get_role(settings["jail_role_id"])
            if settings["jail_role_id"]
            else None
        )

        if jail_role and jail_role in target.roles:
            await target.remove_roles(
                jail_role,
                reason=f"Unjail by {message.author}",
            )

        with connect() as con:
            con.execute(
                "DELETE FROM jails WHERE guild_id=? AND user_id=?",
                (message.guild.id, target.id),
            )

        await message.reply(f"✅ تم إخراج {target.mention} من السجن.")
        await send_mod_log(
            message.guild,
            discord.Embed(
                title="🔓 فك السجن",
                description=f"{target.mention}\nالمشرف: {message.author.mention}",
                color=discord.Color.green(),
            ),
        )

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message(
                "❌ هذا الزر يعمل داخل السيرفر فقط.",
                ephemeral=True,
            )

        with connect() as con:
            old = con.execute(
                """
                SELECT channel_id FROM tickets
                WHERE guild_id=? AND owner_id=? AND closed_by IS NULL
                """,
                (guild.id, interaction.user.id),
            ).fetchone()

        if old:
            return await interaction.response.send_message(
                f"❌ عندك تذكرة مفتوحة: <#{old['channel_id']}>",
                ephemeral=True,
            )

        settings = get_settings(guild.id)
        category = (
            guild.get_channel(settings["ticket_category_id"])
            if settings["ticket_category_id"]
            else None
        )

        if category is None:
            category = await guild.create_category(
                "Tickets",
                reason="إنشاء تصنيف التذاكر",
            )
            set_setting(guild.id, ticket_category_id=category.id)

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
            if role.permissions.administrator or role.permissions.manage_channels:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.name}"[:90],
            category=category,
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user}",
        )

        with connect() as con:
            con.execute(
                """
                INSERT INTO tickets(
                    channel_id, guild_id, owner_id, created_at
                )
                VALUES(?,?,?,?)
                """,
                (channel.id, guild.id, interaction.user.id, utcnow()),
            )

        embed = discord.Embed(
            title="🎫 تذكرة دعم",
            description=(
                f"مرحباً {interaction.user.mention}\n"
                "اكتب طلبك هنا، وسيستلمها أحد أعضاء الإدارة.\n\n"
                "التحكم: استلام، إغلاق، حذف."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        await channel.send(embed=embed, view=TicketControlsView(self))
        await interaction.response.send_message(
            f"✅ تم فتح التذكرة: {channel.mention}",
            ephemeral=True,
        )

    async def claim_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        actor = interaction.user

        if not isinstance(actor, discord.Member) or not is_manager(actor):
            return await interaction.response.send_message(
                "❌ الاستلام للإدارة فقط.",
                ephemeral=True,
            )

        with connect() as con:
            row = con.execute(
                "SELECT * FROM tickets WHERE channel_id=?",
                (interaction.channel_id,),
            ).fetchone()

        if not row or row["closed_by"]:
            return await interaction.response.send_message(
                "❌ هذه ليست تذكرة مفتوحة.",
                ephemeral=True,
            )

        if row["owner_id"] == actor.id:
            return await interaction.response.send_message(
                "❌ صاحب التذكرة ما يقدرش يستلم تذكرته.",
                ephemeral=True,
            )

        if row["claimed_by"] and row["claimed_by"] != actor.id:
            return await interaction.response.send_message(
                f"❌ مستلمة من طرف <@{row['claimed_by']}>.",
                ephemeral=True,
            )

        with connect() as con:
            con.execute(
                "UPDATE tickets SET claimed_by=? WHERE channel_id=?",
                (actor.id, interaction.channel_id),
            )

        await interaction.response.send_message(
            f"🙋 تم استلام التذكرة بواسطة {actor.mention}."
        )

    async def close_ticket(self, interaction: discord.Interaction):
        actor = interaction.user

        if not isinstance(actor, discord.Member) or not is_manager(actor):
            return await interaction.response.send_message(
                "❌ الإغلاق للإدارة فقط.",
                ephemeral=True,
            )

        with connect() as con:
            row = con.execute(
                "SELECT * FROM tickets WHERE channel_id=?",
                (interaction.channel_id,),
            ).fetchone()

        if not row or row["closed_by"]:
            return await interaction.response.send_message(
                "❌ هذه ليست تذكرة مفتوحة.",
                ephemeral=True,
            )

        if not row["claimed_by"]:
            return await interaction.response.send_message(
                "❌ استلم التذكرة أولاً.",
                ephemeral=True,
            )

        if (
            row["claimed_by"] != actor.id
            and not actor.guild_permissions.administrator
        ):
            return await interaction.response.send_message(
                "❌ غير مسموح لك بإغلاق تذكرة مستلمة من مشرف آخر.",
                ephemeral=True,
            )

        with connect() as con:
            con.execute(
                """
                UPDATE tickets
                SET closed_by=?, closed_at=?
                WHERE channel_id=?
                """,
                (actor.id, utcnow(), interaction.channel_id),
            )

        await interaction.response.send_message(
            f"🔒 تم إغلاق التذكرة.\n"
            f"صاحب التذكرة {mention_user(row['owner_id'])} يقدر الآن يقيّم الدعم من 1 إلى 5.",
            view=TicketRatingView(self, interaction.channel_id),
        )

    async def delete_ticket(self, interaction: discord.Interaction):
        actor = interaction.user

        if not isinstance(actor, discord.Member) or not is_manager(actor):
            return await interaction.response.send_message(
                "❌ الحذف للإدارة فقط.",
                ephemeral=True,
            )

        with connect() as con:
            row = con.execute(
                "SELECT * FROM tickets WHERE channel_id=?",
                (interaction.channel_id,),
            ).fetchone()

        if not row:
            return await interaction.response.send_message(
                "❌ هاد الروم ماشي تذكرة.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "🗑️ غادي يتحذف روم التذكرة.",
            ephemeral=True,
        )
        await asyncio.sleep(2)

        try:
            await interaction.channel.delete(
                reason=f"Ticket deleted by {actor}",
            )
        except discord.HTTPException:
            pass

    async def rate_ticket(self, interaction, channel_id: int, value: int):
        with connect() as con:
            row = con.execute(
                "SELECT * FROM tickets WHERE channel_id=?",
                (channel_id,),
            ).fetchone()

        if not row:
            return await interaction.response.send_message(
                "❌ التذكرة غير موجودة.",
                ephemeral=True,
            )

        if interaction.user.id != row["owner_id"]:
            return await interaction.response.send_message(
                "❌ التقييم لصاحب التذكرة فقط.",
                ephemeral=True,
            )

        if row["rating"] is not None:
            return await interaction.response.send_message(
                "⚠️ سبق تم إرسال التقييم.",
                ephemeral=True,
            )

        if not row["closed_by"]:
            return await interaction.response.send_message(
                "❌ التذكرة لم تُغلق بعد.",
                ephemeral=True,
            )

        with connect() as con:
            con.execute(
                "UPDATE tickets SET rating=? WHERE channel_id=?",
                (value, channel_id),
            )

        if row["claimed_by"]:
            await self.save_rating(
                interaction.guild,
                row["claimed_by"],
                interaction.user.id,
                value,
                "تقييم التذكرة",
                channel_id,
            )

        await interaction.response.send_message(
            f"⭐ شكراً، تم تسجيل تقييمك: **{value}/5**.",
            ephemeral=True,
        )

    async def save_rating(
        self,
        guild,
        target_id,
        rater_id,
        value,
        note,
        ticket_channel_id,
    ):
        with connect() as con:
            con.execute(
                """
                INSERT INTO ratings(
                    guild_id, target_id, rater_id, rating,
                    note, ticket_channel_id, created_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    guild.id,
                    target_id,
                    rater_id,
                    value,
                    note,
                    ticket_channel_id,
                    utcnow(),
                ),
            )

        await send_mod_log(
            guild,
            discord.Embed(
                title="⭐ تقييم جديد",
                description=(
                    f"المقيَّم: {mention_user(target_id)}\n"
                    f"المقيِّم: {mention_user(rater_id)}\n"
                    f"التقييم: **{value}/5**\n"
                    f"الملاحظة: {note or 'بدون ملاحظة'}"
                ),
                color=discord.Color.green(),
            ),
        )

    async def show_ratings(self, message, target):
        with connect() as con:
            rows = con.execute(
                """
                SELECT rating, note, rater_id, created_at
                FROM ratings
                WHERE guild_id=? AND target_id=?
                ORDER BY id DESC
                LIMIT 10
                """,
                (message.guild.id, target.id),
            ).fetchall()
            avg_row = con.execute(
                """
                SELECT AVG(rating) AS average, COUNT(*) AS count
                FROM ratings
                WHERE guild_id=? AND target_id=?
                """,
                (message.guild.id, target.id),
            ).fetchone()

        if not rows:
            return await message.reply(
                f"⭐ ما كايناش تقييمات مسجلة لـ {target.mention}."
            )

        average = float(avg_row["average"] or 0)
        lines = []
        for row in rows:
            note = row["note"] or "بدون ملاحظة"
            lines.append(
                f"⭐ **{row['rating']}/5** — {note} — <@{row['rater_id']}>"
            )

        embed = discord.Embed(
            title=f"⭐ تقييمات {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=f"المتوسط: {average:.2f}/5 | الإجمالي: {avg_row['count']}"
        )
        await message.reply(embed=embed)

    async def mass_dm(self, guild, content):
        success = failed = skipped = 0
        for member in guild.members:
            if member.bot:
                skipped += 1
                continue

            try:
                await member.send(content)
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

            await asyncio.sleep(1.2)

        return success, failed, skipped

    async def show_warnings(self, message, target):
        if not isinstance(target, discord.Member):
            return await message.reply("❌ العضو غير موجود.")

        rows = warning_rows(message.guild.id, target.id)
        if not rows:
            return await message.reply(
                f"✅ {target.mention} ما عندوش تحذيرات نشطة."
            )

        lines = [
            f"**#{row['id']}** — {row['reason']} — <@{row['moderator_id']}>"
            for row in rows[:10]
        ]

        embed = discord.Embed(
            title=f"⚠️ تحذيرات {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.yellow(),
        )
        embed.set_footer(
            text=f"{len(rows)}/{WARNING_LIMIT} تحذيرات نشطة"
        )
        await message.reply(embed=embed)

    async def remove_warning(self, message, target):
        if not target:
            return await message.reply(
                "❌ الاستعمال: مسح_تحذير @عضو"
            )

        with connect() as con:
            row = con.execute(
                """
                SELECT id FROM warnings
                WHERE guild_id=? AND user_id=? AND active=1
                ORDER BY id DESC LIMIT 1
                """,
                (message.guild.id, target.id),
            ).fetchone()

            if not row:
                return await message.reply(
                    "❌ ما عندوش تحذيرات نشطة."
                )

            con.execute(
                "UPDATE warnings SET active=0 WHERE id=?",
                (row["id"],),
            )

        await message.reply(
            f"✅ تم إلغاء آخر تحذير عن {target.mention}."
        )

    async def clear_warnings(self, message, target):
        if not target:
            return await message.reply(
                "❌ الاستعمال: مسح_تحذيرات @عضو"
            )

        with connect() as con:
            cur = con.execute(
                """
                UPDATE warnings
                SET active=0
                WHERE guild_id=? AND user_id=? AND active=1
                """,
                (message.guild.id, target.id),
            )
            count = cur.rowcount

        await message.reply(
            f"✅ تم مسح **{count}** تحذيرات عن {target.mention}."
        )

    async def create_ticket_panel(self, message):
        if not isinstance(message.channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title="🎫 الدعم والتذاكر",
            description=(
                "اضغط على زر **فتح تذكرة** لفتح تذكرة خاصة مع الإدارة."
            ),
            color=discord.Color.blurple(),
        )

        sent = await message.channel.send(
            embed=embed,
            view=TicketPanelView(self),
        )

        set_setting(
            message.guild.id,
            ticket_panel_channel_id=message.channel.id,
            ticket_panel_message_id=sent.id,
        )

        await message.reply(
            f"✅ تم إرسال بانل التذاكر في {message.channel.mention}."
        )

    async def lock_channel(self, message, lock: bool):
        if not isinstance(message.channel, discord.TextChannel):
            return

        overwrite = message.channel.overwrites_for(
            message.guild.default_role
        )
        overwrite.send_messages = False if lock else None

        try:
            await message.channel.set_permissions(
                message.guild.default_role,
                overwrite=overwrite,
                reason=f"{'Lock' if lock else 'Unlock'} by {message.author}",
            )
            await message.reply(
                "🔒 تم قفل الروم."
                if lock
                else
                "🔓 تم فتح الروم."
            )
        except discord.HTTPException:
            await message.reply("❌ تعذر تعديل صلاحيات الروم.")

    async def purge(self, message, raw):
        try:
            amount = max(1, min(100, int(raw)))
        except ValueError:
            amount = 10

        try:
            deleted = await message.channel.purge(limit=amount + 1)
            confirm = await message.channel.send(
                f"🧹 تم حذف **{max(0, len(deleted) - 1)}** رسالة."
            )
            await asyncio.sleep(3)
            await confirm.delete()
        except discord.HTTPException:
            await message.reply("❌ تعذر مسح الرسائل.")

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not isinstance(message.author, discord.Member):
            return

        if not is_manager(message.author):
            return

        content = message.content.strip()
        if content.startswith(PREFIX):
            content = content[1:].strip()

        parts = content.split()
        if not parts:
            return

        command = parts[0].lower().strip()
        mentions = list(message.mentions)
        channel_mentions = list(message.channel_mentions)

        aliases = {
            "قفل": "lock",
            "لوك": "lock",
            "فتح": "unlock",
            "انلوك": "unlock",
            "مسح": "purge",
            "تحذير": "warn",
            "انذار": "warn",
            "تبنيد": "ban",
            "باند": "ban",
            "حظر": "ban",
            "كيك": "kick",
            "طرد": "kick",
            "كتم": "mute",
            "تايم": "mute",
            "سجن": "jail",
            "فك_سجن": "unjail",
            "فك_السجن": "unjail",
            "فك_تايم": "unmute",
            "فك_الكتم": "unmute",
            "تحذيرات": "warnings",
            "مسح_تحذير": "unwarn",
            "مسح_تحذيرات": "clearwarnings",
            "رسالة": "dm",
            "خاص": "dm",
            "للجميع": "massdm",
            "خاص_للجميع": "massdm",
            "ارسال": "channelmsg",
            "إرسال": "channelmsg",
            "تكت_بانل": "ticketpanel",
            "تكت": "ticketpanel",
            "اعداد_لوق": "setlog",
            "إعداد_لوق": "setlog",
            "اعداد_تحذيرات": "setwarninglog",
            "إعداد_تحذيرات": "setwarninglog",
            "اعداد_السجن": "setjail",
            "إعداد_السجن": "setjail",
            "اعداد_تكت": "setticket",
            "إعداد_تكت": "setticket",
            "تقييم": "rate",
            "تقييمات": "ratings",
        }
        command = aliases.get(command, command)

        if command == "lock":
            await self.lock_channel(message, True)
            return

        if command == "unlock":
            await self.lock_channel(message, False)
            return

        if command == "purge":
            await self.purge(
                message,
                parts[1] if len(parts) > 1 else "10",
            )
            return

        if command in {"warn", "ban", "kick", "mute", "jail"}:
            target = mentions[0] if mentions else None
            if not isinstance(target, discord.Member):
                return await message.reply(
                    "❌ خاصك تحدد العضو بالمنشن."
                )

            action_label = {
                "warn": "تحذير",
                "ban": "حظر",
                "kick": "طرد",
                "mute": "كتم",
                "jail": "سجن",
            }[command]

            return await message.reply(
                f"اختر سبب {action_label} لـ {target.mention}:",
                view=ActionReasonView(
                    self,
                    command,
                    target,
                    message.author.id,
                    ephemeral_result=False,
                ),
            )

        if command == "unmute":
            target = mentions[0] if mentions else None
            if not isinstance(target, discord.Member):
                return await message.reply("❌ الاستعمال: فك_كتم @عضو")

            if not message.guild.me or not message.guild.me.guild_permissions.moderate_members:
                return await message.reply(
                    "❌ البوت ما عندوش Moderate Members."
                )

            try:
                await target.timeout(
                    None,
                    reason=f"Unmute by {message.author}",
                )
                await message.reply(
                    f"✅ تم فك الكتم عن {target.mention}."
                )
            except discord.HTTPException:
                await message.reply("❌ تعذر فك الكتم.")
            return

        if command == "unjail":
            target = mentions[0] if mentions else None
            if not isinstance(target, discord.Member):
                return await message.reply("❌ الاستعمال: فك_سجن @عضو")
            await self.unjail_from_message(message, target)
            return

        if command == "warnings":
            target = mentions[0] if mentions else message.author
            await self.show_warnings(message, target)
            return

        if command == "unwarn":
            target = mentions[0] if mentions else None
            await self.remove_warning(message, target)
            return

        if command == "clearwarnings":
            target = mentions[0] if mentions else None
            await self.clear_warnings(message, target)
            return

        if command == "dm":
            target = mentions[0] if mentions else None
            if not target:
                return await message.reply(
                    "❌ الاستعمال: رسالة @عضو النص"
                )

            text = message.content.split(
                target.mention,
                1,
            )[-1].strip()

            if not text and len(parts) >= 3:
                text = " ".join(parts[2:])

            if not text:
                return await message.reply(
                    "❌ اكتب الرسالة بعد العضو."
                )

            await dm_user(target, text)
            await message.reply(
                f"✅ تم إرسال الرسالة إلى {target.mention}."
            )
            return

        if command == "massdm":
            text = " ".join(parts[1:]).strip()
            if not text:
                return await message.reply(
                    "❌ الاستعمال: خاص_للجميع النص"
                )

            return await message.reply(
                (
                    "⚠️ غادي نرسل هاد الرسالة لجميع الأعضاء غير البوتات.\n"
                    f"الرسالة: {text}\n"
                    "هل تريد المتابعة؟"
                ),
                view=MassDMConfirmView(
                    self,
                    message.author.id,
                    text,
                ),
            )

        if command == "channelmsg":
            target_channel = (
                channel_mentions[0]
                if channel_mentions
                else message.channel
            )

            marker = (
                target_channel.mention
                if channel_mentions
                else ""
            )

            text = (
                message.content.split(marker, 1)[-1].strip()
                if marker
                else " ".join(parts[1:])
            )

            if not text:
                return await message.reply(
                    "❌ الاستعمال: إرسال #الروم النص"
                )

            if not isinstance(
                target_channel,
                (discord.TextChannel, discord.Thread),
            ):
                return await message.reply(
                    "❌ خاصك تحدد روم نصية."
                )

            try:
                await target_channel.send(text)
                await message.reply(
                    f"✅ تم إرسال الرسالة في {target_channel.mention}."
                )
            except discord.HTTPException:
                await message.reply(
                    "❌ تعذر الإرسال للروم."
                )
            return

        if command == "ticketpanel":
            await self.create_ticket_panel(message)
            return

        if command == "setwarninglog":
            if not message.author.guild_permissions.administrator:
                return await message.reply(
                    "❌ هذا الإعداد لصاحب السيرفر أو Administrator فقط."
                )
            channel = (
                channel_mentions[0]
                if channel_mentions
                else message.channel
            )
            set_setting(
                message.guild.id,
                warning_log_channel_id=channel.id,
            )
            await message.reply(
                f"✅ تم تعيين {channel.mention} كسجل خاص بالتحذيرات."
            )
            return

        if command == "setlog":
            channel = (
                channel_mentions[0]
                if channel_mentions
                else message.channel
            )
            set_setting(
                message.guild.id,
                mod_log_channel_id=channel.id,
            )
            await message.reply(
                f"✅ تم تعيين {channel.mention} كلوق للإدارة."
            )
            return

        if command == "setjail":
            role = (
                message.role_mentions[0]
                if message.role_mentions
                else None
            )
            channel = (
                channel_mentions[0]
                if channel_mentions
                else None
            )

            if role:
                set_setting(
                    message.guild.id,
                    jail_role_id=role.id,
                )
            else:
                role = await ensure_jail_role(
                    message.guild
                )

            if channel:
                set_setting(
                    message.guild.id,
                    jail_channel_id=channel.id,
                )

            await message.reply(
                f"✅ إعداد السجن تم. الرتبة: {role.mention}"
                + (
                    f" | روم السجن: {channel.mention}"
                    if channel
                    else ""
                )
            )
            return

        if command == "setticket":
            category = next(
                (
                    category
                    for category in message.guild.categories
                    if category.name.lower() == "tickets"
                ),
                None,
            )

            if category is None:
                category = await message.guild.create_category(
                    "Tickets"
                )

            set_setting(
                message.guild.id,
                ticket_category_id=category.id,
            )

            await message.reply(
                f"✅ تم تعيين تصنيف التذاكر: {category.name}"
            )
            return

        if command == "rate":
            target = mentions[0] if mentions else None
            if not isinstance(target, discord.Member):
                return await message.reply(
                    "❌ الاستعمال: تقييم @مشرف"
                )

            await message.reply(
                f"اختر تقييمك لـ {target.mention}:",
                view=RatingView(
                    self,
                    target.id,
                    message.author.id,
                ),
            )
            return

        if command == "ratings":
            target = mentions[0] if mentions else message.author
            await self.show_ratings(message, target)


    async def _slash_reason(self, interaction, action, target):
        if not isinstance(interaction.user, discord.Member):
            return False
        ok, error = can_manage_target(interaction.guild, interaction.user, target)
        if not ok:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        label = {
            "warn": "التحذير",
            "ban": "الحظر",
            "kick": "الطرد",
            "mute": "الكتم",
            "jail": "السجن",
        }.get(action, action)
        await interaction.response.send_message(
            f"اختر سبب {label} لـ {target.mention}:",
            view=ActionReasonView(
                self,
                action,
                target,
                interaction.user.id,
                ephemeral_result=True,
            ),
            ephemeral=True,
        )
        return True

    @discord.app_commands.command(
        name="warn",
        description="إعطاء تحذير لعضو ثم اختيار السبب من قائمة"
    )
    @discord.app_commands.describe(member="العضو الذي سيحصل على التحذير")
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self._slash_reason(interaction, "warn", member)

    @discord.app_commands.command(
        name="ban",
        description="حظر عضو مع اختيار السبب"
    )
    @discord.app_commands.describe(member="العضو الذي سيتم حظره")
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self._slash_reason(interaction, "ban", member)

    @discord.app_commands.command(
        name="kick",
        description="طرد عضو مع اختيار السبب"
    )
    @discord.app_commands.describe(member="العضو الذي سيتم طرده")
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self._slash_reason(interaction, "kick", member)

    @discord.app_commands.command(
        name="mute",
        description="كتم عضو مع اختيار السبب والمدة"
    )
    @discord.app_commands.describe(member="العضو الذي سيتم كتمه")
    async def slash_mute(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self._slash_reason(interaction, "mute", member)

    @discord.app_commands.command(
        name="jail",
        description="إدخال عضو إلى السجن مع اختيار السبب"
    )
    @discord.app_commands.describe(member="العضو الذي سيتم سجنه")
    async def slash_jail(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self._slash_reason(interaction, "jail", member)

    @discord.app_commands.command(
        name="warnings",
        description="عرض تحذيرات عضو"
    )
    @discord.app_commands.describe(member="العضو المطلوب")
    async def slash_warnings(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self.show_warnings_to_interaction(interaction, member)

    async def show_warnings_to_interaction(self, interaction, target):
        rows = warning_rows(interaction.guild.id, target.id)
        if not rows:
            return await interaction.response.send_message(
                f"✅ {target.mention} ما عندوش تحذيرات نشطة.",
                ephemeral=True,
            )
        lines = [
            f"**#{row['id']}** — {row['reason']} — <@{row['moderator_id']}>"
            for row in rows[:10]
        ]
        embed = discord.Embed(
            title=f"⚠️ تحذيرات {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.yellow(),
        )
        embed.set_footer(text=f"{len(rows)}/{WARNING_LIMIT} تحذيرات نشطة")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(
        name="clear-warnings",
        description="مسح جميع التحذيرات النشطة لعضو"
    )
    @discord.app_commands.describe(member="العضو المطلوب")
    async def slash_clear_warnings(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        with connect() as con:
            cur = con.execute(
                "UPDATE warnings SET active=0 WHERE guild_id=? AND user_id=? AND active=1",
                (interaction.guild.id, member.id),
            )
            count = cur.rowcount
        await interaction.response.send_message(
            f"✅ تم مسح **{count}** تحذيرات عن {member.mention}.",
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="remove-warning",
        description="إلغاء آخر تحذير نشط لعضو"
    )
    @discord.app_commands.describe(member="العضو المطلوب")
    async def slash_remove_warning(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        with connect() as con:
            row = con.execute(
                """
                SELECT id FROM warnings
                WHERE guild_id=? AND user_id=? AND active=1
                ORDER BY id DESC LIMIT 1
                """,
                (interaction.guild.id, member.id),
            ).fetchone()
            if not row:
                return await interaction.response.send_message(
                    "❌ ما عندوش تحذيرات نشطة.",
                    ephemeral=True,
                )
            con.execute("UPDATE warnings SET active=0 WHERE id=?", (row["id"],))
        await interaction.response.send_message(
            f"✅ تم إلغاء آخر تحذير عن {member.mention}.",
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="unmute",
        description="فك الكتم عن عضو"
    )
    @discord.app_commands.describe(member="العضو المطلوب")
    async def slash_unmute(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        if not interaction.guild.me or not interaction.guild.me.guild_permissions.moderate_members:
            return await interaction.response.send_message(
                "❌ البوت ما عندوش Moderate Members.",
                ephemeral=True,
            )
        ok, error = can_manage_target(interaction.guild, interaction.user, member)
        if not ok:
            return await interaction.response.send_message(error, ephemeral=True)
        try:
            await member.timeout(None, reason=f"Unmute by {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Discord رفض فك الكتم بسبب الصلاحيات أو ترتيب الرتب.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.response.send_message("❌ تعذر فك الكتم.", ephemeral=True)
        await interaction.response.send_message(f"✅ تم فك الكتم عن {member.mention}.")

    @discord.app_commands.command(
        name="unjail",
        description="إخراج عضو من السجن وإرجاع رتبته السابقة"
    )
    @discord.app_commands.describe(member="العضو المطلوب")
    async def slash_unjail(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self.unjail_from_message(
            await self._message_proxy(interaction),
            member,
        )

    async def _message_proxy(self, interaction):
        return InteractionMessageProxy(interaction)

    @discord.app_commands.command(
        name="send",
        description="إرسال رسالة إلى روم محدد"
    )
    @discord.app_commands.describe(channel="الروم المستهدفة", message="النص المراد إرساله")
    async def slash_send(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        if not channel.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message(
                "❌ البوت ما عندوش صلاحية إرسال في هاد الروم.",
                ephemeral=True,
            )
        try:
            await channel.send(message)
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Discord رفض الإرسال.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ وقع خطأ أثناء الإرسال.", ephemeral=True)
        await interaction.response.send_message(f"✅ تم إرسال الرسالة في {channel.mention}.", ephemeral=True)

    @discord.app_commands.command(
        name="dm",
        description="إرسال رسالة خاصة إلى عضو محدد"
    )
    @discord.app_commands.describe(member="العضو المستهدف", message="الرسالة الخاصة")
    async def slash_dm(self, interaction: discord.Interaction, member: discord.Member, message: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        try:
            await member.send(
                f"رسالة من إدارة سيرفر **{interaction.guild.name}**:\n{message}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ العضو مانع الرسائل الخاصة أو Discord رفض الإرسال.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.response.send_message("❌ تعذر إرسال الرسالة الخاصة.", ephemeral=True)
        await interaction.response.send_message(f"✅ تم إرسال DM إلى {member.mention}.", ephemeral=True)

    @discord.app_commands.command(
        name="mass-dm",
        description="إرسال DM لجميع الأعضاء بعد تأكيد"
    )
    @discord.app_commands.describe(message="الرسالة التي ستصل للأعضاء")
    async def slash_mass_dm(self, interaction: discord.Interaction, message: str):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await interaction.response.send_message(
            (
                "⚠️ هذا الإجراء سيرسل DM لجميع أعضاء السيرفر غير البوتات.\n"
                f"الرسالة: {message}\n\n"
                "أكد العملية من الزر التالي."
            ),
            view=MassDMConfirmView(self, interaction.user.id, message),
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="ticket-panel",
        description="إرسال بانل التذاكر في روم محددة"
    )
    @discord.app_commands.describe(
        channel="الروم التي تريد وضع بانل التذاكر فيها"
    )
    async def slash_ticket_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الأمر للإدارة فقط.",
                ephemeral=True,
            )

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ الروم غير صالحة.",
                ephemeral=True,
            )

        perms = target_channel.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            return await interaction.response.send_message(
                "❌ البوت خاصو Send Messages وEmbed Links في هاد الروم.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "✅ جاري تجهيز بانل التذاكر...",
            ephemeral=True,
        )
        try:
            await self.create_ticket_panel_message(
                interaction.guild,
                target_channel,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord رفض إنشاء بانل التذاكر بسبب الصلاحيات.",
                ephemeral=True,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ وقع خطأ أثناء إنشاء بانل التذاكر.",
                ephemeral=True,
            )

    async def create_ticket_panel_message(self, guild, channel):
        embed = discord.Embed(
            title="🎫 الدعم والتذاكر",
            description="اضغط على **فتح تذكرة** لفتح روم خاصة مع الإدارة.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        sent = await channel.send(embed=embed, view=TicketPanelView(self))
        set_setting(
            guild.id,
            ticket_panel_channel_id=channel.id,
            ticket_panel_message_id=sent.id,
        )

    @discord.app_commands.command(
        name="set-warning-log",
        description="تعيين روم مخصصة لسجل التحذيرات"
    )
    @discord.app_commands.describe(channel="الروم التي سيستقبل فيها البوت سجل التحذيرات")
    async def slash_set_warning_log(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ هذا الإعداد لصاحب السيرفر أو Administrator فقط.",
                ephemeral=True,
            )
        set_setting(
            interaction.guild.id,
            warning_log_channel_id=channel.id,
        )
        await interaction.response.send_message(
            f"✅ تم تعيين {channel.mention} كسجل خاص بالتحذيرات.",
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="set-log",
        description="تعيين روم لوق الإدارة"
    )
    @discord.app_commands.describe(channel="روم اللوق")
    async def slash_set_log(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ هذا الإعداد لصاحب السيرفر أو Administrator فقط.", ephemeral=True)
        set_setting(interaction.guild.id, mod_log_channel_id=channel.id)
        await interaction.response.send_message(f"✅ تم تعيين {channel.mention} كلوق للإدارة.", ephemeral=True)

    @discord.app_commands.command(
        name="set-ticket",
        description="تعيين تصنيف التذاكر"
    )
    @discord.app_commands.describe(category="تصنيف التذاكر")
    async def slash_set_ticket(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ هذا الإعداد للـAdministrator فقط.", ephemeral=True)
        set_setting(interaction.guild.id, ticket_category_id=category.id)
        await interaction.response.send_message(f"✅ تم تعيين تصنيف التذاكر: **{category.name}**.", ephemeral=True)

    @discord.app_commands.command(
        name="set-jail",
        description="تعيين رتبة السجن وروم السجن"
    )
    @discord.app_commands.describe(role="رتبة السجن", channel="روم السجن الاختيارية")
    async def slash_set_jail(self, interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel | None = None):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ هذا الإعداد للـAdministrator فقط.", ephemeral=True)
        if role.is_default() or role.managed:
            return await interaction.response.send_message("❌ اختر رتبة عادية قابلة للإدارة.", ephemeral=True)
        if interaction.guild.me and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ رتبة السجن خاصها تكون تحت رتبة البوت.", ephemeral=True)
        set_setting(
            interaction.guild.id,
            jail_role_id=role.id,
            jail_channel_id=channel.id if channel else None,
        )
        text = f"✅ رتبة السجن: {role.mention}"
        if channel:
            text += f" | روم السجن: {channel.mention}"
        await interaction.response.send_message(text, ephemeral=True)

    @discord.app_commands.command(
        name="lock",
        description="قفل روم أمام الأعضاء"
    )
    @discord.app_commands.describe(channel="الروم المستهدفة، تقدر تخليها فارغة للروم الحالية")
    async def slash_lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الروم غير صالحة.", ephemeral=True)
        overwrite = target_channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        try:
            await target_channel.set_permissions(
                interaction.guild.default_role,
                overwrite=overwrite,
                reason=f"Lock by {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ البوت ما عندوش Manage Channels.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ تعذر قفل الروم.", ephemeral=True)
        await interaction.response.send_message(f"🔒 تم قفل {target_channel.mention}.")

    @discord.app_commands.command(
        name="unlock",
        description="فتح روم للأعضاء"
    )
    @discord.app_commands.describe(channel="الروم المستهدفة، تقدر تخليها فارغة للروم الحالية")
    async def slash_unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الروم غير صالحة.", ephemeral=True)
        overwrite = target_channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        try:
            await target_channel.set_permissions(
                interaction.guild.default_role,
                overwrite=overwrite,
                reason=f"Unlock by {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ البوت ما عندوش Manage Channels.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ تعذر فتح الروم.", ephemeral=True)
        await interaction.response.send_message(f"🔓 تم فتح {target_channel.mention}.")

    @discord.app_commands.command(
        name="purge",
        description="حذف عدد من الرسائل"
    )
    @discord.app_commands.describe(amount="عدد الرسائل من 1 إلى 100")
    async def slash_purge(self, interaction: discord.Interaction, amount: discord.app_commands.Range[int, 1, 100]):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=int(amount))
        except discord.Forbidden:
            return await interaction.response.send_message("❌ البوت ما عندوش Manage Messages.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ تعذر حذف الرسائل.", ephemeral=True)
        await interaction.response.send_message(f"🧹 تم حذف **{len(deleted)}** رسالة.", ephemeral=True)

    @discord.app_commands.command(
        name="rate",
        description="إرسال لوحة تقييم لموظف"
    )
    @discord.app_commands.describe(member="الموظف الذي سيحصل على التقييم")
    async def slash_rate(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await interaction.response.send_message(
            f"اختر تقييمك لـ {member.mention}:",
            view=RatingView(self, member.id, interaction.user.id),
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="ratings",
        description="عرض تقييمات موظف"
    )
    @discord.app_commands.describe(member="الموظف المطلوب")
    async def slash_ratings(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)
        await self.show_ratings_to_interaction(interaction, member)

    async def show_ratings_to_interaction(self, interaction, target):
        with connect() as con:
            rows = con.execute(
                """
                SELECT rating, note, rater_id, created_at
                FROM ratings
                WHERE guild_id=? AND target_id=?
                ORDER BY id DESC LIMIT 10
                """,
                (interaction.guild.id, target.id),
            ).fetchall()
            avg_row = con.execute(
                """
                SELECT AVG(rating) AS average, COUNT(*) AS count
                FROM ratings
                WHERE guild_id=? AND target_id=?
                """,
                (interaction.guild.id, target.id),
            ).fetchone()

        if not rows:
            return await interaction.response.send_message(
                f"⭐ ما كايناش تقييمات مسجلة لـ {target.mention}.",
                ephemeral=True,
            )

        average = float(avg_row["average"] or 0)
        lines = [
            f"⭐ **{row['rating']}/5** — {row['note'] or 'بدون ملاحظة'} — <@{row['rater_id']}>"
            for row in rows
        ]
        embed = discord.Embed(
            title=f"⭐ تقييمات {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"المتوسط: {average:.2f}/5 | الإجمالي: {avg_row['count']}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.handle_message(message)


async def start():
    token = os.getenv("MODERATION_BOT_TOKEN")
    if not token:
        print("[WARN] MODERATION_BOT_TOKEN is missing; second moderation bot is disabled.")
        return

    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True

    instance = SecondBot(
        command_prefix=PREFIX,
        intents=intents,
        case_insensitive=True,
    )

    await instance.add_cog(ModerationBot(instance))

    try:
        await instance.start(token)
    finally:
        await instance.close()


async def run():
    await start()
