from discord import app_commands
from discord.ext import commands

class Health(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @app_commands.command(name='health',description='حالة البوت والأنظمة')
    async def health(self,interaction):
        await interaction.response.send_message(f'✅ Nawaf يعمل — Ping: {round(self.bot.latency*1000)}ms',ephemeral=True)

async def setup(bot): await bot.add_cog(Health(bot))
