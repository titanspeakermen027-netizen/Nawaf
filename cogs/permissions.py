import discord
from discord.ext import commands

class PermissionHandler(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.Cog.listener()
    async def on_app_command_error(self,interaction,error):
        if isinstance(error, discord.app_commands.errors.MissingPermissions):
            message='❌ ما عندكش الصلاحية الكافية لاستعمال هاد الأمر.'
            if interaction.response.is_done(): await interaction.followup.send(message,ephemeral=True)
            else: await interaction.response.send_message(message,ephemeral=True)

async def setup(bot): await bot.add_cog(PermissionHandler(bot))
