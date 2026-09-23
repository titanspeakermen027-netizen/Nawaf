## Start

Create .env from config.example.env and put the main Nawaf token in DISCORD_TOKEN.

For the separate Roblox Monitor bot, also fill:
- ROBLOX_MONITOR_TOKEN
- ROBLOX_MONITOR_API_KEY
- ROBLOX_MONITOR_CHANNEL_ID
- ROBLOX_MONITOR_MAP_NAME
- ROBLOX_MONITOR_PUBLIC_URL

Install requirements:

~~~bash
pip install -r requirements.txt
~~~

Run the normal Nawaf bot with:

~~~bash
python main.py
~~~

Run only the standalone Roblox monitor with:

~~~bash
python roblox_monitor/bot.py
~~~

The database file nawaf.sqlite3 is created automatically and is ignored by git.
