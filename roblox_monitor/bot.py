from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

LOGGER = logging.getLogger("roblox_monitor")

TOKEN = os.getenv("ROBLOX_MONITOR_TOKEN", "").strip()
API_KEY = os.getenv("ROBLOX_MONITOR_API_KEY", "").strip()
CHANNEL_ID_RAW = os.getenv("ROBLOX_MONITOR_CHANNEL_ID", "").strip()
HOST = os.getenv("ROBLOX_MONITOR_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT_RAW = os.getenv("ROBLOX_MONITOR_PORT", "8080").strip()
MAP_NAME = os.getenv("ROBLOX_MONITOR_MAP_NAME", "Roblox Map").strip() or "Roblox Map"
PUBLIC_URL = os.getenv("ROBLOX_MONITOR_PUBLIC_URL", "").strip()
DEDUPE_SECONDS_RAW = os.getenv("ROBLOX_MONITOR_DEDUPE_SECONDS", "60").strip()
RATE_LIMIT_WINDOW_RAW = os.getenv("ROBLOX_MONITOR_RATE_WINDOW", "60").strip()
RATE_LIMIT_MAX_RAW = os.getenv("ROBLOX_MONITOR_RATE_MAX", "20").strip()

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    CHANNEL_ID = 0

try:
    PORT = int(PORT_RAW)
except ValueError:
    PORT = 8080

try:
    DEDUPE_SECONDS = max(0, int(DEDUPE_SECONDS_RAW))
except ValueError:
    DEDUPE_SECONDS = 60

try:
    RATE_LIMIT_WINDOW = max(1, int(RATE_LIMIT_WINDOW_RAW))
except ValueError:
    RATE_LIMIT_WINDOW = 60

try:
    RATE_LIMIT_MAX = max(1, int(RATE_LIMIT_MAX_RAW))
except ValueError:
    RATE_LIMIT_MAX = 20

MAX_MESSAGE_LENGTH = 3500
MAX_FIELD_LENGTH = 1000
MAX_BODY_BYTES = 32 * 1024

if not TOKEN:
    raise RuntimeError("ROBLOX_MONITOR_TOKEN is missing from .env")

if not API_KEY or API_KEY in {"CHANGE_ME", "PUT_YOUR_API_KEY_HERE"}:
    raise RuntimeError("ROBLOX_MONITOR_API_KEY is missing or still uses a placeholder")

if not CHANNEL_ID:
    raise RuntimeError("ROBLOX_MONITOR_CHANNEL_ID is missing or invalid")

if not (1 <= PORT <= 65535):
    raise RuntimeError("ROBLOX_MONITOR_PORT must be between 1 and 65535")


intents = discord.Intents.none()
bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    help_command=None,
    allowed_mentions=discord.AllowedMentions.none(),
)

seen_errors: dict[str, tuple[float, int]] = {}
request_history: dict[str, deque[float]] = defaultdict(deque)
state_lock = asyncio.Lock()


