import discord
from discord import app_commands
from discord.ext import commands
from database import connect, set_config

class Messages(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="send", description="إرسال رسالة من البوت إلى روم محدد")
    @app_commands.describe(channel="الروم", message="الرسالة")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def send(self, interaction, channel: discord.TextChannel, message: str):
        await channel.send(message)
        with connect() as con:
            con.execute("INSERT INTO sent_messages (guild_id, channel_id, author_id, content) VALUES (?,?,?,?)", (interaction.guild.id, channel.id, interaction.user.id, message))
        await interaction.response.send_message(f"✅ تم الإرسال في {channel.mention}", ephemeral=True)

    @app_commands.command(name="set-announcement-channel", description="تحديد روم الإعلانات")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_announcement_channel(self, interaction, channel: discord.TextChannel):
        set_config(interaction.guild.id, announcement_channel=channel.id)
        await interaction.response.send_message(f"✅ روم الإعلانات: {channel.mention}", ephemeral=True)

async def setup(bot): await bot.add_cog(Messages(bot))
