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

### Startup command

الاستضافة تحتاج غير Startup واحد:

~~~bash
python main.py
~~~

هذا الملف كيشغّل البوتين بجوج في نفس العملية:
- Nawaf Discord Bot باستخدام DISCORD_TOKEN
- Roblox Monitor باستخدام ROBLOX_MONITOR_TOKEN

The database file nawaf.sqlite3 is created automatically and is ignored by git.
