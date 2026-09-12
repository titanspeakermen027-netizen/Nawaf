from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import connect
from cogs.access_control import can_control_bot

BOT_OWNER_ID = 1472570059367911587
MIN_ROULETTE_PLAYERS = 4
DEFAULT_ROULETTE_MAX = 12
ABSOLUTE_ROULETTE_MAX = 50

SUPPORT_INVITES = (
    "https://discord.gg/T7CvRtSJER",
)

PREMIUM_CONTACTS = (
    813789061068095550,
    1522325331623542862,
    1472570059367911587,
)

UPSELL_TEXT = (
    "**يمكنك تغيير الحد الأقصى لعدد اللاعبين، وهذه الميزة حصرية لمشتركي Premium فقط.**\n\n"
    "للتواصل للحصول على Premium، ادخل إلى السيرفر التالي:\n"
    f"{SUPPORT_INVITES[0]}\n\n"
    "افتح تذكرة واذكر أحد الأشخاص التاليين:\n"
    f"<@{PREMIUM_CONTACTS[0]}>\n"
    f"<@{PREMIUM_CONTACTS[1]}>\n"
    f"<@{PREMIUM_CONTACTS[2]}>\n\n"
    "ثم ادفع له بإحدى طرق الدفع المتاحة، وسيتم منحك Premium للمدة التي اشتريتها."
)

DURATION_RE = re.compile(r"^(\d+)(y|mo|w|d|h|m|s)$", re.IGNORECASE)
DURATION_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
    "mo": 60 * 60 * 24 * 30,
    "y": 60 * 60 * 24 * 365,
}


def ensure_premium_table() -> None:
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_premium (
                guild_id INTEGER PRIMARY KEY,
                activated_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                activated_by INTEGER NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS roulette_premium_config (
                guild_id INTEGER PRIMARY KEY,
                maximum_players INTEGER NOT NULL DEFAULT 12
            )
            """
        )


def get_premium_expiry(guild_id: int) -> int | None:
    ensure_premium_table()
    with connect() as con:
        row = con.execute(
            "SELECT expires_at FROM guild_premium WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return None
    expiry = int(row[0])
    if expiry <= int(time.time()):
        with connect() as con:
            con.execute("DELETE FROM guild_premium WHERE guild_id=?", (guild_id,))
        return None
    return expiry


def is_premium(guild_id: int) -> bool:
    return get_premium_expiry(guild_id) is not None


def get_roulette_max(guild_id: int) -> int:
    if not is_premium(guild_id):
        return DEFAULT_ROULETTE_MAX
    with connect() as con:
        row = con.execute(
            "SELECT maximum_players FROM roulette_premium_config WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    if not row:
        return DEFAULT_ROULETTE_MAX
    return max(MIN_ROULETTE_PLAYERS, min(ABSOLUTE_ROULETTE_MAX, int(row[0])))


def set_roulette_max(guild_id: int, maximum_players: int) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO roulette_premium_config(guild_id, maximum_players)
            VALUES(?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET maximum_players=excluded.maximum_players
            """,
            (guild_id, maximum_players),
        )


def parse_duration(value: str) -> int | None:
    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    amount = int(match.group(1))
    if amount <= 0:
        return None
    return amount * DURATION_SECONDS[match.group(2).lower()]


