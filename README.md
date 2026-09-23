# Nawaf Discord Bot

بوت Discord مكتوب بـ Python و discord.py 2.x مع SQLite.

## المميزات

- 💬 /send إرسال رسالة من البوت إلى روم يحدده المشرف.
- 📩 /say-member إرسال رسالة خاصة إلى عضو.
- 🎫 Tickets مع منع صاحب التذكرة من إغلاقها، وإغلاق من الإدارة، ثم تقييم من 1 إلى 5 مع ملاحظة.
- 📈 XP وLevels تلقائياً مع /level.
- 📝 نظام تقديم عبر Modal مع قبول/رفض وإشعار للمتقدم.
- 📢 /announce إعلانات Embed إلى روم محدد.
- 🕌 أذكار تلقائية قابلة للتفعيل وتحديد الروم والفاصل الزمني.
- 🪙 عملة خاصة بكل سيرفر مع /balance و/pay و/currency-settings و/currency-add.
- 🛒 متجر بالعملة الخاصة بالسيرفر: إضافة وحذف المنتجات، عرض المتجر، الشراء، المخزون، وأرقام الطلبات.
- 🧾 تسجيل الطلبات في SQLite مع إمكانية تحديد روم لإشعارات الطلبات عبر /shop-settings.
- ⚙️ /config لعرض إعدادات الأنظمة الرئيسية.

## أوامر المتجر

- /shop عرض المنتجات المتاحة.
- /shop-buy product_id quantity شراء منتج.
- /shop-add name price description stock إضافة منتج — stock=-1 يعني مخزون غير محدود.
- /shop-remove product_id إخفاء منتج من المتجر.
- /shop-settings channel تحديد روم إشعارات الطلبات.

## Roblox Map Error Monitor

الريبو يحتوي أيضاً على خدمة مستقلة لمراقبة أخطاء خرائط Roblox Studio. الخدمة لها توكن Discord منفصل عن Nawaf، وتستقبل أخطاء Studio عبر API ثم ترسلها إلى روم Discord محدد.

### Startup واحد للاستضافة

ما تحتاجش تشغّل جوج ملفات. الملف الرئيسي `main.py` كيشغّل **Nawaf + Roblox Monitor معاً في نفس العملية**، مع توكنين مستقلين:

~~~bash
python main.py
~~~

تفاصيل إعداد API وStudio Plugin موجودة في:

~~~text
roblox_monitor/README.md
roblox_monitor/studio_plugin.lua
~~~

## التشغيل

1. ثبّت Python 3.11+.
2. نفّذ pip install -r requirements.txt.
3. أنشئ ملف .env وضع فيه:
   DISCORD_TOKEN=توكن_البوت
4. شغّل python main.py.

فعّل في Discord Developer Portal الـMessage Content Intent وServer Members Intent لأن بعض الأنظمة تحتاجها.

قاعدة البيانات تُنشأ تلقائياً عند تشغيل البوت، وتدعم ترقية قواعد البيانات القديمة بإضافة جداول المتجر الجديدة دون حذف البيانات السابقة.


## Cloudflare Pages public URL for Roblox Monitor

الريبو فيه مشروع Pages جاهز داخل:

~~~text
cloudflare/
~~~

هاد المشروع كيعطيك رابط HTTPS على ~~~text
*.pages.dev
~~~ وكيعمل Proxy آمن نحو API ديال Roblox Monitor اللي خدامة في FeatherPanel/Quaxly.

### إعداد Pages

- Cloudflare Dashboard → Workers & Pages → Create application → Pages → Import an existing Git repository.
- Repository: ~~~text
titanspeakermen027-netizen/Nawaf
~~~
- Production branch: ~~~text
main
~~~
- Root directory: ~~~text
cloudflare
~~~
- Framework preset: None
- Build command: ~~~text
exit 0
~~~
- Build output directory: ~~~text
.
~~~

من بعد النشر، استعمل رابط ~~~text
https://YOUR-PROJECT.pages.dev
~~~ كـROBLOX_MONITOR_PUBLIC_URL.

### Variables / Secrets في Cloudflare

في Pages → Settings → Variables and Secrets أضف:

~~~text
ROBLOX_UPSTREAM_URL=https://YOUR-FEATHERPANEL-API.example.com
ROBLOX_MONITOR_API_KEY=YOUR_SHARED_SECRET
~~~

ما تحطش production secrets في GitHub.

تفاصيل الربط كاملة موجودة في ~~~text
cloudflare/README.md
~~~
