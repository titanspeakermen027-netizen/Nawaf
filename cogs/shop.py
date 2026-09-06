import json

import discord
from discord import app_commands
from discord.ext import commands

from database import connect, get_config, set_config


class AdDetailsModal(discord.ui.Modal):
    def __init__(self, cog, product_id: int, quantity: int):
        super().__init__(title="تفاصيل الإعلان")
        self.cog = cog
        self.product_id = product_id
        self.quantity = quantity
        self.text = discord.ui.TextInput(
            label="نص الإعلان",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            placeholder="اكتب الإعلان الذي تريد نشره...",
        )
        self.image = discord.ui.TextInput(
            label="رابط صورة الإعلان (اختياري)",
            required=False,
            max_length=500,
            placeholder="https://...",
        )
        self.add_item(self.text)
        self.add_item(self.image)

    async def on_submit(self, interaction: discord.Interaction):
        details = {"text": self.text.value.strip(), "image_url": self.image.value.strip() or None}
        await self.cog.checkout(interaction, self.product_id, self.quantity, json.dumps(details, ensure_ascii=False))


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def delivery_label(product):
        kind = product["delivery_type"] or "generic"
        return {"ad": "📢 إعلان", "role": "🎖️ رتبة", "generic": "📦 منتج"}.get(kind, "📦 منتج")

    @app_commands.command(name="shop", description="عرض منتجات المتجر")
    async def shop(self, interaction: discord.Interaction):
        with connect() as con:
            products = con.execute(
                "SELECT id,name,description,price,stock,delivery_type,role_id FROM shop_products "
                "WHERE guild_id=? AND active=1 ORDER BY id",
                (interaction.guild.id,),
            ).fetchall()
        cfg = get_config(interaction.guild.id)
        if not products:
            return await interaction.response.send_message("🛒 المتجر فارغ حالياً.", ephemeral=True)

        embed = discord.Embed(title="🛒 متجر Nawaf", description="استعمل `/shop-buy` للشراء.", color=discord.Color.blurple())
        for product in products[:25]:
            stock = "∞" if product["stock"] < 0 else str(product["stock"])
            description = product["description"] or "بدون وصف"
            if product["delivery_type"] == "role" and product["role_id"]:
                description += f"\n🎖️ الرتبة: <@&{product['role_id']}>"
            embed.add_field(
                name=f"#{product['id']} — {product['name']} • {self.delivery_label(product)}",
                value=f"{description}\n💰 **{product['price']:,} {cfg['currency_symbol']}**\n📦 المخزون: **{stock}**",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop-add", description="إضافة منتج للمتجر")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(kind="نوع المنتج", role="الرتبة إذا كان المنتج من نوع رتبة")
    @app_commands.choices(kind=[
        app_commands.Choice(name="منتج عادي", value="generic"),
        app_commands.Choice(name="شراء إعلان", value="ad"),
        app_commands.Choice(name="رتبة", value="role"),
    ])
    async def shop_add(
        self,
        interaction: discord.Interaction,
        name: str,
        price: app_commands.Range[int, 1, 1_000_000_000],
        description: str = "",
        stock: int = -1,
        kind: app_commands.Choice[str] = None,
        role: discord.Role | None = None,
    ):
        kind_value = kind.value if kind else "generic"
        if stock == 0 or stock < -1:
            return await interaction.response.send_message("❌ المخزون غير صالح.", ephemeral=True)
        if kind_value == "role" and role is None:
            return await interaction.response.send_message("❌ خاصك تحدد الرتبة لمنتج من نوع رتبة.", ephemeral=True)
        if role and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ البوت ما يقدرش يعطي هاد الرتبة. خاص رتبة البوت تكون فوقها.", ephemeral=True)

        with connect() as con:
            cur = con.execute(
                "INSERT INTO shop_products(guild_id,name,description,price,stock,delivery_type,role_id) VALUES(?,?,?,?,?,?,?)",
                (interaction.guild.id, name[:100], description[:1000], price, stock, kind_value, role.id if role else None),
            )
            product_id = cur.lastrowid
        await interaction.response.send_message(f"✅ تمت إضافة **#{product_id} — {name}** إلى المتجر.", ephemeral=True)

    @app_commands.command(name="shop-remove", description="حذف منتج من المتجر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_remove(self, interaction: discord.Interaction, product_id: int):
        with connect() as con:
            row = con.execute(
                "SELECT name FROM shop_products WHERE id=? AND guild_id=? AND active=1",
                (product_id, interaction.guild.id),
            ).fetchone()
            if not row:
                return await interaction.response.send_message("❌ المنتج غير موجود.", ephemeral=True)
            con.execute("UPDATE shop_products SET active=0 WHERE id=?", (product_id,))
        await interaction.response.send_message(f"✅ تم حذف **{row['name']}** من المتجر.", ephemeral=True)

    @app_commands.command(name="shop-settings", description="تحديد روم الطلبات")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_settings(self, interaction: discord.Interaction, channel: discord.TextChannel):
        set_config(interaction.guild.id, shop_order_channel=channel.id)
        await interaction.response.send_message(f"✅ روم الطلبات: {channel.mention}", ephemeral=True)

    @app_commands.command(name="shop-buy", description="شراء منتج من المتجر")
    async def shop_buy(self, interaction: discord.Interaction, product_id: int, quantity: app_commands.Range[int, 1, 100] = 1):
        with connect() as con:
            product = con.execute(
                "SELECT id,name,price,stock,delivery_type,role_id FROM shop_products WHERE id=? AND guild_id=? AND active=1",
                (product_id, interaction.guild.id),
            ).fetchone()
        if not product:
            return await interaction.response.send_message("❌ المنتج غير موجود أو غير متاح.", ephemeral=True)
        if product["delivery_type"] == "ad":
            return await interaction.response.send_modal(AdDetailsModal(self, product_id, quantity))
        await self.checkout(interaction, product_id, quantity, None)

    async def checkout(self, interaction: discord.Interaction, product_id: int, quantity: int, details: str | None):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        with connect() as con:
            product = con.execute(
                "SELECT * FROM shop_products WHERE id=? AND guild_id=? AND active=1",
                (product_id, guild_id),
            ).fetchone()
            if not product:
                return await self.respond(interaction, "❌ المنتج غير موجود أو غير متاح.")
            if product["stock"] >= 0 and product["stock"] < quantity:
                return await self.respond(interaction, "❌ المخزون غير كافٍ.")
            con.execute("INSERT OR IGNORE INTO balances(guild_id,user_id,balance) VALUES(?,?,0)", (guild_id, user_id))
            balance = con.execute(
                "SELECT balance FROM balances WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            ).fetchone()["balance"]
            total = product["price"] * quantity
            if balance < total:
                return await self.respond(interaction, "❌ رصيدك غير كافٍ.")
            con.execute(
                "UPDATE balances SET balance=balance-? WHERE guild_id=? AND user_id=?",
                (total, guild_id, user_id),
            )
            if product["stock"] >= 0:
                con.execute("UPDATE shop_products SET stock=stock-? WHERE id=?", (quantity, product_id))

            status = "pending"
            if product["delivery_type"] == "role" and product["role_id"]:
                role = interaction.guild.get_role(product["role_id"])
                try:
                    if role:
                        await interaction.user.add_roles(role, reason=f"Purchased shop product #{product_id}")
                        status = "completed"
                except discord.HTTPException:
                    status = "pending"

            cur = con.execute(
                "INSERT INTO shop_orders(guild_id,user_id,product_id,quantity,total_price,status,details) VALUES(?,?,?,?,?,?,?)",
                (guild_id, user_id, product_id, quantity, total, status, details),
            )
            order_id = cur.lastrowid

        cfg = get_config(guild_id)
        order_channel = interaction.guild.get_channel(cfg["shop_order_channel"]) if cfg["shop_order_channel"] else None
        if order_channel:
            embed = discord.Embed(title=f"🛒 طلب جديد #{order_id}", color=discord.Color.blurple())
            embed.add_field(name="العضو", value=interaction.user.mention, inline=True)
            embed.add_field(name="المنتج", value=product["name"], inline=True)
            embed.add_field(name="الكمية", value=str(quantity), inline=True)
            embed.add_field(name="الإجمالي", value=f"{total:,} {cfg['currency_symbol']}", inline=True)
            embed.add_field(name="الحالة", value=status, inline=True)
            if details:
                try:
                    data = json.loads(details)
                    embed.add_field(name="تفاصيل الإعلان", value=data.get("text", "—")[:1024], inline=False)
                    if data.get("image_url"):
                        embed.set_image(url=data["image_url"])
                except (json.JSONDecodeError, TypeError):
                    embed.add_field(name="التفاصيل", value=details[:1024], inline=False)
            await order_channel.send(embed=embed)

        await self.respond(
            interaction,
            f"✅ تم الشراء بنجاح!\n🛒 **{product['name']}** × {quantity}\n💰 **{total:,} {cfg['currency_symbol']}**\n🧾 الطلب: **#{order_id}**",
            ephemeral=True,
        )

    @staticmethod
    async def respond(interaction, content, ephemeral=True):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)


async def setup(bot):
    await bot.add_cog(Shop(bot))
