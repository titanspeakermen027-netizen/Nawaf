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

def parse_role_permissions(value: str | None) -> discord.Permissions:
    if not value or value.strip().lower() in {"none", "بدون", "لا شيء", "-"}:
        return discord.Permissions.none()
    names = [part.strip().lower().replace(" ", "_") for part in value.split(",") if part.strip()]
    if len(names) > 10:
        raise ValueError("لا يمكن تحديد أكثر من 10 صلاحيات.")
    permissions = discord.Permissions.none()
    invalid = []
    for name in names:
        if name not in discord.Permissions.VALID_FLAGS:
            invalid.append(name)
            continue
        setattr(permissions, name, True)
    if invalid:
        raise ValueError("صلاحيات غير صحيحة: " + ", ".join(invalid))
    return permissions

def role_permission_names(perms: discord.Permissions) -> list[str]:
    return [name for name, enabled in perms if enabled]

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

    @app_commands.command(name="premium-role", description="إنشاء رتبة أو تعديل صلاحيات رتبة موجودة")
    @app_commands.describe(role_name="اسم الرتبة الجديدة", role="رتبة موجودة لتعديلها", permissions="من 1 إلى 10 صلاحيات مفصولة بفاصلة، أو اتركها فارغة بدون صلاحيات")
    async def premium_role(self, interaction: discord.Interaction, role_name: str | None = None, role: discord.Role | None = None, permissions: str | None = None):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة المخولة بالتحكم في البوت.", ephemeral=True)
        if not interaction.guild.me.guild_permissions.manage_roles:
            return await interaction.response.send_message("❌ البوت يحتاج صلاحية إدارة الرتب.", ephemeral=True)
        try:
            perms = parse_role_permissions(permissions)
        except ValueError as exc:
            return await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        target = role
        if target is None:
            name = (role_name or "Premium").strip()[:100]
            if not name:
                return await interaction.response.send_message("❌ حدد اسم الرتبة الجديدة.", ephemeral=True)
            target = await interaction.guild.create_role(name=name, permissions=perms, reason=f"Role created by {interaction.user}")
            action = "إنشاء"
        else:
            if target >= interaction.guild.me.top_role:
                return await interaction.response.send_message("❌ لا يمكن للبوت تعديل هذه الرتبة لأنها أعلى من رتبة البوت أو مساوية لها.", ephemeral=True)
            await target.edit(permissions=perms, reason=f"Role updated by {interaction.user}")
            action = "تعديل"
        enabled = role_permission_names(target.permissions)
        description = "بدون صلاحيات." if not enabled else "\n".join(f"• {name}" for name in enabled)
        await interaction.response.send_message(f"✅ تم {action} الرتبة {target.mention} بنجاح.\n\n**الصلاحيات:**\n{description}", ephemeral=True)

    @app_commands.command(name="premium-role-permissions", description="عرض أسماء الصلاحيات المتاحة")
    async def premium_role_permissions(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not can_control_bot(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة.", ephemeral=True)
        names = sorted(discord.Permissions.VALID_FLAGS.keys())
        text = ", ".join(names)
        await interaction.response.send_message("استخدم أسماء الصلاحيات بالإنجليزية ومفصولة بفاصلة. يمكنك تحديد من 1 إلى 10، أو ترك الحقل فارغاً بدون صلاحيات.\n\n" + text[:3800], ephemeral=True)

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
