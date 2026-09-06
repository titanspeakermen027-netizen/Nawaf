import time

import discord
from discord import app_commands
from discord.ext import commands

from database import connect


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_xp = {}

    @staticmethod
    def level_from_xp(xp: int) -> int:
        return int((max(0, xp) // 100) ** 0.5)

    async def apply_rewards(self, member: discord.Member, new_level: int):
        with connect() as con:
            rows = con.execute(
                "SELECT level, role_id FROM level_rewards WHERE guild_id=? AND level<=? AND enabled=1 ORDER BY level",
                (member.guild.id, new_level),
            ).fetchall()
        for row in rows:
            role = member.guild.get_role(row["role_id"])
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Nawaf level reward: level {row['level']}")
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self.last_xp.get(key, 0) < 30:
            return
        self.last_xp[key] = now

        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO xp(guild_id,user_id,xp,level) VALUES(?,?,0,0)",
                (message.guild.id, message.author.id),
            )
            row = con.execute(
                "SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",
                (message.guild.id, message.author.id),
            ).fetchone()
            new_xp = row["xp"] + 10
            new_level = self.level_from_xp(new_xp)
            con.execute(
                "UPDATE xp SET xp=?,level=? WHERE guild_id=? AND user_id=?",
                (new_xp, new_level, message.guild.id, message.author.id),
            )

        if new_level > row["level"]:
            await self.apply_rewards(message.author, new_level)
            await message.channel.send(
                f"🎉 مبروك {message.author.mention}! وصلت للمستوى **{new_level}**."
            )

    @app_commands.command(name="level", description="عرض مستواك أو مستوى عضو")
    async def level(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        with connect() as con:
            row = con.execute(
                "SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",
                (interaction.guild.id, member.id),
            ).fetchone()
            rewards = con.execute(
                "SELECT level,role_id FROM level_rewards WHERE guild_id=? AND level<=? AND enabled=1 ORDER BY level DESC",
                (interaction.guild.id, row["level"] if row else 0),
            ).fetchall()

        xp = row["xp"] if row else 0
        current_level = row["level"] if row else 0
        next_level_xp = ((current_level + 1) ** 2) * 100
        remaining = max(0, next_level_xp - xp)
        role_text = "، ".join(f"Lv.{r['level']} → <@&{r['role_id']}>" for r in rewards[:5]) or "لا توجد رتب مستوى بعد"

        embed = discord.Embed(title=f"📈 مستوى {member.display_name}", color=discord.Color.blurple())
        embed.add_field(name="المستوى", value=f"**{current_level}**", inline=True)
        embed.add_field(name="XP", value=f"**{xp}**", inline=True)
        embed.add_field(name="XP للمستوى القادم", value=f"**{remaining}**", inline=True)
        embed.add_field(name="الرتب المكتسبة", value=role_text, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="level-reward-set", description="ربط مستوى برتبة تلقائية")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reward_set(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "❌ البوت ما يقدرش يعطي هاد الرتبة. خاص رتبة البوت تكون فوقها.", ephemeral=True
            )
        with connect() as con:
            con.execute(
                "INSERT INTO level_rewards(guild_id,level,role_id,enabled) VALUES(?,?,?,1) "
                "ON CONFLICT(guild_id,level) DO UPDATE SET role_id=excluded.role_id,enabled=1",
                (interaction.guild.id, level, role.id),
            )
        await interaction.response.send_message(
            f"✅ منين يوصل العضو للمستوى **{level}** غادي ياخذ {role.mention}.", ephemeral=True
        )

    @app_commands.command(name="level-image-role", description="تحديد رتبة الصور التي تُعطى عند مستوى معين")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def image_role(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "❌ البوت ما يقدرش يعطي هاد الرتبة. خاص رتبة البوت تكون فوقها.", ephemeral=True
            )
        with connect() as con:
            con.execute(
                "INSERT INTO level_rewards(guild_id,level,role_id,enabled) VALUES(?,?,?,1) "
                "ON CONFLICT(guild_id,level) DO UPDATE SET role_id=excluded.role_id,enabled=1",
                (interaction.guild.id, level, role.id),
            )
        await interaction.response.send_message(
            f"✅ رتبة الصور {role.mention} غادي تتعطى تلقائياً منين يوصل العضو للمستوى **{level}**.", ephemeral=True
        )

    @app_commands.command(name="level-reward-remove", description="حذف رتبة مرتبطة بمستوى")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reward_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]):
        with connect() as con:
            con.execute("DELETE FROM level_rewards WHERE guild_id=? AND level=?", (interaction.guild.id, level))
        await interaction.response.send_message(f"✅ تم حذف مكافأة المستوى **{level}**.", ephemeral=True)

    @app_commands.command(name="level-rewards", description="عرض رتب المستويات المفعلة")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rewards(self, interaction: discord.Interaction):
        with connect() as con:
            rows = con.execute(
                "SELECT level,role_id FROM level_rewards WHERE guild_id=? AND enabled=1 ORDER BY level",
                (interaction.guild.id,),
            ).fetchall()
        if not rows:
            return await interaction.response.send_message("❌ ما كايناش رتب مرتبطة بالمستويات.", ephemeral=True)
        text = "\n".join(f"• المستوى **{r['level']}** → <@&{r['role_id']}>" for r in rows[:50])
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
