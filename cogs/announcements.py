import discord
from discord import app_commands
from discord.ext import commands
from database import get_config, set_config

class Announcements(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @app_commands.command(name='announce',description='إرسال إعلان احترافي إلى روم محدد')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def announce(self,interaction,channel:discord.TextChannel,title:str,message:str):
        e=discord.Embed(title=f'📢 {title}',description=message,timestamp=discord.utils.utcnow())
        e.set_footer(text=f'بواسطة {interaction.user.display_name}')
        await channel.send(embed=e)
        set_config(interaction.guild.id,ad_channel=channel.id)
        await interaction.response.send_message(f'✅ تم نشر الإعلان في {channel.mention}.',ephemeral=True)

async def setup(bot): await bot.add_cog(Announcements(bot))
