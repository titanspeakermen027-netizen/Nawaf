import json
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import connect, get_config, set_config


class Jail(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.release_loop.start()

    def cog_unload(self):
        self.release_loop.cancel()

    async def _apply_jail_channel_permissions(self, guild: discord.Guild, member: discord.Member, jail_role: discord.Role, jail_channel: discord.abc.GuildChannel):
        # The jail role denies visibility everywhere and is explicitly allowed only in the configured jail room.
        for channel in guild.channels:
            try:
                if channel.id == jail_channel.id:
                    await channel.set_permissions(
                        jail_role,
                        view_channel=True,
                        send_messages=True if isinstance(channel, discord.TextChannel) else None,
                        read_message_history=True if isinstance(channel, discord.TextChannel) else None,
                        connect=True if isinstance(channel, discord.VoiceChannel) else None,
                        speak=True if isinstance(channel, discord.VoiceChannel) else None,
                        reason="Nawaf jail access room",
                    )
                else:
                    await channel.set_permissions(
                        jail_role,
                        view_channel=False,
                        send_messages=False if isinstance(channel, discord.TextChannel) else None,
                        connect=False if isinstance(channel, discord.VoiceChannel) else None,
                        reason="Nawaf jail hidden rooms",
                    )
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def jail_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        moderator: discord.Member,
        minutes: int | None,
    ):
        cfg = get_config(guild.id)
        role_id = cfg["jail_role_id"]
        channel_id = cfg["jail_channel_id"]
        if not role_id or not channel_id:
            return False, "❌ خاص الإدارة تحدد **رتبة السجن وروم السجن** أولاً باستعمال `/jail-settings`."

        jail_role = guild.get_role(role_id)
        jail_channel = guild.get_channel(channel_id)
        if not jail_role:
            return False, "❌ رتبة السجن المحددة ما بقاتش موجودة."
        if not jail_channel:
            return False, "❌ روم السجن المحددة ما بقاتش موجودة."
        if jail_role >= guild.me.top_role:
            return False, "❌ رتبة السجن خاصها تكون تحت رتبة البوت."
        if member.id == moderator.id or member.bot:
            return False, "❌ ما يمكنش تسجن هاد العضو."
        if member.top_role >= guild.me.top_role and not member.guild_permissions.administrator:
            return False, "❌ ما عنديش صلاحية كافية على رتبة هاد العضو."

        expires_at = (
            (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
            if minutes
            else None
        )
        previous_roles = [
            role.id
            for role in member.roles
            if role != guild.default_role and role < guild.me.top_role and role != jail_role
        ]
        with connect() as con:
            con.execute(
                "INSERT INTO jails(guild_id,user_id,previous_roles,expires_at,jailed_by) VALUES(?,?,?,?,?) "
                "ON CONFLICT(guild_id,user_id) DO UPDATE SET previous_roles=excluded.previous_roles,expires_at=excluded.expires_at,jailed_by=excluded.jailed_by",
                (guild.id, member.id, json.dumps(previous_roles), expires_at, moderator.id),
            )

        removable = [
            role
            for role in member.roles
            if role != guild.default_role and role < guild.me.top_role and role != jail_role
        ]
        if removable:
            try:
                await member.remove_roles(*removable, reason=f"Nawaf jail by {moderator}")
            except discord.HTTPException:
                pass

        try:
            await member.add_roles(jail_role, reason=f"Nawaf jail by {moderator}")
        except discord.HTTPException:
            return False, "❌ ما قدرتش نضيف رتبة السجن للعضو."

        await self._apply_jail_channel_permissions(guild, member, jail_role, jail_channel)
        return True, None

    async def unjail_member(self, guild: discord.Guild, member: discord.Member):
        with connect() as con:
            row = con.execute(
                "SELECT previous_roles FROM jails WHERE guild_id=? AND user_id=?",
                (guild.id, member.id),
            ).fetchone()
            con.execute("DELETE FROM jails WHERE guild_id=? AND user_id=?", (guild.id, member.id))
        if not row:
            return False

        cfg = get_config(guild.id)
        jail_role = guild.get_role(cfg["jail_role_id"]) if cfg["jail_role_id"] else None
        if jail_role and jail_role in member.roles:
            try:
                await member.remove_roles(jail_role, reason="Nawaf unjail")
            except discord.HTTPException:
                pass

        try:
            role_ids = json.loads(row["previous_roles"] or "[]")
        except (json.JSONDecodeError, TypeError):
            role_ids = []
        roles = [guild.get_role(role_id) for role_id in role_ids]
        roles = [role for role in roles if role and role < guild.me.top_role]
        if roles:
            try:
                await member.add_roles(*roles, reason="Nawaf unjail - restore roles")
            except discord.HTTPException:
                pass
        return True

    async def perform_jail(self, guild, member, moderator, minutes=None):
        return await self.jail_member(guild, member, moderator, minutes)

    async def perform_unjail(self, guild, member):
        return await self.unjail_member(guild, member)

    @tasks.loop(seconds=30)
    async def release_loop(self):
        now = datetime.now(timezone.utc)
        with connect() as con:
            rows = con.execute(
                "SELECT guild_id,user_id,expires_at FROM jails WHERE expires_at IS NOT NULL"
            ).fetchall()
        for row in rows:
            try:
                expired = datetime.fromisoformat(row["expires_at"]) <= now
            except (ValueError, TypeError):
                expired = True
            if not expired:
                continue
            guild = self.bot.get_guild(row["guild_id"])
            if not guild:
                continue
            member = guild.get_member(row["user_id"])
            if member:
                await self.unjail_member(guild, member)

    @release_loop.before_loop
    async def before_release(self):
        await self.bot.wait_until_ready()


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say-member", description="إرسال رسالة خاصة لعضو")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def say_member(self, interaction: discord.Interaction, member: discord.Member, message: str):
        try:
            await member.send(message)
        except discord.Forbidden:
            return await interaction.response.send_message("❌ العضو لا يستقبل الرسائل الخاصة.", ephemeral=True)
        await interaction.response.send_message(f"✅ تم إرسال الرسالة الخاصة إلى {member.mention}.", ephemeral=True)

    @app_commands.command(name="jail-settings", description="تحديد رتبة السجن وروم السجن")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def jail_settings(self, interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ رتبة السجن خاصها تكون تحت رتبة البوت.", ephemeral=True)
        if channel.guild.id != interaction.guild.id:
            return await interaction.response.send_message("❌ الروم خاصها تكون من نفس السيرفر.", ephemeral=True)
        set_config(interaction.guild.id, jail_role_id=role.id, jail_channel_id=channel.id)
        await interaction.response.send_message(
            f"✅ إعدادات السجن:\nرتبة السجن: {role.mention}\nروم السجن: {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(name="jail-role", description="تحديد رتبة السجن")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def jail_role(self, interaction: discord.Interaction, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ رتبة السجن خاصها تكون تحت رتبة البوت.", ephemeral=True)
        set_config(interaction.guild.id, jail_role_id=role.id)
        await interaction.response.send_message(f"✅ رتبة السجن أصبحت: {role.mention}", ephemeral=True)

    @app_commands.command(name="jail-channel", description="تحديد روم السجن")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def jail_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        set_config(interaction.guild.id, jail_channel_id=channel.id)
        await interaction.response.send_message(f"✅ روم السجن أصبحت: {channel.mention}", ephemeral=True)

    @app_commands.command(name="jail", description="سجن عضو مع إمكانية تحديد مدة بالدقائق")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def jail(self, interaction: discord.Interaction, member: discord.Member, minutes: int | None = None):
        if minutes is not None and not 1 <= minutes <= 10080:
            return await interaction.response.send_message("❌ المدة خاصها تكون بين 1 و10080 دقيقة.", ephemeral=True)
        cog = self.bot.get_cog("Jail")
        if not cog:
            return await interaction.response.send_message("❌ نظام السجن غير محمل.", ephemeral=True)
        ok, error = await cog.jail_member(interaction.guild, member, interaction.user, minutes)
        if not ok:
            return await interaction.response.send_message(error, ephemeral=True)
        duration = f" لمدة **{minutes} دقيقة**" if minutes else " **بدون مدة محددة**"
        await interaction.response.send_message(f"🔒 تم سجن {member.mention}{duration}.")

    @app_commands.command(name="unjail", description="فك السجن عن عضو واسترجاع رتبه")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unjail(self, interaction: discord.Interaction, member: discord.Member):
        cog = self.bot.get_cog("Jail")
        if not cog:
            return await interaction.response.send_message("❌ نظام السجن غير محمل.", ephemeral=True)
        found = await cog.unjail_member(interaction.guild, member)
        if not found:
            return await interaction.response.send_message("❌ هاد العضو ماشي مسجون عندي.", ephemeral=True)
        await interaction.response.send_message(f"🔓 تم فك السجن عن {member.mention}.")


async def setup(bot):
    await bot.add_cog(Jail(bot))
    await bot.add_cog(Moderation(bot))