def clean_text(value: Any, fallback: str = "Unknown") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def truncate(value: str, limit: int) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def error_fingerprint(payload: dict[str, Any]) -> str:
    raw = "\n".join(
        [
            clean_text(payload.get("map_name"), MAP_NAME).lower(),
            clean_text(payload.get("message")).lower(),
            clean_text(payload.get("source"), "studio").lower(),
            clean_text(payload.get("message_type"), "MessageError").lower(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def parse_script_location(message: str) -> tuple[str | None, str | None]:
    patterns = (
        r"^([^\n:]+):([0-9]+):",
        r"^([^\n]+):([0-9]+):",
        r"^([^\n]+)\((?:line )?([0-9]+)\)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return truncate(match.group(1).strip(), 200), match.group(2)
    return None, None


def get_client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.remote or "unknown"


async def rate_allowed(request: web.Request) -> bool:
    now = time.monotonic()
    client_ip = get_client_ip(request)

    async with state_lock:
        bucket = request_history[client_ip]
        cutoff = now - RATE_LIMIT_WINDOW
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT_MAX:
            return False

        bucket.append(now)
        return True


async def authorize(request: web.Request) -> bool:
    supplied = request.headers.get("X-API-Key", "")
    return bool(supplied) and supplied == API_KEY


async def resolve_channel() -> discord.abc.Messageable:
    channel = bot.get_channel(CHANNEL_ID)
    if channel is not None:
        return channel

    channel = await bot.fetch_channel(CHANNEL_ID)
    if not isinstance(channel, discord.abc.Messageable):
        raise RuntimeError("Configured Roblox monitor channel is not messageable")
    return channel


async def send_error_embed(payload: dict[str, Any]) -> tuple[bool, bool]:
    message = truncate(clean_text(payload.get("message"), "Unknown Roblox error"), MAX_MESSAGE_LENGTH)
    map_name = truncate(clean_text(payload.get("map_name"), MAP_NAME), 200)
    message_type = truncate(clean_text(payload.get("message_type"), "MessageError"), 100)
    source = truncate(clean_text(payload.get("source"), "Roblox Studio"), 100)
    context = truncate(clean_text(payload.get("context"), "Studio"), MAX_FIELD_LENGTH)
    place_id = truncate(clean_text(payload.get("place_id"), "N/A"), 100)
    game_id = truncate(clean_text(payload.get("game_id"), "N/A"), 100)
    client_time = truncate(clean_text(payload.get("timestamp"), "N/A"), 100)

    script_name, script_line = parse_script_location(message)

    fingerprint = error_fingerprint(payload)
    now = time.monotonic()
    suppressed_count = 0

    async with state_lock:
        previous = seen_errors.get(fingerprint)
        if previous and DEDUPE_SECONDS > 0 and now - previous[0] < DEDUPE_SECONDS:
            seen_errors[fingerprint] = (previous[0], previous[1] + 1)
            return False, True

        if previous:
            suppressed_count = previous[1]
        seen_errors[fingerprint] = (now, 1)

        stale_before = now - max(DEDUPE_SECONDS * 2, 600)
        stale_keys = [key for key, (stamp, _) in seen_errors.items() if stamp < stale_before]
        for key in stale_keys:
            seen_errors.pop(key, None)

    embed = discord.Embed(
        title="Roblox Map Error Detected",
        description=f"~~~text\n{message}\n~~~",
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Map", value=map_name, inline=True)
    embed.add_field(name="Type", value=message_type, inline=True)
    embed.add_field(name="Source", value=source, inline=True)

    if script_name:
        location = script_name
        if script_line:
            location += f" — line {script_line}"
        embed.add_field(name="Script / Location", value=truncate(location, MAX_FIELD_LENGTH), inline=False)

    embed.add_field(name="Context", value=context, inline=True)
    embed.add_field(name="Place ID", value=place_id, inline=True)
    embed.add_field(name="Game ID", value=game_id, inline=True)

    if client_time != "N/A":
        embed.add_field(name="Studio Timestamp", value=client_time, inline=True)

    if suppressed_count:
        embed.add_field(
            name="Repeated Errors Suppressed",
            value=f"{suppressed_count} duplicate event(s) were grouped during the cooldown.",
            inline=False,
        )

    embed.set_footer(text="Nawaf • Roblox Monitor")

    channel = await resolve_channel()
    await channel.send(embed=embed)
    return True, False


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "nawaf-roblox-monitor",
            "discord_ready": bot.is_ready(),
            "channel_id": CHANNEL_ID,
        }
    )


async def receive_error(request: web.Request) -> web.Response:
    if not await authorize(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    if not await rate_allowed(request):
        return web.json_response({"ok": False, "error": "rate_limited"}, status=429)

    if not bot.is_ready():
        return web.json_response({"ok": False, "error": "discord_not_ready"}, status=503)

    if request.content_length and request.content_length > MAX_BODY_BYTES:
        return web.json_response({"ok": False, "error": "request_too_large"}, status=413)

    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)

    message = clean_text(payload.get("message"), "")
    if not message:
        return web.json_response({"ok": False, "error": "message_required"}, status=400)

    try:
        sent, suppressed = await send_error_embed(payload)
    except discord.Forbidden:
        LOGGER.exception("Discord rejected the monitor message; check channel permissions")
        return web.json_response({"ok": False, "error": "discord_forbidden"}, status=403)
    except discord.HTTPException:
        LOGGER.exception("Discord API error while sending Roblox monitor message")
        return web.json_response({"ok": False, "error": "discord_http_error"}, status=502)
    except Exception:
        LOGGER.exception("Unexpected Roblox monitor error")
        return web.json_response({"ok": False, "error": "internal_error"}, status=500)

    return web.json_response({"ok": True, "sent": sent, "suppressed": suppressed}, status=202)


app = web.Application(client_max_size=MAX_BODY_BYTES)
app.router.add_get("/health", health)
app.router.add_get("/", health)
app.router.add_post("/v1/errors", receive_error)


@bot.event
async def on_ready() -> None:
    if getattr(bot, "_monitor_synced", False):
        return

    try:
        synced = await bot.tree.sync()
        bot._monitor_synced = True
        LOGGER.info("Synced %d Roblox Monitor slash command(s)", len(synced))
    except discord.HTTPException:
        LOGGER.exception("Failed to sync Roblox Monitor slash commands")

    LOGGER.info("Roblox Monitor logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    LOGGER.info("HTTP API listening on %s:%s", HOST, PORT)
    if PUBLIC_URL:
        LOGGER.info("Public monitor URL: %s", PUBLIC_URL)


@bot.tree.command(name="roblox-status", description="التحقق من حالة Roblox Monitor وواجهة API")
async def roblox_status(interaction: discord.Interaction) -> None:
    public_api = f"{PUBLIC_URL.rstrip('/')}/health" if PUBLIC_URL else "غير محدد"
    status = "ONLINE" if bot.is_ready() else "OFFLINE"
    await interaction.response.send_message(
        f"Roblox Monitor\nDiscord: {status}\nChannel: {CHANNEL_ID}\nAPI: {public_api}",
        ephemeral=True,
    )


@bot.tree.command(name="roblox-test", description="إرسال رسالة اختبار إلى روم مراقبة Roblox")
async def roblox_test(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)

    try:
        channel = await resolve_channel()
        embed = discord.Embed(
            title="Roblox Monitor Test",
            description="The Roblox error monitor can send messages to this channel.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Map", value=MAP_NAME, inline=True)
        embed.add_field(name="Source", value="Discord /roblox-test", inline=True)
        embed.set_footer(text="Nawaf • Roblox Monitor")
        await channel.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "فشل الاختبار: البوت لا يملك صلاحية إرسال الرسائل/Embeds في الروم المحدد.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(f"فشل الاختبار بسبب Discord API: {exc}", ephemeral=True)
        return

    await interaction.followup.send("تم إرسال رسالة الاختبار بنجاح.", ephemeral=True)


async def run() -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()

    try:
        async with bot:
            await bot.start(TOKEN)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(run())
