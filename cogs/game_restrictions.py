from __future__ import annotations

from discord.ext import commands

from cogs.game_channels import is_group_game_channel_allowed


class GameRestrictions(commands.Cog):
    """Overlay channel restrictions without replacing the existing game systems."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patched = False
        self.original_games_start = None
        self.original_roulette_start = None

    def _allowed(self, message) -> bool:
        if not message.guild:
            return False
        return is_group_game_channel_allowed(message.guild.id, message.channel.id)

    async def cog_load(self):
        self.patch_games()
        self.patch_roulette()

    def patch_games(self) -> None:
        games = self.bot.get_cog("Games")
        if not games or self._patched:
            return

        original = games.start_session
        self.original_games_start = original

        def guarded_start_session(guild, channel_id, starter_id, game_type, reward=5, max_players=15):
            if not is_group_game_channel_allowed(guild.id, channel_id):
                return None, "❌ هاد الروم ما مسموحش فيه الألعاب الجماعية. استعمل واحد من الرومات المحددة من الإدارة."
            return original(guild, channel_id, starter_id, game_type, reward, max_players)

        games.start_session = guarded_start_session
        self._patched = True

    def patch_roulette(self) -> None:
        roulette = self.bot.get_cog("RouletteUpgrade")
        if not roulette:
            return

        original = roulette.start
        self.original_roulette_start = original

        async def guarded_start(message, args):
            if not self._allowed(message):
                return await message.reply(
                    "❌ هاد الروم ما مسموحش فيه الألعاب الجماعية. استعمل واحد من الرومات المحددة من الإدارة.",
                    mention_author=False,
                )
            return await original(message, args)

        roulette.start = guarded_start


async def setup(bot: commands.Bot):
    await bot.add_cog(GameRestrictions(bot))
