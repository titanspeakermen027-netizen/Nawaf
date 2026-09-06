import discord
from discord import app_commands
from discord.ext import commands
from database import connect, get_config


class Config(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="config", description="عرض إعدادات البوت في السيرفر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction):
        c = get_config(interaction.guild.id)
        with connect() as con:
            rewards = con.execute(
                "SELECT COUNT(*) AS count FROM level_rewards WHERE guild_id=? AND enabled=1",
                (interaction.guild.id,),
            ).fetchone()["count"]
            products = con.execute(
                "SELECT COUNT(*) AS count FROM shop_products WHERE guild_id=? AND active=1",
                (interaction.guild.id,),
            ).fetchone()["count"]

        e = discord.Embed(title="⚙️ إعدادات Nawaf", color=discord.Color.blurple())
        e.add_field(name="Tickets Category", value=f"<#{c['ticket_category']}>" if c["ticket_category"] else "غير محدد", inline=False)
        e.add_field(name="Ticket Rating", value=f"1–{c['ticket_rating_max'] or 10}", inline=True)
        e.add_field(name="Ticket Log", value=f"<#{c['ticket_log_channel']}>" if c["ticket_log_channel"] else "غير محدد", inline=True)
        e.add_field(name="Applications", value=f"<#{c['application_review_channel']}>" if c["application_review_channel"] else "غير محدد", inline=False)
        e.add_field(name="Announcements", value=f"<#{c['ad_channel']}>" if c["ad_channel"] else "غير محدد", inline=False)
        e.add_field(name="Dhikr", value=f"<#{c['dhikr_channel']}>" if c["dhikr_channel"] else "غير مفعل", inline=False)
        e.add_field(name="Shop Orders", value=f"<#{c['shop_order_channel']}>" if c["shop_order_channel"] else "غير محدد", inline=False)
        e.add_field(name="Shop Products", value=str(products), inline=True)
        e.add_field(name="Level Roles", value=str(rewards), inline=True)
        e.add_field(name="Jail Role", value=f"<@&{c['jail_role_id']}>" if c["jail_role_id"] else "غير محددة", inline=True)
        e.add_field(name="Jail Channel", value=f"<#{c['jail_channel_id']}>" if c["jail_channel_id"] else "غير محدد", inline=True)
        e.add_field(name="Currency", value=f"{c['currency_name']} {c['currency_symbol']}", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Config(bot))
