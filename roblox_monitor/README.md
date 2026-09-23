# Nawaf — Roblox Map Error Monitor

This is a standalone service inside the Nawaf repository. It does not use the main Nawaf bot token and does not load Nawaf existing cogs.

## What it does

Roblox Studio sends MessageError events to the monitor API. The standalone Discord bot receives them and posts an Embed to one configured Discord channel.

The monitor also includes:

- API-key authentication.
- Basic IP rate limiting.
- Duplicate-error grouping to avoid Discord spam.
- /roblox-status to check the monitor configuration.
- /roblox-test to test the configured Discord channel.
- /health for a simple health check.
- HTTPS-ready deployment behind your normal reverse proxy/domain.

## Environment

Add these variables to the root .env:

~~~env
ROBLOX_MONITOR_TOKEN=YOUR_SEPARATE_DISCORD_BOT_TOKEN
ROBLOX_MONITOR_API_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_SECRET
ROBLOX_MONITOR_CHANNEL_ID=123456789012345678
ROBLOX_MONITOR_MAP_NAME=My Roblox Map

ROBLOX_MONITOR_HOST=0.0.0.0
ROBLOX_MONITOR_PORT=8080
ROBLOX_MONITOR_PUBLIC_URL=https://your-domain.example.com
ROBLOX_MONITOR_DEDUPE_SECONDS=60
ROBLOX_MONITOR_RATE_WINDOW=60
ROBLOX_MONITOR_RATE_MAX=20
~~~

Do not commit .env. It is already covered by the repository .gitignore.

## Discord permissions

Invite the separate bot to the target server with the minimum permissions it needs. It needs access to the configured channel and permission to send messages and embeds there.

## Start

Install the repository requirements, then start only this standalone service:

~~~bash
pip install -r requirements.txt
python roblox_monitor/bot.py
~~~

The normal Nawaf bot remains:

~~~bash
python main.py
~~~

They can therefore run independently with two different Discord bot tokens.

## Roblox Studio plugin

Use studio_plugin.lua as the source for a local Studio Plugin.

Change:

~~~lua
local API_URL = "https://YOUR-PUBLIC-DOMAIN.example.com/v1/errors"
local API_KEY = "PUT_YOUR_API_KEY_HERE"
local MAP_NAME_OVERRIDE = "" -- Optional
~~~

The API URL must point to the public HTTPS URL of the monitor /v1/errors endpoint.

### Automatic map detection

The plugin automatically sends the current Roblox place name from `game.Name`, together with `PlaceId` and `GameId`. The Discord monitor reads these values from the request payload and displays them in the error Embed.

That means you do not need to configure the map name manually for every place. To force a custom name, set:

~~~lua
local MAP_NAME_OVERRIDE = "My Map Name"
~~~

Leave it empty to keep automatic detection.

Roblox Studio plugin HTTP requests may require a permission prompt the first time the plugin communicates with the address.

The plugin listens to LogService.MessageOut and forwards only Enum.MessageType.MessageError, not normal output or warnings.

## Recommended deployment

Expose the Python service through an HTTPS reverse proxy/domain. Keep the Discord bot token and API key only in .env.

Do not put either secret inside a public GitHub repository or inside a publicly shared Studio Plugin.

## Example

A Roblox error such as:

~~~text
Workspace.Map.Doors.MainDoor:25: attempt to index nil with 'Position'
~~~

can arrive in Discord as a structured alert containing the map, error text, source, place/game IDs, and parsed script/line information when it can be extracted from the Roblox error message.
