import discord
from discord import app_commands
from discord.ext import commands
from database import connect, get_config, set_config


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def ensure(guild_id, user_id):
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO balances(guild_id,user_id,balance) VALUES(?,?,0)",
                (guild_id, user_id),
            )

    @app_commands.command(name="balance", description="عرض تفاصيل رصيدك أو رصيد عضو")
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
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
        embed.add_field(
            name="الرصيد",
            value=f"**{row['balance']:,}** {cfg['currency_name']} {cfg['currency_symbol']}",
            inline=False,
        )
        embed.set_footer(text="يمكن استعمال العملة للتحويل والشراء من المتجر.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description="تحويل العملة لعضو")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message("❌ لا يمكن التحويل لهذا العضو.", ephemeral=True)
        guild_id = interaction.guild.id
        self.ensure(guild_id, interaction.user.id)
        self.ensure(guild_id, member.id)
        with connect() as con:
            sender = con.execute(
                "SELECT balance FROM balances WHERE guild_id=? AND user_id=?",
                (guild_id, interaction.user.id),
            ).fetchone()
            if sender["balance"] < amount:
                return await interaction.response.send_message("❌ رصيدك غير كافٍ.", ephemeral=True)
            con.execute(
                "UPDATE balances SET balance=balance-? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, interaction.user.id),
            )
            con.execute(
                "UPDATE balances SET balance=balance+? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, member.id),
            )
        cfg = get_config(guild_id)
        await interaction.response.send_message(
            f"✅ تم تحويل **{amount:,} {cfg['currency_symbol']}** إلى {member.mention}."
        )

    @app_commands.command(name="balance-top", description="ترتيب أغنى الأعضاء")
    async def top(self, interaction: discord.Interaction):
        with connect() as con:
            rows = con.execute(
                "SELECT user_id,balance FROM balances WHERE guild_id=? ORDER BY balance DESC LIMIT 10",
                (interaction.guild.id,),
            ).fetchall()
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
    @app_commands.checks.has_permissions(manage_guild=True)
    async def currency_settings(self, interaction: discord.Interaction, name: str, symbol: str = "🪙"):
        set_config(interaction.guild.id, currency_name=name[:40], currency_symbol=symbol[:10])
        await interaction.response.send_message(f"✅ العملة أصبحت: **{name} {symbol}**", ephemeral=True)

    @app_commands.command(name="currency-add", description="إضافة عملة لعضو")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute(
                "UPDATE balances SET balance=balance+? WHERE guild_id=? AND user_id=?",
                (amount, interaction.guild.id, member.id),
            )
        await interaction.response.send_message(f"✅ تمت إضافة **{amount:,}** لــ{member.mention}.", ephemeral=True)

    @app_commands.command(name="currency-remove", description="حذف عملة من رصيد عضو")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute(
                "UPDATE balances SET balance=MAX(0,balance-?) WHERE guild_id=? AND user_id=?",
                (amount, interaction.guild.id, member.id),
            )
        await interaction.response.send_message(f"✅ تمت إزالة **{amount:,}** من {member.mention}.", ephemeral=True)

    @app_commands.command(name="currency-set", description="تحديد رصيد عضو مباشرة")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_balance(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 0, 1_000_000_000]):
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute(
                "UPDATE balances SET balance=? WHERE guild_id=? AND user_id=?",
                (amount, interaction.guild.id, member.id),
            )
        await interaction.response.send_message(f"✅ رصيد {member.mention} أصبح **{amount:,}**.", ephemeral=True)

    @app_commands.command(name="currency-reset", description="تصفير رصيد عضو")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_balance(self, interaction: discord.Interaction, member: discord.Member):
        self.ensure(interaction.guild.id, member.id)
        with connect() as con:
            con.execute(
                "UPDATE balances SET balance=0 WHERE guild_id=? AND user_id=?",
                (interaction.guild.id, member.id),
            )
        await interaction.response.send_message(f"✅ تم تصفير رصيد {member.mention}.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Economy(bot))
