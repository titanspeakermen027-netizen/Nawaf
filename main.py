import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database import init_db

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=("!", "C"), intents=intents, case_insensitive=True)

COGS = (
    "cogs.permissions",
    "cogs.messages",
    "cogs.tickets",
    "cogs.leveling",
    "cogs.applications",
    "cogs.announcements",
    "cogs.dhikr",
    "cogs.economy",
    "cogs.shop",
    "cogs.config",
    "cogs.moderation",
    "cogs.prefix_systems",
    "cogs.games",
    "cogs.game_channels",
    "cogs.dice_upgrade",
    "cogs.game_restrictions",
    "cogs.roulette_multi_message",
    "cogs.premium",
    "cogs.game_assets",
    "cogs.points",
    "cogs.mini_games",
    "cogs.roulette_prefix_guard",
    "cogs.help",
    "cogs.admin_prefix",
    "cogs.health",
)


async def load_cogs():
    for extension in COGS:
        try:
            await bot.load_extension(extension)
            print(f"[OK] Loaded {extension}")
        except Exception as exc:
            print(f"[ERROR] Failed to load {extension}: {exc!r}")
            raise


@bot.event
async def on_ready():
    if getattr(bot, "_commands_synced", False):
        return

    try:
        synced = await bot.tree.sync()
        bot._commands_synced = True
        print(f"[OK] Synced {len(synced)} global slash commands")

        applications = bot.get_cog("Applications")
        if applications:
            await applications.register_persistent_panels()
            print("[OK] Registered persistent application panels")
    except discord.HTTPException as exc:
        print(f"[ERROR] Slash command sync failed: {exc!r}")
        raise

    print(f"Nawaf logged in as {bot.user} ({bot.user.id})")
    print(f"Connected to {len(bot.guilds)} guild(s)")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)


@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 {round(bot.latency * 1000)}ms", ephemeral=True)


@bot.tree.command(name="test", description="Test Nawaf slash commands")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Slash Commands ديال Nawaf خدامين مزيان!", ephemeral=True)


async def main():
    init_db()
    await load_cogs()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing from .env")

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
