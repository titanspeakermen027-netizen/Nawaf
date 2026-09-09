from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import connect, get_config, set_config
from cogs.access_control import can_control_bot


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def ensure(guild_id: int, user_id: int) -> None:
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO balances(guild_id,user_id,balance) VALUES(?,?,0)",
                (guild_id, user_id),
            )

    @staticmethod
    async def require_control(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفر.", ephemeral=True)
            return False
        if not can_control_bot(interaction.user):
            await interaction.response.send_message("❌ غير الإدارة أو رتب التحكم في البوت تقدر تستعمل هاد الأمر.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="balance", description="عرض تفاصيل رصيدك أو رصيد عضو")
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        assert interaction.guild is not None
        member = member or interaction.user
        self.ensure(interaction.guild.id, member.id)
        cfg = get_config(interaction.guild.id)
        with connect() as con:
            row = con.execute(
                "SELECT balance FROM balances WHERE guild_id=? AND user_id=?",
                (interaction.guild.id, member.id),
            ).fetchone()
        embed = discord.Embed(title=f"💰 محفظة {member.display_name}", color=discord.Color.gold())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="الرصيد", value=f"**{row['balance']:,}** {cfg['currency_name']} {cfg['currency_symbol']}", inline=False)
        embed.set_footer(text="يمكن استعمال العملة للتحويل والشراء من المتجر.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description="تحويل العملة لعضو")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        assert interaction.guild is not None
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message("❌ لا يمكن التحويل لهذا العضو.", ephemeral=True)
        guild_id = interaction.guild.id
        self.ensure(guild_id, interaction.user.id)
        self.ensure(guild_id, member.id)
        with connect() as con:
            sender = con.execute("SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (guild_id, interaction.user.id)).fetchone()
            if sender["balance"] < amount:
                return await interaction.response.send_message("❌ رصيدك غير كافٍ.", ephemeral=True)
            con.execute("UPDATE balances SET balance=balance-? WHERE guild_id=? AND user_id=?", (amount, guild_id, interaction.user.id))
            con.execute("UPDATE balances SET balance=balance+? WHERE guild_id=? AND user_id=?", (amount, guild_id, member.id))
        cfg = get_config(guild_id)
        await interaction.response.send_message(f"✅ تم تحويل **{amount:,} {cfg['currency_symbol']}** إلى {member.mention}.")

    @app_commands.command(name="balance-top", description="ترتيب أغنى الأعضاء")
    async def top(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        with connect() as con:
            rows = con.execute("SELECT user_id,balance FROM balances WHERE guild_id=? ORDER BY balance DESC LIMIT 10", (interaction.guild.id,)).fetchall()
        cfg = get_config(interaction.guild.id)
        if not rows:
            return await interaction.response.send_message("❌ ما كاين حتى رصيد مسجل.", ephemeral=True)
        lines = []
        for index, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.mention if member else f"<@{row['user_id']}>"
            lines.append(f"**#{index}** {name} — **{row['balance']:,} {cfg['currency_symbol']}**")
        await interaction.response.send_message(embed=discord.Embed(title="🏆 أغنى الأعضاء", description="\n".join(lines), color=discord.Color.gold()))

    @app_commands.command(name="currency-settings", description="تغيير اسم ورمز العملة")
    async def currency_settings(self, interaction: discord.Interaction, name: str, symbol: str = "🪙"):
        if not await self.require_control(interaction):
            return
        assert interaction.guild is not None
        set_config(interaction.guild.id, currency_name=name[:40], currency_symbol=symbol[:10])
        await interaction.response.send_message(f"✅ العملة أصبحت: **{name} {symbol}**", ephemeral=True)

    @app_commands.command(name="currency-add", description="إضافة عملة لعضو")
    async def add(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        if not await self.require_control(interaction):
            return
        assert interaction.guild is not None
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute("UPDATE balances SET balance=balance+? WHERE guild_id=? AND user_id=?", (amount, interaction.guild.id, member.id))
        await interaction.response.send_message(f"✅ تمت إضافة **{amount:,}** إلى {member.mention}.", ephemeral=True)

    @app_commands.command(name="currency-remove", description="حذف عملة من رصيد عضو")
    async def remove(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        if not await self.require_control(interaction):
            return
        assert interaction.guild is not None
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute("UPDATE balances SET balance=MAX(0,balance-?) WHERE guild_id=? AND user_id=?", (amount, interaction.guild.id, member.id))
        await interaction.response.send_message(f"✅ تمت إزالة **{amount:,}** من {member.mention}.", ephemeral=True)

    @app_commands.command(name="currency-set", description="تحديد رصيد عضو مباشرة")
    async def set_balance(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 0, 1_000_000_000]):
        if not await self.require_control(interaction):
            return
        assert interaction.guild is not None
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute("UPDATE balances SET balance=? WHERE guild_id=? AND user_id=?", (amount, interaction.guild.id, member.id))
        await interaction.response.send_message(f"✅ رصيد {member.mention} أصبح **{amount:,}**.", ephemeral=True)

    @app_commands.command(name="currency-reset", description="تصفير رصيد عضو")
    async def reset_balance(self, interaction: discord.Interaction, member: discord.Member):
        if not await self.require_control(interaction):
            return
        assert interaction.guild is not None
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute("UPDATE balances SET balance=0 WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id))
        await interaction.response.send_message(f"✅ تم تصفير رصيد {member.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
