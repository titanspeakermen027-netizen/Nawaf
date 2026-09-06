# Nawaf Discord Bot

بوت Discord مكتوب بـ Python و `discord.py 2.x` مع SQLite.

## المميزات

- 💬 `/send` إرسال رسالة من البوت إلى روم يحدده المشرف.
- 📩 `/say-member` إرسال رسالة خاصة إلى عضو.
- 🎫 Tickets مع منع صاحب التذكرة من إغلاقها، وإغلاق من الإدارة، ثم تقييم من 1 إلى 5 مع ملاحظة.
- 📈 XP وLevels تلقائياً مع `/level`.
- 📝 نظام تقديم عبر Modal مع قبول/رفض وإشعار للمتقدم.
- 📢 `/announce` إعلانات Embed إلى روم محدد.
- 🕌 أذكار تلقائية قابلة للتفعيل وتحديد الروم والفاصل الزمني.
- 🪙 عملة خاصة بكل سيرفر مع `/balance` و`/pay` و`/currency-settings` و`/currency-add`.
- 🛒 متجر بالعملة الخاصة بالسيرفر: إضافة وحذف المنتجات، عرض المتجر، الشراء، المخزون، وأرقام الطلبات.
- 🧾 تسجيل الطلبات في SQLite مع إمكانية تحديد روم لإشعارات الطلبات عبر `/shop-settings`.
- ⚙️ `/config` لعرض إعدادات الأنظمة الرئيسية.

## أوامر المتجر

- `/shop` عرض المنتجات المتاحة.
- `/shop-buy product_id quantity` شراء منتج.
- `/shop-add name price description stock` إضافة منتج — `stock=-1` يعني مخزون غير محدود.
- `/shop-remove product_id` إخفاء منتج من المتجر.
- `/shop-settings channel` تحديد روم إشعارات الطلبات.

## التشغيل

1. ثبّت Python 3.11+.
2. نفّذ `pip install -r requirements.txt`.
3. أنشئ ملف `.env` وضع فيه:
   `DISCORD_TOKEN=توكن_البوت`
4. شغّل `python main.py`.

فعّل في Discord Developer Portal الـMessage Content Intent وServer Members Intent لأن بعض الأنظمة تحتاجها.

قاعدة البيانات تُنشأ تلقائياً عند تشغيل البوت، وتدعم ترقية قواعد البيانات القديمة بإضافة جداول المتجر الجديدة دون حذف البيانات السابقة.
