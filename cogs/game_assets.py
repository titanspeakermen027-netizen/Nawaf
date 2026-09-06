from __future__ import annotations

import io
from pathlib import Path

import discord
from discord.ext import commands

# All game artwork is stored inside the repository so it does not depend on
# temporary Discord CDN URLs.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

ROULETTE_ART_FILENAME = "a3a22b8922412e080f008b2177c2ba80c5ba947a7c003716e06b184059052e15.png"
MAFIA_ART_FILENAME = "1788362198735.png"
POINT_REFERENCE_FILENAME = "Screenshot_--3.jpg"
POINT_ART_FILENAMES = (
    "f7530086801337860a15d5ff23448183c412f77f90191511777cc6b3b611b428.png",
    "f7530086801337860a15d5ff23448183c412f77f90191511777cc6b3b611b428_1.png",
)


def asset_path(filename: str) -> Path:
    return ASSETS_DIR / filename


def find_asset(filename: str) -> Path | None:
    path = asset_path(filename)
    return path if path.is_file() else None


def get_mafia_art_path() -> Path | None:
    return find_asset(MAFIA_ART_FILENAME)


def get_point_art_paths() -> list[Path]:
    return [path for name in POINT_ART_FILENAMES if (path := find_asset(name)) is not None]


class GameAssets(commands.Cog):
    """Shared local artwork for Nawaf games."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is None:
            return

        # Keep the original generated wheel as a safe fallback until the
        # roulette artwork is uploaded to assets/ with the expected filename.
        if not hasattr(roulette, "_original_wheel_file"):
            roulette._original_wheel_file = roulette.wheel_file

        async def roulette_wheel_file(session, selected_id: int, final: bool = False):
            local_path = find_asset(ROULETTE_ART_FILENAME)
            if local_path is None:
                # Also accept a future upload whose filename starts with the
                # supplied roulette artwork identifier.
                matches = sorted(ASSETS_DIR.glob("a3a22b8922412e080f008b2177c2ba80c5ba947a7c003716e06b184059052e15*.png"))
                local_path = matches[0] if matches else None

            if local_path is not None:
                try:
                    return discord.File(local_path, filename="roulette.png")
                except (OSError, ValueError):
                    pass

            return await roulette._original_wheel_file(session, selected_id, final)

        roulette.wheel_file = roulette_wheel_file


async def setup(bot: commands.Bot):
    await bot.add_cog(GameAssets(bot))
