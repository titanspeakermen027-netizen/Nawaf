import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_config, set_config

DHIKR = [
    'سُبْحَانَ اللَّهِ وَبِحَمْدِهِ',
    'سُبْحَانَ اللَّهِ الْعَظِيمِ',
    'لَا إِلَٰهَ إِلَّا اللَّهُ',
    'أَسْتَغْفِرُ اللَّهَ وَأَتُوبُ إِلَيْهِ',
    'اللَّهُمَّ صَلِّ وَسَلِّمْ عَلَى نَبِيِّنَا مُحَمَّدٍ ﷺ'
]

class Dhikr(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.index=0; self.task.start()
    def cog_unload(self): self.task.cancel()
    @tasks.loop(minutes=1)
    async def task(self):
        for guild in self.bot.guilds:
            cfg=get_config(guild.id)
            if not cfg['dhikr_enabled'] or not cfg['dhikr_channel']: continue
            # interval is in minutes for predictable scheduling
            if self.task.current_loop % max(1, int(cfg['dhikr_interval'] or 60)) != 0: continue
            ch=guild.get_channel(cfg['dhikr_channel'])
            if ch:
                await ch.send(f'🕌 **ذكر اليوم**\n{DHIKR[self.index % len(DHIKR)]}')
                self.index += 1
    @task.before_loop
    async def before(self): await self.bot.wait_until_ready()

    @app_commands.command(name='dhikr-settings',description='تفعيل الأذكار وتحديد الروم والفاصل بالدقائق')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(self,interaction,channel:discord.TextChannel,minutes:int=60,enabled:bool=True):
        if minutes<1: return await interaction.response.send_message('❌ المدة يجب أن تكون دقيقة أو أكثر.',ephemeral=True)
        set_config(interaction.guild.id,dhikr_channel=channel.id,dhikr_interval=minutes,dhikr_enabled=int(enabled))
        await interaction.response.send_message(f'✅ الأذكار: {"مفعلة" if enabled else "متوقفة"} — كل {minutes} دقيقة في {channel.mention}',ephemeral=True)

async def setup(bot): await bot.add_cog(Dhikr(bot))
