from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

ROLES_FILE = Path("dmall_roles.json")
DM_DELAY_MIN = 2.5
DM_DELAY_MAX = 4.0
DM_TIMEOUT = 15
BATCH_SIZE = 20
BATCH_BREAK_MIN = 10
BATCH_BREAK_MAX = 20


def load_roles() -> dict[str, list[str]]:
    if not ROLES_FILE.exists():
        return {}
    try:
        data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_roles(data: dict[str, list[str]]) -> None:
    ROLES_FILE.write_text(
        json.dumps(data, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )


class DMALL(commands.Cog):
    """DM all non-bot members with per-role permission control."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.allowed_roles = load_roles()
        self.running_guilds: set[int] = set()

    def guild_roles(self, guild_id: int) -> list[str]:
        key = str(guild_id)
        roles = self.allowed_roles.setdefault(key, [])
        return roles

    def has_permission(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        allowed = set(self.guild_roles(member.guild.id))
        return any(str(role.id) in allowed for role in member.roles)

    def is_admin(self, member: discord.Member) -> bool:
        return member.guild_permissions.administrator

    async def send_one(self, member: discord.Member, text: str) -> str:
        if member.bot:
            return "skipped_bot"
        payload = f"{text}\n\n<@{member.id}>"
        try:
            await asyncio.wait_for(
                member.send(
                    payload,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                        replied_user=False,
                    ),
                ),
                timeout=DM_TIMEOUT,
            )
            return "sent"
        except (discord.Forbidden, discord.NotFound):
            return "skipped_closed"
        except asyncio.TimeoutError:
            return "failed"
        except discord.HTTPException as exc:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                await asyncio.sleep(min(float(retry_after), 30.0))
            return "failed"
        except Exception:
            return "failed"

    async def send_all(self, guild: discord.Guild, text: str) -> tuple[int, int, int, int]:
        sent = failed = skipped_bots = skipped_closed = 0
        try:
            await asyncio.wait_for(guild.chunk(), timeout=20)
        except Exception:
            pass

        members = list(guild.members)
        for member in members:
            result = await self.send_one(member, text)
            if result == "sent":
                sent += 1
            elif result == "skipped_bot":
                skipped_bots += 1
                continue
            elif result == "skipped_closed":
                skipped_closed += 1
            else:
                failed += 1

            if sent and sent % BATCH_SIZE == 0:
                await asyncio.sleep(random.uniform(BATCH_BREAK_MIN, BATCH_BREAK_MAX))
            else:
                await asyncio.sleep(random.uniform(DM_DELAY_MIN, DM_DELAY_MAX))

        return sent, failed, skipped_bots, skipped_closed

    @app_commands.command(name="dmall", description="إرسال رسالة خاصة إلى أعضاء السيرفر")
    @app_commands.describe(message="الرسالة التي تريد إرسالها")
    async def dmall(self, interaction: discord.Interaction, message: str):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفر.", ephemeral=True)
        if not self.has_permission(interaction.user):
            return await interaction.response.send_message(
                "❌ تحتاج Administrator أو رتبة مسموحة باستعمال `/dmall`.",
                ephemeral=True,
            )
        if interaction.guild.id in self.running_guilds:
            return await interaction.response.send_message(
                "⚠️ توجد عملية DMALL تعمل حالياً في هذا السيرفر.",
                ephemeral=True,
            )

        self.running_guilds.add(interaction.guild.id)
        await interaction.response.send_message("📨 بدأت عملية إرسال الرسائل الخاصة...", ephemeral=True)
        try:
            sent, failed, skipped_bots, skipped_closed = await self.send_all(interaction.guild, message)
            embed = discord.Embed(
                title="📨 DMALL Completed",
                description="تم الانتهاء من عملية الإرسال.",
            )
            embed.add_field(name="✅ تم الإرسال", value=str(sent), inline=True)
            embed.add_field(name="❌ فشل", value=str(failed), inline=True)
            embed.add_field(name="🔒 الخاص مغلق", value=str(skipped_closed), inline=True)
            embed.add_field(name="🤖 بوتات", value=str(skipped_bots), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
        finally:
            self.running_guilds.discard(interaction.guild.id)

    @app_commands.command(name="dmall-role-add", description="إضافة رتبة مسموح لها باستعمال DMALL")
    @app_commands.describe(role="الرتبة المسموح لها")
    async def role_add(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not self.is_admin(interaction.user):
            return await interaction.response.send_message("❌ يجب أن تمتلك Administrator.", ephemeral=True)
        roles = self.guild_roles(interaction.guild.id)
        role_id = str(role.id)
        if role_id in roles:
            return await interaction.response.send_message("⚠️ هذه الرتبة مضافة بالفعل.", ephemeral=True)
        roles.append(role_id)
        save_roles(self.allowed_roles)
        await interaction.response.send_message(f"✅ تم السماح لـ {role.mention} باستعمال `/dmall`.", ephemeral=True)

    @app_commands.command(name="dmall-role-remove", description="حذف رتبة من صلاحيات DMALL")
    @app_commands.describe(role="الرتبة التي تريد حذفها")
    async def role_remove(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not self.is_admin(interaction.user):
            return await interaction.response.send_message("❌ يجب أن تمتلك Administrator.", ephemeral=True)
        roles = self.guild_roles(interaction.guild.id)
        role_id = str(role.id)
        if role_id not in roles:
            return await interaction.response.send_message("⚠️ هذه الرتبة غير مضافة.", ephemeral=True)
        roles.remove(role_id)
        save_roles(self.allowed_roles)
        await interaction.response.send_message(f"✅ تم حذف {role.mention} من صلاحيات `/dmall`.", ephemeral=True)

    @app_commands.command(name="dmall-roles", description="عرض الرتب المسموح لها باستعمال DMALL")
    async def roles(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not self.is_admin(interaction.user):
            return await interaction.response.send_message("❌ يجب أن تمتلك Administrator.", ephemeral=True)
        role_mentions = []
        for raw_id in self.guild_roles(interaction.guild.id):
            role = interaction.guild.get_role(int(raw_id))
            if role:
                role_mentions.append(role.mention)
        text = "\n".join(f"• {role}" for role in role_mentions) or "لا توجد رتب مسموحة حالياً."
        embed = discord.Embed(title="🔐 رتب DMALL المسموح لها", description=text)
        embed.set_footer(text="Administrator مسموح له دائماً")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DMALL(bot))
