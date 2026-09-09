from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import connect


BOT_CONTROL_MAX_ROLES = 2


def init_access_tables() -> None:
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_control_roles (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS event_manager_roles (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            )
            """
        )


def get_bot_control_roles(guild_id: int) -> set[int]:
    init_access_tables()
    with connect() as con:
        rows = con.execute(
            "SELECT role_id FROM bot_control_roles WHERE guild_id=?",
            (guild_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


def get_event_manager_roles(guild_id: int) -> set[int]:
    init_access_tables()
    with connect() as con:
        rows = con.execute(
            "SELECT role_id FROM event_manager_roles WHERE guild_id=?",
            (guild_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


def _has_any_role(member: discord.Member, role_ids: set[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


def can_control_bot(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return _has_any_role(member, get_bot_control_roles(member.guild.id))


def can_manage_events(member: discord.Member) -> bool:
    if can_control_bot(member):
        return True
    return _has_any_role(member, get_event_manager_roles(member.guild.id))


def can_use_game_commands(member: discord.Member) -> bool:
    return can_manage_events(member)


class AccessControl(commands.GroupCog, group_name="bot-access"):
    """Central permission configuration for bot controllers and event managers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_access_tables()

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفر.", ephemeral=True)
            return False
        if not can_control_bot(interaction.user):
            await interaction.response.send_message("❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الإعداد.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="add", description="إضافة رتبة للتحكم في البوت")
    @app_commands.describe(role="الرتبة التي يمكنها التحكم في البوت")
    async def add(self, interaction: discord.Interaction, role: discord.Role):
        assert interaction.guild is not None
        roles = get_bot_control_roles(interaction.guild.id)
        if role.id in roles:
            return await interaction.response.send_message("⚠️ هذه الرتبة مضافة بالفعل.", ephemeral=True)
        if len(roles) >= BOT_CONTROL_MAX_ROLES:
            return await interaction.response.send_message(
                f"❌ يمكنك تحديد حد أقصى **{BOT_CONTROL_MAX_ROLES}** رتب للتحكم في البوت.",
                ephemeral=True,
            )
        with connect() as con:
            con.execute(
                "INSERT INTO bot_control_roles(guild_id,role_id) VALUES(?,?)",
                (interaction.guild.id, role.id),
            )
        await interaction.response.send_message(f"✅ تم إعطاء {role.mention} صلاحية التحكم في البوت.", ephemeral=True)

    @app_commands.command(name="remove", description="حذف رتبة من رتب التحكم في البوت")
    @app_commands.describe(role="الرتبة التي تريد إزالة صلاحيتها")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        assert interaction.guild is not None
        with connect() as con:
            cur = con.execute(
                "DELETE FROM bot_control_roles WHERE guild_id=? AND role_id=?",
                (interaction.guild.id, role.id),
            )
        message = f"✅ تم حذف صلاحية {role.mention}." if cur.rowcount else "⚠️ هذه الرتبة ليست ضمن رتب التحكم."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="list", description="عرض رتب التحكم في البوت")
    async def list_roles(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        roles = sorted(get_bot_control_roles(interaction.guild.id))
        text = "\n".join(f"• <@&{role_id}>" for role_id in roles) or "لا توجد رتب مخصصة حالياً."
        await interaction.response.send_message(f"🛡️ **رتب التحكم في البوت**\n{text}", ephemeral=True)


class EventAccess(commands.GroupCog, group_name="event-role"):
    """Role that can create/start game events without full bot-control access."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_access_tables()

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفر.", ephemeral=True)
            return False
        if not can_control_bot(interaction.user):
            await interaction.response.send_message("❌ غير الإدارة أو رتب التحكم في البوت تقدر تعدل رئيس الفعاليات.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="set", description="تحديد رتبة رئيس الفعاليات")
    @app_commands.describe(role="الرتبة التي ستتمكن من تشغيل الفعاليات")
    async def set_role(self, interaction: discord.Interaction, role: discord.Role):
        assert interaction.guild is not None
        with connect() as con:
            con.execute("DELETE FROM event_manager_roles WHERE guild_id=?", (interaction.guild.id,))
            con.execute(
                "INSERT INTO event_manager_roles(guild_id,role_id) VALUES(?,?)",
                (interaction.guild.id, role.id),
            )
        await interaction.response.send_message(f"✅ تم تحديد {role.mention} كرئيس الفعاليات.", ephemeral=True)

    @app_commands.command(name="clear", description="إلغاء رتبة رئيس الفعاليات")
    async def clear(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        with connect() as con:
            con.execute("DELETE FROM event_manager_roles WHERE guild_id=?", (interaction.guild.id,))
        await interaction.response.send_message("✅ تم إلغاء رتبة رئيس الفعاليات.", ephemeral=True)

    @app_commands.command(name="show", description="عرض رتبة رئيس الفعاليات الحالية")
    async def show(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        roles = sorted(get_event_manager_roles(interaction.guild.id))
        text = "\n".join(f"• <@&{role_id}>" for role_id in roles) or "لا توجد رتبة محددة حالياً."
        await interaction.response.send_message(f"🎮 **رئيس الفعاليات**\n{text}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AccessControl(bot))
    await bot.add_cog(EventAccess(bot))
