from __future__ import annotations

import math

import discord
from discord.ext import commands


PREFIX_COMMANDS = {
    "🎮 الألعاب": [
        ("-روليت", "تشغيل روليت الإقصاء الجماعية — تبدأ تلقائياً بعد 30 ثانية"),
        ("!نرد [النقاط] [الحد]", "تشغيل معركة النرد الجماعية"),
        ("!دخول", "الدخول إلى اللعبة الجماعية الحالية"),
        ("!خروج", "الخروج من اللوبي قبل البداية"),
        ("!ابدأ", "بدء لعبة جماعية متوافقة مع النظام القديم"),
        ("!انهاء", "إغلاق اللعبة الجماعية للإدارة"),
        ("!العاب", "عرض قائمة الألعاب"),
    ],
    "🛡️ الإدارة": [
        ("-تحويل @user amount", "إضافة نقاط لعضو بواسطة الإدارة"),
        ("-نقاطي", "عرض نقاطك"),
        ("!حذف", "حذف جميع التكتات بواسطة الإدارة"),
    ],
}


class HelpView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: int):
        super().__init__(timeout=120)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ غير صاحب أمر `/help` يقدر يستعمل أزرار التنقل.", ephemeral=True)
            return False
        return True

    async def render(self, interaction: discord.Interaction):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="السابق", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        await self.render(interaction)

    @discord.ui.button(label="التالي", style=discord.ButtonStyle.primary, emoji="▶️")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await self.render(interaction)

    @discord.ui.button(label="إغلاق", style=discord.ButtonStyle.danger, emoji="✖️")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def slash_commands_by_category(self) -> dict[str, list[tuple[str, str]]]:
        categories: dict[str, list[tuple[str, str]]] = {
            "💰 الاقتصاد": [],
            "🎮 الألعاب": [],
            "🎫 التذاكر والتقديم": [],
            "🛡️ الإدارة والحماية": [],
            "📈 المستوى والرسائل": [],
            "⚙️ الإعدادات والأدوات": [],
        }

        for command in sorted(self.bot.tree.get_commands(), key=lambda item: item.name):
            name = f"/{command.qualified_name}"
            description = command.description or "بدون وصف"
            text = f"{name} — {description}"

            lower = command.qualified_name.lower()
            if any(word in lower for word in ("balance", "pay", "currency", "shop")):
                category = "💰 الاقتصاد"
            elif any(word in lower for word in ("game", "coinflip", "solo-dice", "rps", "points")):
                category = "🎮 الألعاب"
            elif any(word in lower for word in ("ticket", "application")):
                category = "🎫 التذاكر والتقديم"
            elif any(word in lower for word in ("warn", "ban", "mute", "jail", "kick", "delete", "moder")):
                category = "🛡️ الإدارة والحماية"
            elif any(word in lower for word in ("level", "xp", "message")):
                category = "📈 المستوى والرسائل"
            else:
                category = "⚙️ الإعدادات والأدوات"

            categories[category].append((name, description))

        return categories

    def build_pages(self) -> list[discord.Embed]:
        categories = self.slash_commands_by_category()
        pages: list[discord.Embed] = []

        intro = discord.Embed(
            title="📚 Nawaf Help",
            description=(
                "هنا تلقى أوامر Nawaf كاملة مقسمة حسب النظام.\n\n"
                "**Slash Commands** كتخدم بـ `/`\n"
                "**Prefix Commands** كتخدم بـ `!` أو `C` حسب إعداد البوت.\n\n"
                "🎰 الروليت الجديدة كتبدأ فقط بـ `-روليت`."
            ),
            color=discord.Color.blurple(),
        )
        intro.add_field(name="📦 الأنظمة", value=f"**{len(categories)}** أقسام", inline=True)
        intro.add_field(name="⚡ Slash Commands", value=f"**{len(self.bot.tree.get_commands())}** أمر", inline=True)
        intro.add_field(name="💬 Prefix Commands", value="ألعاب + إدارة", inline=True)
        intro.set_footer(text="Nawaf • صفحة المساعدة")
        pages.append(intro)

        for category, commands_list in categories.items():
            if not commands_list:
                continue
            embed = discord.Embed(title=f"{category}", color=discord.Color.blurple())
            lines = [f"`{name}` — {description}" for name, description in commands_list]
            chunks = [lines[i : i + 12] for i in range(0, len(lines), 12)]
            for chunk_index, chunk in enumerate(chunks, start=1):
                name = category if len(chunks) == 1 else f"{category} — {chunk_index}"
                embed.add_field(name=name, value="\n".join(chunk), inline=False)
                if chunk_index != len(chunks):
                    break
            pages.append(embed)

        prefix_embed = discord.Embed(title="💬 Prefix Commands", color=discord.Color.dark_gold())
        for category, command_list in PREFIX_COMMANDS.items():
            prefix_embed.add_field(
                name=category,
                value="\n".join(f"`{name}` — {description}" for name, description in command_list),
                inline=False,
            )
        prefix_embed.set_footer(text="استعمال أوامر الإدارة يتطلب الصلاحيات المناسبة")
        pages.append(prefix_embed)
        return pages

    @discord.app_commands.command(name="help", description="عرض جميع أوامر وأنظمة Nawaf")
    async def help_command(self, interaction: discord.Interaction):
        pages = self.build_pages()
        view = HelpView(pages, interaction.user.id)
        view.previous.disabled = True
        view.next.disabled = len(pages) <= 1
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
