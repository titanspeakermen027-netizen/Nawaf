# Nawaf Roblox Monitor — Cloudflare Worker

هاد المجلد هو Worker مستقل داخل نفس الريبو. الدور ديالو هو يعطي Roblox Studio رابط HTTPS ثابت على `workers.dev`، ومن بعد يمرر الطلبات بشكل آمن إلى API الحقيقية ديال Roblox Monitor اللي كتخدم في FeatherPanel/Quaxly.

## البنية

```text
cloudflare/
├── src/
│   └── index.js
├── wrangler.jsonc
├── package.json
└── README.md
```

ما بقيناش محتاجين Pages Functions أو مجلد `functions/`.

## Routes

- `GET /` → حالة الـWorker والـendpoints
- `GET /health` → فحص حالة الـupstream
- `POST /v1/errors` → تمرير أخطاء Roblox Studio إلى الـupstream
- `OPTIONS` → CORS preflight

## Cloudflare Variables / Secrets

من Cloudflare Dashboard افتح الـWorker:

`Workers & Pages → [Worker] → Settings → Variables and Secrets`

أضف:

- `ROBLOX_UPSTREAM_URL`: الرابط العمومي للـAPI اللي خدامة في FeatherPanel/Quaxly.
  مثال: `https://your-api.example.com:25543`
- `ROBLOX_MONITOR_API_KEY`: نفس الـsecret الطويل اللي حاطو في Nawaf. خليه Secret وما تحطوش في GitHub.

مهم: `ROBLOX_UPSTREAM_URL` خاصو يكون قابل للوصول من الإنترنت. `localhost` و`127.0.0.1` وعناوين الشبكة المحلية ما غاديش يخدمو.

الـWorker مفعّل فيه compatibility flag ديال `allow_custom_ports` باش يقدر يستعمل port مخصص في `fetch()` عند الحاجة. إلا كان الـupstream وراء Cloudflare نفسها، فالـport خاصو يكون من الـports اللي كتدعمها Cloudflare.

## GitHub → Workers Builds

1. من Cloudflare Dashboard دخل إلى:
   `Workers & Pages → Create application → Get started → Import a repository`
2. ربط GitHub واختار:
   `titanspeakermen027-netizen/Nawaf`
3. اختار branch:
   `main`
4. Root directory:
   `cloudflare`
5. خليه يكتاشف `wrangler.jsonc` الموجود داخل نفس الـroot.
6. Build command: خليه فارغ.
7. Deploy command: `npx wrangler deploy`
8. دير Save and Deploy.

Workers Builds غادي ينفذ Wrangler في بيئة Cloudflare، وبالتالي ما محتاجش تثبت أو تشغل Wrangler في Termux.

مهم: اسم الـWorker في Cloudflare Dashboard خاصو يطابق `name` داخل `wrangler.jsonc`، يعني:

```text
nawaf-roblox
```

من بعد الـdeploy Cloudflare غادي يعطيك رابط `workers.dev` بحال:

```text
https://nawaf-roblox.YOUR-SUBDOMAIN.workers.dev
```

وكل push جديد للbranch المرتبط يقدر يطلق deployment أوتوماتيكي.

## Nawaf .env

في سيرفر FeatherPanel/Quaxly:

```env
ROBLOX_MONITOR_HOST=0.0.0.0
ROBLOX_MONITOR_PORT=PORT_FROM_FEATHERPANEL
ROBLOX_MONITOR_PUBLIC_URL=https://nawaf-roblox.YOUR-SUBDOMAIN.workers.dev
ROBLOX_MONITOR_API_KEY=YOUR_SHARED_SECRET
```

`ROBLOX_MONITOR_PUBLIC_URL` غير هو الرابط العمومي اللي غيبان في `/roblox-status`.

## Roblox Studio Plugin

داخل `roblox_monitor/studio_plugin.lua` بدّل:

```lua
local API_URL = "https://nawaf-roblox.YOUR-SUBDOMAIN.workers.dev/v1/errors"
local API_KEY = "YOUR_SHARED_SECRET"
local MAP_NAME = "YOUR MAP NAME"
```

خاص `API_KEY` يكون نفس القيمة اللي موجودة في Nawaf وCloudflare.

## اختبار

بعد النشر:

1. افتح:
   `https://nawaf-roblox.YOUR-SUBDOMAIN.workers.dev/`
2. افتح:
   `https://nawaf-roblox.YOUR-SUBDOMAIN.workers.dev/health`
3. خاص `/health` يوصل للـAPI الأصلية ويعطيك حالتها.
4. من Roblox Studio جرب Error حقيقي في Studio Output.
5. راقب روم Discord المحدد في `ROBLOX_MONITOR_CHANNEL_ID`.

إلا `/health` عطاك `upstream_unreachable`، فالمشكل غالباً في الوصول إلى API ديال FeatherPanel/Quaxly أو في الـport، ماشي في GitHub deployment.
