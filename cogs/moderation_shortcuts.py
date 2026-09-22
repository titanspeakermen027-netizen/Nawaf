from __future__ import annotations

import re
from datetime import timedelta

import discord
from discord.ext import commands

from cogs.access_control import can_control_bot


DURATION_RE = re.compile(r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?$", re.IGNORECASE)
USER_ID_RE = re.compile(r"^(\d{15,25})$")
CHANNEL_MENTION_RE = re.compile(r"^<#(\d+)>$")
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60


class ModerationShortcuts(commands.Cog):
    """Arabic prefix shortcuts for common moderation actions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(self, message: discord.Message, content: str):
        return await message.reply(content, mention_author=False)

    @staticmethod
    def _command_and_args(content: str) -> tuple[str, list[str]]:
        parts = content.strip().split()
        if not parts:
            return "", []

        command = parts[0]
        if command[:1] in {"!", "c", "C"} and len(command) > 1:
            command = command[1:]
        return command.casefold(), parts[1:]

    @staticmethod
    def _find_member(guild: discord.Guild, args: list[str], message: discord.Message) -> tuple[discord.Member | None, int]:
        if message.mentions:
            target = message.mentions[0]
            for index, token in enumerate(args):
                if token.startswith("<@") and token.endswith(">"):
                    return target, index
            return target, 0

        if args:
            raw = args[0].strip("<@!>")
            if raw.isdigit():
                member = guild.get_member(int(raw))
                if member:
                    return member, 0
        return None, -1

    @staticmethod
    def _find_role(guild: discord.Guild, args: list[str], start: int) -> discord.Role | None:
        for index in range(max(0, start), len(args)):
            token = args[index]
            if token.startswith("<@&") and token.endswith(">"):
                role_id = token[3:-1]
                if role_id.isdigit():
                    role = guild.get_role(int(role_id))
                    if role:
                        return role

            if token.isdigit():
                role = guild.get_role(int(token))
                if role:
                    return role

        return None

    @staticmethod
    def _find_channel(guild: discord.Guild, token: str | None) -> discord.TextChannel | None:
        if not token:
            return None

        match = CHANNEL_MENTION_RE.fullmatch(token)
        if match:
            channel_id = int(match.group(1))
        elif token.isdigit():
            channel_id = int(token)
        else:
            return None

        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    @staticmethod
    def _parse_duration(value: str) -> timedelta | None:
        match = DURATION_RE.fullmatch(value.strip())
        if not match:
            return None

        amount = int(match.group(1))
        unit = (match.group(2) or "m").casefold()

        if amount <= 0:
            return None

        if unit in {"s", "sec", "secs", "second", "seconds"}:
            seconds = amount
        elif unit in {"m", "min", "mins", "minute", "minutes"}:
            seconds = amount * 60
        elif unit in {"h", "hr", "hrs", "hour", "hours"}:
            seconds = amount * 60 * 60
        else:
            seconds = amount * 24 * 60 * 60

        if seconds > MAX_TIMEOUT_SECONDS:
            return None

        return timedelta(seconds=seconds)

    @staticmethod
    def _hierarchy_error(guild: discord.Guild, moderator: discord.Member, target: discord.Member) -> str | None:
        bot_member = guild.me
        if not bot_member:
            return "❌ ما قدرتش نحدد رتبة البوت."
        if target.id == guild.owner_id:
            return "❌ ما يمكنش تنفيذ هاد العملية على مالك السيرفر."
        if target.id == moderator.id:
            return "❌ ما يمكنش تنفذ هاد العملية على راسك."
        if target.top_role >= bot_member.top_role:
            return "❌ رتبة العضو خاصها تكون تحت أعلى رتبة ديال البوت."
        return None

    @staticmethod
    def _reason(args: list[str], start: int) -> str | None:
        reason = " ".join(args[start:]).strip()
        return reason or None

    async def _lock_channel(self, message: discord.Message, channel: discord.TextChannel, lock: bool):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_channels:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Manage Channels.")

        try:
            await channel.set_permissions(
                message.guild.default_role,
                send_messages=False if lock else None,
                reason=f"Nawaf shortcut by {message.author}",
            )
            await self._reply(message, f"{'🔒 تم قفل' if lock else '🔓 تم فتح'} {channel.mention}.")
        except discord.Forbidden:
            await self._reply(message, "❌ البوت ما قدرش يبدل صلاحيات هاد الروم.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء تغيير صلاحيات الروم.")

    async def _ban(self, message: discord.Message, target: discord.Member, args: list[str], index: int):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        hierarchy_error = self._hierarchy_error(message.guild, message.author, target)
        if hierarchy_error:
            return await self._reply(message, hierarchy_error)

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.ban_members:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Ban Members.")

        reason = self._reason(args, index + 1)
        try:
            await target.ban(reason=reason or f"Nawaf ban by {message.author}", delete_message_seconds=0)
            suffix = f" السبب: **{reason}**" if reason else ""
            await self._reply(message, f"🔨 تم تبنيد {target.mention}.{suffix}")
        except discord.Forbidden:
            await self._reply(message, "❌ ما قدرتش نبنّد هاد العضو. تأكد من ترتيب الرتب وصلاحيات البوت.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء التبنيد.")

    async def _unban(self, message: discord.Message, args: list[str]):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.ban_members:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Ban Members.")

        raw = args[0].strip("<@!>") if args else ""
        if not USER_ID_RE.fullmatch(raw):
            return await self._reply(message, "❌ الاستعمال: فك_باند ID.")

        try:
            user = await self.bot.fetch_user(int(raw))
            await message.guild.unban(user, reason=f"Nawaf unban by {message.author}")
            await self._reply(message, f"✅ تم فك التبنيد عن **{user}**.")
        except discord.NotFound:
            await self._reply(message, "❌ ما لقيتش هاد العضو ضمن قائمة المبندين.")
        except discord.Forbidden:
            await self._reply(message, "❌ البوت ما قدرش يفك التبنيد.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء فك التبنيد.")

    async def _kick(self, message: discord.Message, target: discord.Member, args: list[str], index: int):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        hierarchy_error = self._hierarchy_error(message.guild, message.author, target)
        if hierarchy_error:
            return await self._reply(message, hierarchy_error)

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.kick_members:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Kick Members.")

        reason = self._reason(args, index + 1)
        try:
            await target.kick(reason=reason or f"Nawaf kick by {message.author}")
            suffix = f" السبب: **{reason}**" if reason else ""
            await self._reply(message, f"👢 تم طرد {target.mention}.{suffix}")
        except discord.Forbidden:
            await self._reply(message, "❌ ما قدرتش نطرد هاد العضو. تأكد من ترتيب الرتب وصلاحيات البوت.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء الطرد.")

    async def _role(self, message: discord.Message, target: discord.Member, role: discord.Role, add: bool):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Manage Roles.")

        if role.is_default():
            return await self._reply(message, "❌ ما يمكنش إعطاء أو إزالة رتبة @everyone.")
        if role.managed:
            return await self._reply(message, "❌ ما يمكنش تعديل رتبة مرتبطة ببوت أو تكامل.")
        if role >= bot_member.top_role:
            return await self._reply(message, "❌ الرتبة خاصها تكون تحت أعلى رتبة ديال البوت.")

        try:
            if add:
                await target.add_roles(role, reason=f"Nawaf role shortcut by {message.author}")
                await self._reply(message, f"✅ تمت إضافة {role.mention} إلى {target.mention}.")
            else:
                await target.remove_roles(role, reason=f"Nawaf role shortcut by {message.author}")
                await self._reply(message, f"✅ تمت إزالة {role.mention} من {target.mention}.")
        except discord.Forbidden:
            await self._reply(message, "❌ البوت ما قدرش يغيّر رتبة هاد العضو.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء تعديل الرتبة.")

    async def _timeout(self, message: discord.Message, target: discord.Member, args: list[str], index: int):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        hierarchy_error = self._hierarchy_error(message.guild, message.author, target)
        if hierarchy_error:
            return await self._reply(message, hierarchy_error)

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.moderate_members:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Moderate Members.")

        if len(args) <= index + 1:
            return await self._reply(message, "❌ الاستعمال: تايم @user 10m أو تايم @user 1h أو تايم @user 1d.")

        duration = self._parse_duration(args[index + 1])
        if duration is None:
            return await self._reply(message, "❌ المدة غير صحيحة. استعمل s أو m أو h أو d، والحد الأقصى 28 يوم.")

        reason = self._reason(args, index + 2)
        try:
            await target.timeout(duration, reason=reason or f"Nawaf timeout by {message.author}")
            suffix = f" السبب: **{reason}**" if reason else ""
            await self._reply(message, f"⏳ تم إعطاء تايم لـ {target.mention} لمدة **{args[index + 1]}**.{suffix}")
        except discord.Forbidden:
            await self._reply(message, "❌ ما قدرتش نعطي التايم لهاد العضو.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء إعطاء التايم.")

    async def _remove_timeout(self, message: discord.Message, target: discord.Member):
        if not can_control_bot(message.author):
            return await self._reply(message, "❌ غير الإدارة أو الرتب المصرح لها تقدر تستعمل هاد الاختصار.")

        hierarchy_error = self._hierarchy_error(message.guild, message.author, target)
        if hierarchy_error:
            return await self._reply(message, hierarchy_error)

        bot_member = message.guild.me
        if not bot_member or not bot_member.guild_permissions.moderate_members:
            return await self._reply(message, "❌ البوت ما عندوش صلاحية Moderate Members.")

        try:
            await target.timeout(None, reason=f"Nawaf remove timeout by {message.author}")
            await self._reply(message, f"✅ تم فك التايم عن {target.mention}.")
        except discord.Forbidden:
            await self._reply(message, "❌ ما قدرتش نفك التايم عن هاد العضو.")
        except discord.HTTPException:
            await self._reply(message, "❌ وقع خطأ من Discord أثناء فك التايم.")

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return

        command, args = self._command_and_args(message.content)
        if not command:
            return

        if command in {"قفل", "لوك", "lock"}:
            channel = self._find_channel(message.guild, args[0] if args else None)
            if channel is None and isinstance(message.channel, discord.TextChannel):
                channel = message.channel
            if not channel:
                return await self._reply(message, "❌ حدد روم نصية أو استعمل الأمر داخل روم نصية.")
            await self._lock_channel(message, channel, True)
            return

        if command in {"فتح", "انلوك", "unlock"}:
            channel = self._find_channel(message.guild, args[0] if args else None)
            if channel is None and isinstance(message.channel, discord.TextChannel):
                channel = message.channel
            if not channel:
                return await self._reply(message, "❌ حدد روم نصية أو استعمل الأمر داخل روم نصية.")
            await self._lock_channel(message, channel, False)
            return

        if command in {"تبنيد", "باند", "ban"}:
            target, index = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: تبنيد @user [السبب] أو تبنيد ID [السبب].")
            await self._ban(message, target, args, index)
            return

        if command in {"فك_باند", "فكباند", "unban"}:
            await self._unban(message, args)
            return

        if command in {"كيك", "kick"}:
            target, index = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: كيك @user [السبب] أو كيك ID [السبب].")
            await self._kick(message, target, args, index)
            return

        if command in {"اعطاء", "إعطاء", "اضافة", "إضافة", "رتبة"}:
            target, target_index = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: اعطاء @user @role أو اعطاء ID ROLE_ID.")
            role = self._find_role(message.guild, args, target_index + 1)
            if not role:
                return await self._reply(message, "❌ خاصك تحدد الرتبة: اعطاء @user @role.")
            await self._role(message, target, role, True)
            return

        if command in {"ازالة", "إزالة", "نزع", "شيل"}:
            target, target_index = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: ازالة @user @role أو ازالة ID ROLE_ID.")
            role = self._find_role(message.guild, args, target_index + 1)
            if not role:
                return await self._reply(message, "❌ خاصك تحدد الرتبة: ازالة @user @role.")
            await self._role(message, target, role, False)
            return

        if command in {"تايم", "timeout"}:
            target, index = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: تايم @user 10m أو تايم ID 1h.")
            await self._timeout(message, target, args, index)
            return

        if command in {"فكتايم", "فك_تايم", "untimeout"}:
            target, _ = self._find_member(message.guild, args, message)
            if not target:
                return await self._reply(message, "❌ الاستعمال: فكتايم @user أو فكتايم ID.")
            await self._remove_timeout(message, target)


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.handle_message(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationShortcuts(bot))