def format_dt(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# All permission flags exposed by the installed discord.py version are shown.
# Arabic names are provided for Discord's current permission set; unknown/new
# flags automatically fall back to a readable English label so they are never omitted.
ARABIC_PERMISSION_NAMES = {
    "administrator": "Administrator",
    "view_channel": "عرض القنوات",
    "manage_channels": "إدارة القنوات",
    "manage_roles": "إدارة الرتب",
    "manage_permissions": "إدارة الصلاحيات",
    "manage_webhooks": "إدارة الويبهوكات",
    "manage_expressions": "إدارة الإيموجي والملصقات",
    "manage_guild": "إدارة الخادم",
    "view_audit_log": "عرض سجل التدقيق",
    "view_guild_insights": "عرض إحصائيات الخادم",
    "manage_nicknames": "إدارة الألقاب",
    "change_nickname": "تغيير اللقب",
    "kick_members": "طرد الأعضاء",
    "ban_members": "حظر الأعضاء",
    "moderate_members": "إدارة الأعضاء",
    "create_instant_invite": "إنشاء الدعوات",
    "send_messages": "إرسال الرسائل",
    "send_messages_in_threads": "إرسال الرسائل في المواضيع",
    "create_public_threads": "إنشاء المواضيع العامة",
    "create_private_threads": "إنشاء المواضيع الخاصة",
    "embed_links": "تضمين الروابط",
    "attach_files": "إرفاق الملفات",
    "read_message_history": "قراءة سجل الرسائل",
    "mention_everyone": "ذكر الجميع",
    "use_external_emojis": "استخدام الإيموجي الخارجي",
    "use_external_stickers": "استخدام الملصقات الخارجية",
    "add_reactions": "إضافة التفاعلات",
    "connect": "الاتصال الصوتي",
    "speak": "التحدث",
    "stream": "البث",
    "use_voice_activation": "استخدام تفعيل الصوت",
    "priority_speaker": "المتحدث ذو الأولوية",
    "mute_members": "كتم الأعضاء",
    "deafen_members": "كتم سماع الأعضاء",
    "move_members": "تحريك الأعضاء",
    "use_soundboard": "استخدام لوحة الأصوات",
    "use_external_sounds": "استخدام الأصوات الخارجية",
    "request_to_speak": "طلب التحدث",
    "manage_events": "إدارة الأحداث",
    "send_polls": "إرسال الاستطلاعات",
    "create_events": "إنشاء الأحداث",
    "use_external_apps": "استخدام التطبيقات الخارجية",
}


def pretty_permission_name(flag: str) -> str:
    translated = ARABIC_PERMISSION_NAMES.get(flag)
    if translated:
        return translated
    words = flag.replace("_", " ").strip().title()
    return words or flag


def all_permission_items() -> list[tuple[str, str]]:
    # discord.py keeps the complete supported permission set in VALID_FLAGS.
    # This also makes the UI automatically include newly added Discord permissions.
    return [(pretty_permission_name(flag), flag) for flag in discord.Permissions.VALID_FLAGS]


def permissions_from_flags(flags: list[str]) -> discord.Permissions:
    permissions = discord.Permissions.none()
    valid = discord.Permissions.VALID_FLAGS
    for flag in flags:
        if flag in valid:
            setattr(permissions, flag, True)
    return permissions


class RolePermissionsView(discord.ui.View):
    def __init__(self, cog: "Premium", target_role: discord.Role | None, role_name: str | None):
        super().__init__(timeout=300)
        self.cog = cog
        self.target_role = target_role
        self.role_name = role_name
        self.selected: set[str] = set()
        self.permission_selects: list[discord.ui.Select] = []
        self.labels_by_flag = {flag: label for label, flag in all_permission_items()}

        items = all_permission_items()
        # Discord select menus allow at most 25 options each. Split all
        # permissions across multiple menus so every Discord permission is available.
        chunks = [items[index:index + 25] for index in range(0, len(items), 25)]
        for chunk_index, chunk in enumerate(chunks, start=1):
            options = [
                discord.SelectOption(
                    label=label[:100],
                    value=flag,
                    default=flag in self.selected,
                )
                for label, flag in chunk
            ]
            select = discord.ui.Select(
                placeholder=f"حدد الصلاحيات — القائمة {chunk_index}",
                min_values=0,
                max_values=len(options),
                options=options,
                row=chunk_index - 1,
            )
            select.callback = self.select_permissions
            self.permission_selects.append(select)
            self.add_item(select)

        self.apply_button = discord.ui.Button(
            label="تطبيق الصلاحيات",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=min(len(chunks), 4),
        )
        self.apply_button.callback = self.apply
        self.add_item(self.apply_button)

    async def select_permissions(self, interaction: discord.Interaction):
        # Replace only the values belonging to the changed menu, keeping selections
        # made in the other permission menus intact.
        for select in self.permission_selects:
            chunk_flags = {option.value for option in select.options}
            self.selected.difference_update(chunk_flags)
            self.selected.update(select.values)
            for option in select.options:
                option.default = option.value in self.selected

        preview = (
            "بدون صلاحيات"
            if not self.selected
            else "\n".join(
                f"• {self.labels_by_flag.get(flag, flag)}"
                for flag in sorted(self.selected, key=lambda value: self.labels_by_flag.get(value, value))
            )
        )
        await interaction.response.edit_message(
            content=f"**الصلاحيات المحددة ({len(self.selected)}):**\n{preview}\n\nاضغط **تطبيق الصلاحيات** للتأكيد.",
            view=self,
        )

    async def apply(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة المخولة.", ephemeral=True)
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return await interaction.response.send_message("❌ البوت يحتاج صلاحية إدارة الرتب.", ephemeral=True)

        perms = permissions_from_flags(list(self.selected))
        target = self.target_role
        if target is None:
            name = (self.role_name or "").strip()[:100]
            if not name:
                return await interaction.response.send_message("❌ حدد اسم الرتبة الجديدة أو اختر رتبة موجودة.", ephemeral=True)
            target = await guild.create_role(name=name, permissions=perms, reason=f"Role configured by {interaction.user}")
            action = "إنشاء"
        else:
            if target >= me.top_role:
                return await interaction.response.send_message("❌ لا يمكن للبوت تعديل رتبة أعلى من رتبته أو مساوية لها.", ephemeral=True)
            await target.edit(permissions=perms, reason=f"Role permissions configured by {interaction.user}")
            action = "تعديل"

        shown = (
            "بدون صلاحيات"
            if not self.selected
            else "\n".join(
                f"• {self.labels_by_flag.get(flag, flag)}"
                for flag in sorted(self.selected, key=lambda value: self.labels_by_flag.get(value, value))
            )
        )
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ تم {action} الرتبة {target.mention} بنجاح.\n\n**الصلاحيات ({len(self.selected)}):**\n{shown}",
            view=self,
        )
        self.stop()


class Premium(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_premium_table()

    @app_commands.command(
        name="maximum-number-players-roullete",
        description="تحديد الحد الأقصى للاعبين في فعالية الروليت لمشتركي Premium",
    )
    @app_commands.describe(maximum="الحد الأقصى من 4 إلى 50 لاعباً")
    async def maximum_number_players_roullete(
        self,
        interaction: discord.Interaction,
        maximum: app_commands.Range[int, MIN_ROULETTE_PLAYERS, ABSOLUTE_ROULETTE_MAX],
    ):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل الخوادم فقط.", ephemeral=True)
        if not is_premium(interaction.guild.id):
            return await interaction.response.send_message(UPSELL_TEXT, ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message(
                "❌ غير مالك السيرفر أو الإدارة أو رتب التحكم تقدر تعدل الحد الأقصى.", ephemeral=True
            )
        set_roulette_max(interaction.guild.id, int(maximum))
        await interaction.response.send_message(
            f"✅ تم تحديد الحد الأقصى للمشاركين على **{maximum} لاعباً**.", ephemeral=True
        )

    @app_commands.command(name="premium-role", description="إنشاء رتبة أو تعديل صلاحيات رتبة")
    @app_commands.describe(
        role_name="اسم الرتبة الجديدة (اتركه فارغاً إذا اخترت رتبة)",
        role="اختر رتبة موجودة لتعديلها",
    )
    async def premium_role(
        self,
        interaction: discord.Interaction,
        role_name: str | None = None,
        role: discord.Role | None = None,
    ):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة المخولة بالتحكم في البوت.", ephemeral=True)
        if role is None and not (role_name or "").strip():
            return await interaction.response.send_message("❌ اختر رتبة موجودة أو اكتب اسم رتبة جديدة.", ephemeral=True)
        view = RolePermissionsView(self, role, role_name)
        await interaction.response.send_message(
            "**حدد صلاحيات الرتبة من القوائم التالية.**\nجميع صلاحيات Discord متاحة، بما فيها **Administrator**. يمكنك تحديد أي عدد من الصلاحيات، والاختيارات بين القوائم تبقى محفوظة.",
            view=view,
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        content = message.content.strip()
        if not content.lower().startswith("prm ") or message.author.id != BOT_OWNER_ID:
            return
        seconds = parse_duration(content[4:].strip())
        if seconds is None:
            await message.reply("❌ الصيغة غير صحيحة. مثال: `prm 1mo` أو `prm 1y`.", mention_author=False)
            return
        now = int(time.time())
        expires = now + seconds
        with connect() as con:
            con.execute(
                """
                INSERT INTO guild_premium(guild_id, activated_at, expires_at, activated_by)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    activated_at=excluded.activated_at,
                    expires_at=excluded.expires_at,
                    activated_by=excluded.activated_by
                """,
                (message.guild.id, now, expires, BOT_OWNER_ID),
            )
        embed = discord.Embed(
            title="💎 تم تفعيل Premium",
            description=(
                f"تم تفعيل Premium لخادم **{message.guild.name}**.\n\n"
                f"**تاريخ التفعيل:** {format_dt(now)}\n"
                f"**تاريخ الانتهاء:** {format_dt(expires)}"
            ),
            color=discord.Color.gold(),
        )
        await message.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Premium(bot))
