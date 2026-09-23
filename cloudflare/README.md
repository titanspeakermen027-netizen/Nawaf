# Nawaf Roblox Monitor — Cloudflare Pages Proxy

هاد المجلد هو مشروع Cloudflare Pages مستقل داخل نفس الريبو. الدور ديالو هو يعطي Roblox Studio رابط HTTPS ثابت على pages.dev، ومن بعد يمرر الطلبات بشكل آمن إلى API الحقيقية ديال Roblox Monitor اللي كتخدم في FeatherPanel/Quaxly.

## Routes

- GET / → صفحة تعريفية بسيطة
- GET /health → فحص حالة الـupstream
- POST /v1/errors → تمرير أخطاء Roblox Studio إلى الـupstream

## Cloudflare Variables / Secrets

من Cloudflare Dashboard افتح:
Workers & Pages → مشروع Pages → Settings → Variables and Secrets

أضف:

- ROBLOX_UPSTREAM_URL: الرابط العمومي للـAPI اللي خدامة في FeatherPanel/Quaxly. مثال: https://your-api.example.com:25543
- ROBLOX_MONITOR_API_KEY: نفس الـsecret الطويل اللي حاطو في Nawaf. خليه Secret وما تحطوش في GitHub.

مهم: ROBLOX_UPSTREAM_URL خاصو يكون قابل للوصول من الإنترنت. localhost و127.0.0.1 وعناوين الشبكة المحلية ما غاديش يخدمو.

## Deployment

1. Workers & Pages → Create application → Pages → Import an existing Git repository.
2. اختار repository: titanspeakermen027-netizen/Nawaf
3. Production branch: main
4. Root directory: cloudflare
5. Framework preset: None
6. Build command: exit 0
7. Build output directory: .
8. دير Save and Deploy.
9. من بعد النشر، Cloudflare غيعطيك رابط بحال:
   https://YOUR-PROJECT.pages.dev

## Nawaf .env

في سيرفر FeatherPanel/Quaxly:

ROBLOX_MONITOR_HOST=0.0.0.0
ROBLOX_MONITOR_PORT=PORT_FROM_FEATHERPANEL
ROBLOX_MONITOR_PUBLIC_URL=https://YOUR-PROJECT.pages.dev
ROBLOX_MONITOR_API_KEY=YOUR_SHARED_SECRET

ROBLOX_MONITOR_PUBLIC_URL غير هو الرابط العمومي اللي غيبان في /roblox-status.

## Roblox Studio Plugin

داخل roblox_monitor/studio_plugin.lua بدّل القيم:

local API_URL = "https://YOUR-PROJECT.pages.dev/v1/errors"
local API_KEY = "YOUR_SHARED_SECRET"
local MAP_NAME = "YOUR MAP NAME"

خاص API_KEY يكون نفس القيمة اللي موجودة في Nawaf وCloudflare.

## اختبار

بعد النشر:
1. افتح https://YOUR-PROJECT.pages.dev/health
2. خاصك تشوف JSON من خدمة الـRoblox Monitor الأصلية.
3. من Roblox Studio جرب Error حقيقي في Studio Output.
4. راقب روم Discord المحدد في ROBLOX_MONITOR_CHANNEL_ID.

إلا /health عطاك upstream_unreachable، فالمشكل غالباً في الرابط أو الـport ديال FeatherPanel، ماشي في pages.dev.

Cloudflare Pages Functions كتخدم من مجلد functions، وهي اللي كتخلق routes بحال /health و/v1/errors.
