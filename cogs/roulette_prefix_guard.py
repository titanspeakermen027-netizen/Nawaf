from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class RoulettePrefixGuard(commands.Cog):
    """Disable the legacy roulette entry point and keep -روليت as the only starter."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        games = self.bot.get_cog("Games")
        if games:
            original = games._prefix_start

            async def guarded_prefix_start(message: discord.Message, game_type: str, args: list[str]):
                if game_type == "roulette":
                    return await message.reply(
                        "❌ اختصار الروليت هو **`-روليت`** فقط.",
                        mention_author=False,
                    )
                return await original(message, game_type, args)

            games._prefix_start = guarded_prefix_start

        command = self.bot.tree.get_command("game-start")
        if isinstance(command, app_commands.Command):
            command.choices = [
                app_commands.Choice(name="معركة النرد", value="dice_battle")
            ]


async def setup(bot: commands.Bot):
    await bot.add_cog(RoulettePrefixGuard(bot))
