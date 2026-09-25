from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .bot import connect, is_manager

MAX_AMOUNT = 1_000_000_000
DEFAULT_CURRENCY_NAME = "دولار"
DEFAULT_CURRENCY_SYMBOL = "$"


def init_bank_tables() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS bank_settings (
                guild_id INTEGER PRIMARY KEY,
                currency_name TEXT NOT NULL DEFAULT 'دولار',
                currency_symbol TEXT NOT NULL DEFAULT '$'
            );

            CREATE TABLE IF NOT EXISTS bank_balances (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS bank_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                sender_id INTEGER,
                receiver_id INTEGER,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def get_currency(guild_id: int) -> tuple[str, str]:
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO bank_settings(guild_id, currency_name, currency_symbol) VALUES(?,?,?)",
            (guild_id, DEFAULT_CURRENCY_NAME, DEFAULT_CURRENCY_SYMBOL),
        )
        row = con.execute(
            "SELECT currency_name, currency_symbol FROM bank_settings WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    return row["currency_name"], row["currency_symbol"]


def set_currency(guild_id: int, name: str, symbol: str) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO bank_settings(guild_id, currency_name, currency_symbol)
            VALUES(?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
                currency_name=excluded.currency_name,
                currency_symbol=excluded.currency_symbol
            """,
            (guild_id, name[:40], symbol[:10] or DEFAULT_CURRENCY_SYMBOL),
        )


def ensure_balance(guild_id: int, user_id: int) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO bank_balances(guild_id,user_id,balance) VALUES(?,?,0)",
            (guild_id, user_id),
        )


def get_balance(guild_id: int, user_id: int) -> int:
    ensure_balance(guild_id, user_id)
    with connect() as con:
        row = con.execute(
            "SELECT balance FROM bank_balances WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    return int(row["balance"])


def add_balance(guild_id: int, user_id: int, amount: int, kind: str) -> int:
    ensure_balance(guild_id, user_id)
    with connect() as con:
        con.execute(
            "UPDATE bank_balances SET balance=balance+? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id),
        )
        con.execute(
            """
            INSERT INTO bank_transactions(
                guild_id, sender_id, receiver_id, amount, kind, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                guild_id,
                None,
                user_id,
                amount,
                kind,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        row = con.execute(
            "SELECT balance FROM bank_balances WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    return int(row["balance"])


def set_balance_value(guild_id: int, user_id: int, amount: int, kind: str) -> int:
    ensure_balance(guild_id, user_id)
    with connect() as con:
        old = con.execute(
            "SELECT balance FROM bank_balances WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        con.execute(
            "UPDATE bank_balances SET balance=? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id),
        )
        con.execute(
            """
            INSERT INTO bank_transactions(
                guild_id, sender_id, receiver_id, amount, kind, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                guild_id,
                None,
                user_id,
                amount,
                kind,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return int(old["balance"] if old else 0)


def transfer(guild_id: int, sender_id: int, receiver_id: int, amount: int) -> bool:
    ensure_balance(guild_id, sender_id)
    ensure_balance(guild_id, receiver_id)

    with connect() as con:
        sender = con.execute(
            "SELECT balance FROM bank_balances WHERE guild_id=? AND user_id=?",
            (guild_id, sender_id),
        ).fetchone()

        if not sender or int(sender["balance"]) < amount:
            return False

        con.execute(
            "UPDATE bank_balances SET balance=balance-? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, sender_id),
        )
        con.execute(
            "UPDATE bank_balances SET balance=balance+? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, receiver_id),
        )
        con.execute(
            """
            INSERT INTO bank_transactions(
                guild_id, sender_id, receiver_id, amount, kind, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                guild_id,
                sender_id,
                receiver_id,
                amount,
                "transfer",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return True


def top_balances(guild_id: int, limit: int = 10):
    with connect() as con:
        return con.execute(
            """
            SELECT user_id, balance
            FROM bank_balances
            WHERE guild_id=? AND balance > 0
            ORDER BY balance DESC
            LIMIT ?
            """,
            (guild_id, max(1, min(25, limit))),
        ).fetchall()


class Bank(commands.Cog):
    """The dedicated economy/bank system for the administration bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_bank_tables()

    @staticmethod
    def parse_amount(raw: str) -> int | None:
        try:
            amount = int(raw.replace(",", "").replace("_", ""))
        except (TypeError, ValueError):
            return None
        if amount <= 0 or amount > MAX_AMOUNT:
            return None
        return amount

    async def send_balance(self, interaction: discord.Interaction, member: discord.Member):
        name, symbol = get_currency(interaction.guild.id)
        balance = get_balance(interaction.guild.id, member.id)

        embed = discord.Embed(
            title="💰 " + member.display_name,
            description=f"**الرصيد:** {balance:,} {symbol}\n**العملة:** {name}",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="balance", description="عرض رصيدك أو رصيد عضو")
    @app_commands.describe(member="العضو المطلوب")
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await self.send_balance(interaction, member or interaction.user)

    @app_commands.command(name="pay", description="تحويل مبلغ إلى عضو")
    @app_commands.describe(member="العضو المستلم", amount="المبلغ")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(
                "❌ لا يمكن التحويل لنفسك أو إلى بوت.",
                ephemeral=True,
            )

        if not transfer(interaction.guild.id, interaction.user.id, member.id, int(amount)):
            return await interaction.response.send_message(
                "❌ رصيدك غير كافٍ.",
                ephemeral=True,
            )

        _, symbol = get_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ تم تحويل {amount:,} {symbol} إلى {member.mention}."
        )

    @app_commands.command(name="balance-top", description="عرض قائمة أغنى أعضاء السيرفر")
    async def balance_top(self, interaction: discord.Interaction):
        name, symbol = get_currency(interaction.guild.id)
        rows = top_balances(interaction.guild.id)

        if not rows:
            return await interaction.response.send_message(
                "❌ لا توجد أرصدة مسجلة حالياً.",
                ephemeral=True,
            )

        lines = []
        for index, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(int(row["user_id"]))
            mention = member.mention if member else f"<@{row['user_id']}>"
            lines.append(
                f"**#{index}** {mention} — **{int(row['balance']):,} {symbol}**"
            )

        embed = discord.Embed(
            title="OLD",
            description="قائمة أغنى أشخاص بالسيرفر:\n\n" + "\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"العملة: {name} {symbol}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="currency-settings", description="تغيير اسم ورمز العملة")
    @app_commands.describe(name="اسم العملة", symbol="رمز العملة")
    async def currency_settings(self, interaction: discord.Interaction, name: str, symbol: str = "$"):
        if not is_manager(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الإعداد للإدارة فقط.",
                ephemeral=True,
            )

        set_currency(interaction.guild.id, name, symbol)
        await interaction.response.send_message(
            f"✅ تم تغيير العملة إلى {name[:40]} {symbol[:10]}.",
            ephemeral=True,
        )

    @app_commands.command(name="currency-add", description="إضافة عملة إلى عضو")
    async def currency_add(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)

        balance = add_balance(interaction.guild.id, member.id, int(amount), "admin_add")
        _, symbol = get_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ تمت إضافة {amount:,} {symbol} إلى {member.mention}.\nالرصيد الجديد: {balance:,} {symbol}.",
            ephemeral=True,
        )

    @app_commands.command(name="currency-remove", description="إزالة عملة من عضو")
    async def currency_remove(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)

        old = get_balance(interaction.guild.id, member.id)
        new = max(0, old - int(amount))
        set_balance_value(interaction.guild.id, member.id, new, "admin_remove")
        _, symbol = get_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ تمت إزالة {old - new:,} {symbol} من {member.mention}.\nالرصيد الجديد: {new:,} {symbol}.",
            ephemeral=True,
        )

    @app_commands.command(name="currency-set", description="تحديد رصيد عضو")
    async def currency_set(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 0, MAX_AMOUNT]):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)

        set_balance_value(interaction.guild.id, member.id, int(amount), "admin_set")
        _, symbol = get_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ أصبح رصيد {member.mention}: {amount:,} {symbol}.",
            ephemeral=True,
        )

    @app_commands.command(name="currency-reset", description="تصفير رصيد عضو")
    async def currency_reset(self, interaction: discord.Interaction, member: discord.Member):
        if not is_manager(interaction.user):
            return await interaction.response.send_message("❌ هذا الأمر للإدارة فقط.", ephemeral=True)

        set_balance_value(interaction.guild.id, member.id, 0, "admin_reset")
        await interaction.response.send_message(
            f"✅ تم تصفير رصيد {member.mention}.",
            ephemeral=True,
        )

    async def handle_prefix(self, message: discord.Message) -> bool:
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return False

        content = message.content.strip()
        if content.startswith("!"):
            content = content[1:].strip()

        parts = content.split()
        if not parts:
            return False

        command = parts[0].casefold()

        if command in {"توب", "top", "balance-top"}:
            rows = top_balances(message.guild.id)
            name, symbol = get_currency(message.guild.id)
            if not rows:
                await message.reply("❌ ما كاين حتى رصيد مسجل دابا.", mention_author=False)
                return True

            lines = []
            for index, row in enumerate(rows, start=1):
                member = message.guild.get_member(int(row["user_id"]))
                mention = member.mention if member else f"<@{row['user_id']}>"
                lines.append(
                    f"**#{index}** {mention} — **{int(row['balance']):,} {symbol}**"
                )

            embed = discord.Embed(
                title="OLD",
                description="قائمة أغنى أشخاص بالسيرفر:\n\n" + "\n".join(lines),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"العملة: {name} {symbol}")
            await message.reply(embed=embed, mention_author=False)
            return True

        if command in {"بنك", "رصيدي", "balance", "c"}:
            target = message.mentions[0] if message.mentions else message.author
            balance = get_balance(message.guild.id, target.id)
            name, symbol = get_currency(message.guild.id)
            await message.reply(
                f"💰 {target.display_name} لديه {balance:,} {symbol} ({name}).",
                mention_author=False,
            )
            return True

        if command in {"اهداء", "إهداء", "اهدي", "أهدي"}:
            if not is_manager(message.author):
                await message.reply("❌ غير الإدارة تقدر تهدي العملات.", mention_author=False)
                return True

            target = message.mentions[0] if message.mentions else None
            raw_amount = parts[-1] if len(parts) >= 2 else ""
            amount = self.parse_amount(raw_amount)

            if not target or target.bot or not amount:
                await message.reply(
                    "❌ الاستعمال: اهداء @user 30.",
                    mention_author=False,
                )
                return True

            balance = add_balance(message.guild.id, target.id, amount, "admin_gift")
            _, symbol = get_currency(message.guild.id)
            await message.reply(
                f"✅ تم إهداء {amount:,} {symbol} إلى {target.mention}.\nالرصيد الجديد: {balance:,} {symbol}.",
                mention_author=False,
            )
            return True

        if command in {"دفع", "pay"} and len(parts) >= 3:
            target = message.mentions[0] if message.mentions else None
            amount = self.parse_amount(parts[-1])
            if not target or target.bot or target.id == message.author.id or not amount:
                await message.reply("❌ الاستعمال: دفع @user 30.", mention_author=False)
                return True

            if not transfer(message.guild.id, message.author.id, target.id, amount):
                await message.reply("❌ رصيدك غير كافٍ.", mention_author=False)
                return True

            _, symbol = get_currency(message.guild.id)
            await message.reply(
                f"✅ تم تحويل {amount:,} {symbol} إلى {target.mention}.",
                mention_author=False,
            )
            return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.handle_prefix(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Bank(bot))
