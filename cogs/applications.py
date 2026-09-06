import json

import discord
from discord import app_commands
from discord.ext import commands

from database import connect

MAX_QUESTIONS = 10


def get_types(guild_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_types WHERE guild_id=? AND enabled=1 ORDER BY id", (guild_id,)).fetchall()


def get_all_types():
    with connect() as con:
        return con.execute("SELECT * FROM application_types WHERE enabled=1 ORDER BY id").fetchall()


def get_type(type_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_types WHERE id=?", (type_id,)).fetchone()


def get_questions(type_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_questions WHERE type_id=? ORDER BY position", (type_id,)).fetchall()


def is_manager(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild
        and (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator)
    )


class TextModal(discord.ui.Modal):
    def __init__(self, title: str, label: str, callback):
        super().__init__(title=title[:45])
        self.callback_fn = callback
        self.value = discord.ui.TextInput(label=label[:45], style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_fn(interaction, self.value.value)


class ReasonModal(discord.ui.Modal):
    def __init__(self, application_id: int, status: str):
        super().__init__(title="سبب القرار")
        self.application_id = application_id
        self.status = status
        self.reason = discord.ui.TextInput(label="اكتب السبب", placeholder="اكتب سبب القبول/الرفض...", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await process_decision(interaction, self.application_id, self.status, self.reason.value)


async def process_decision(interaction: discord.Interaction, application_id: int, status: str, reason: str = ""):
    if not interaction.guild or not is_manager(interaction):
        return await interaction.response.send_message("❌ ما عندكش صلاحية مراجعة التقديمات.", ephemeral=True)

    with connect() as con:
        row = con.execute("SELECT * FROM applications WHERE id=? AND guild_id=?", (application_id, interaction.guild.id)).fetchone()
        if not row:
            return await interaction.response.send_message("❌ التقديم غير موجود.", ephemeral=True)
        if row["status"] != "pending":
            return await interaction.response.send_message("⚠️ هاد التقديم تمت مراجعته من قبل.", ephemeral=True)
        con.execute("UPDATE applications SET status=?, reviewer_id=?, review_reason=? WHERE id=?", (status, interaction.user.id, reason, application_id))

    member = interaction.guild.get_member(row["user_id"])
    app_type = get_type(row["type_id"]) if row["type_id"] else None

    if status == "accepted" and member and app_type and app_type["accepted_role_id"]:
        role = interaction.guild.get_role(app_type["accepted_role_id"])
        if role:
            try:
                await member.add_roles(role, reason=f"Application #{application_id} accepted")
            except discord.HTTPException:
                pass

    result_channel_id = app_type["result_channel_id"] if app_type else None
    result_channel = interaction.guild.get_channel(result_channel_id) if result_channel_id else None
    if result_channel:
        result_word = "قبول" if status == "accepted" else "رفض"
        mention = member.mention if member else f"<@{row['user_id']}>"
        embed = discord.Embed(title=f"📝 نتيجة التقديم #{application_id}", description=f"المتقدم: {mention}\nالنتيجة: **{result_word}**", color=discord.Color.green() if status == "accepted" else discord.Color.red())
        if reason:
            embed.add_field(name="السبب", value=reason, inline=False)
        embed.add_field(name="المراجع", value=interaction.user.mention, inline=True)
        await result_channel.send(embed=embed)

    if member:
        try:
            text = "تم قبولك" if status == "accepted" else "تم رفضك"
            msg = f"📝 **{text} في التقديم #{application_id}.**"
            if reason:
                msg += f"\nالسبب: {reason}"
            await member.send(msg)
        except discord.HTTPException:
            pass

    await interaction.response.send_message(f"✅ تم تسجيل {'القبول' if status == 'accepted' else 'الرفض'} للتقديم #{application_id}.", ephemeral=True)

    if interaction.message and interaction.message.embeds:
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green() if status == "accepted" else discord.Color.red()
        embed.add_field(name="الحالة", value=f"{'مقبول' if status == 'accepted' else 'مرفوض'} بواسطة {interaction.user.mention}", inline=False)
        if reason:
            embed.add_field(name="السبب", value=reason, inline=False)
        try:
            await interaction.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass


class ApplicationReview(discord.ui.View):
    def __init__(self, application_id: int):
        super().__init__(timeout=None)
        self.application_id = application_id
        buttons = [
            ("قبول", "✅", discord.ButtonStyle.success, "accept"),
            ("قبول مع سبب", "✅", discord.ButtonStyle.success, "accept_reason"),
            ("رفض", "❌", discord.ButtonStyle.danger, "reject"),
            ("رفض مع سبب", "❌", discord.ButtonStyle.danger, "reject_reason"),
        ]
        for label, emoji, style, action in buttons:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"nawaf:app:{application_id}:{action}")
            button.callback = getattr(self, action)
            self.add_item(button)

    async def accept(self, interaction):
        await process_decision(interaction, self.application_id, "accepted")

    async def accept_reason(self, interaction):
        await interaction.response.send_modal(ReasonModal(self.application_id, "accepted"))

    async def reject(self, interaction):
        await process_decision(interaction, self.application_id, "rejected")

    async def reject_reason(self, interaction):
        await interaction.response.send_modal(ReasonModal(self.application_id, "rejected"))


class DynamicApplicationModal(discord.ui.Modal):
    def __init__(self, cog, type_id: int, questions, start: int = 0):
        app_type = get_type(type_id)
        super().__init__(title=(app_type["name"] if app_type else "التقديم")[:45])
        self.cog = cog
        self.type_id = type_id
        self.questions = questions
        self.start = start
        self.inputs = []
        for index, question in enumerate(questions[start:start + 5], start=start + 1):
            field = discord.ui.TextInput(
                label=f"{index}. {question['question']}"[:45],
                style=discord.TextStyle.paragraph if len(question["question"]) > 35 else discord.TextStyle.short,
                required=bool(question["required"]),
                max_length=1000,
            )
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.user.id, self.type_id)
        draft = self.cog.drafts.setdefault(key, {})
        for offset, field in enumerate(self.inputs):
            draft[str(self.start + offset + 1)] = field.value
        next_start = self.start + len(self.inputs)
        if next_start < len(self.questions):
            return await interaction.response.send_modal(DynamicApplicationModal(self.cog, self.type_id, self.questions, next_start))
        await interaction.response.send_modal(ImageModal(self.cog, self.type_id))


class ImageModal(discord.ui.Modal):
    def __init__(self, cog, type_id: int):
        super().__init__(title="صورة التقديم")
        self.cog = cog
        self.type_id = type_id
        self.image = discord.ui.TextInput(label="رابط الصورة (اختياري)", placeholder="https://...", required=False, max_length=500)
        self.add_item(self.image)

    async def on_submit(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.user.id, self.type_id)
        draft = self.cog.drafts.pop(key, {})
        await self.cog.finish_application(interaction, self.type_id, draft, self.image.value.strip())


class ApplicationTypeView(discord.ui.View):
    def __init__(self, cog, types):
        super().__init__(timeout=None)
        self.cog = cog
        for position, app_type in enumerate(types[:5]):
            button = discord.ui.Button(label=app_type["name"][:80], style=discord.ButtonStyle.primary, custom_id=f"nawaf:application:type:{app_type['id']}", row=position)
            button.callback = self.make_callback(app_type["id"])
            self.add_item(button)

    def make_callback(self, type_id):
        async def callback(interaction):
            await self.cog.start_application(interaction, type_id)
        return callback


class ApplicationSettingsView(discord.ui.View):
    def __init__(self, cog, type_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.type_id = type_id

    async def interaction_check(self, interaction):
        if not is_manager(interaction):
            await interaction.response.send_message("❌ هاد الإعدادات للإدارة فقط.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📝 تعديل عنوان الـPanel", style=discord.ButtonStyle.primary, row=0)
    async def title(self, interaction, button):
        async def save(i, value):
            with connect() as con:
                con.execute("UPDATE application_types SET title=? WHERE id=?", (value, self.type_id))
            await i.response.send_message("✅ تم تحديث عنوان الـPanel.", ephemeral=True)
        await interaction.response.send_modal(TextModal("عنوان الـPanel", "العنوان", save))

    @discord.ui.button(label="🎨 اللون", style=discord.ButtonStyle.secondary, row=0)
    async def color(self, interaction, button):
        async def save(i, value):
            try:
                number = int(value.replace("#", ""), 16)
                if not 0 <= number <= 0xFFFFFF:
                    raise ValueError
            except ValueError:
                return await i.response.send_message("❌ اكتب اللون هكذا: `5865F2` أو `#5865F2`.", ephemeral=True)
            with connect() as con:
                con.execute("UPDATE application_types SET color=? WHERE id=?", (number, self.type_id))
            await i.response.send_message("✅ تم تحديث اللون.", ephemeral=True)
        await interaction.response.send_modal(TextModal("لون التقديم", "Hex Color", save))

    @discord.ui.button(label="🖼️ صورة التقديم", style=discord.ButtonStyle.secondary, row=0)
    async def image(self, interaction, button):
        async def save(i, value):
            with connect() as con:
                con.execute("UPDATE application_types SET image_url=? WHERE id=?", (value.strip() or None, self.type_id))
            await i.response.send_message("✅ تم تحديث صورة التقديم.", ephemeral=True)
        await interaction.response.send_modal(TextModal("صورة التقديم", "رابط الصورة", save))

    @discord.ui.button(label="❓ تعديل الأسئلة", style=discord.ButtonStyle.primary, row=1)
    async def questions(self, interaction, button):
        await interaction.response.send_modal(QuestionsModal(self.type_id))

    @discord.ui.button(label="📋 روم النتائج", style=discord.ButtonStyle.secondary, row=1)
    async def result_channel(self, interaction, button):
        await interaction.response.send_message("استعمل `/application-result-channel` لتحديد الروم.", ephemeral=True)

    @discord.ui.button(label="🛡️ رتبة القبول", style=discord.ButtonStyle.secondary, row=1)
    async def role(self, interaction, button):
        await interaction.response.send_message("استعمل `/application-role` لتحديد رتبة القبول.", ephemeral=True)

    @discord.ui.button(label="📨 إرسال الـPanel", style=discord.ButtonStyle.success, row=2)
    async def send(self, interaction, button):
        await self.cog.send_panel(interaction, self.type_id)


class QuestionsModal(discord.ui.Modal):
    def __init__(self, type_id: int):
        super().__init__(title="أسئلة التقديم")
        self.type_id = type_id
        self.fields = []
        current = get_questions(type_id)
        for index in range(MAX_QUESTIONS):
            value = current[index]["question"] if index < len(current) else ""
            field = discord.ui.TextInput(label=f"السؤال {index + 1}", default=value[:1000], required=False, style=discord.TextStyle.paragraph, max_length=1000)
            self.fields.append(field)
            self.add_item(field)

    async def on_submit(self, interaction):
        values = [field.value.strip() for field in self.fields if field.value.strip()]
        with connect() as con:
            con.execute("DELETE FROM application_questions WHERE type_id=?", (self.type_id,))
            for position, question in enumerate(values, start=1):
                con.execute("INSERT INTO application_questions(type_id,position,question,required) VALUES(?,?,?,1)", (self.type_id, position, question))
        await interaction.response.send_message(f"✅ تم حفظ {len(values)} سؤال.", ephemeral=True)


class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drafts = {}

    async def cog_load(self):
        with connect() as con:
            rows = con.execute("SELECT id FROM applications WHERE status='pending'").fetchall()
        for row in rows:
            self.bot.add_view(ApplicationReview(row["id"]))

    async def register_persistent_panels(self):
        types = get_all_types()
        for start in range(0, len(types), 5):
            self.bot.add_view(ApplicationTypeView(self, types[start:start + 5]))

    async def start_application(self, interaction, type_id):
        app_type = get_type(type_id)
        if not app_type or app_type["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message("❌ نوع التقديم غير موجود.", ephemeral=True)
        questions = get_questions(type_id)
        if not questions:
            return await interaction.response.send_message("❌ هاد التقديم ما فيه حتى سؤال. الإدارة خاصها تضبط الأسئلة أولاً.", ephemeral=True)
        self.drafts[(interaction.guild.id, interaction.user.id, type_id)] = {}
        await interaction.response.send_modal(DynamicApplicationModal(self, type_id, questions))

    async def finish_application(self, interaction, type_id, answers, image_url):
        app_type = get_type(type_id)
        if not app_type:
            return await interaction.response.send_message("❌ نوع التقديم غير موجود.", ephemeral=True)
        with connect() as con:
            cur = con.execute("INSERT INTO applications(guild_id,user_id,type_id,answers,image_url) VALUES(?,?,?,?,?)", (interaction.guild.id, interaction.user.id, type_id, json.dumps(answers, ensure_ascii=False), image_url or None))
            application_id = cur.lastrowid

        review_channel = interaction.guild.get_channel(app_type["review_channel_id"]) if app_type["review_channel_id"] else None
        if review_channel:
            embed = discord.Embed(title=f"{app_type['name']} • تقديم #{application_id}", description=f"**المتقدم:** {interaction.user.mention}\n**النوع:** {app_type['name']}", color=discord.Color(app_type["color"] or 0x5865F2))
            for question in get_questions(type_id):
                answer = answers.get(str(question["position"]), "—")
                embed.add_field(name=question["question"], value=answer[:1024], inline=False)
            if image_url:
                embed.set_image(url=image_url)
            await review_channel.send(embed=embed, view=ApplicationReview(application_id))
            self.bot.add_view(ApplicationReview(application_id))

        await interaction.response.send_message("✅ تم إرسال التقديم للإدارة بنجاح.", ephemeral=True)

    async def send_panel(self, interaction, type_id):
        app_type = get_type(type_id)
        if not app_type:
            return await interaction.response.send_message("❌ نوع التقديم غير موجود.", ephemeral=True)
        embed = discord.Embed(title=app_type["title"], description=app_type["description"], color=discord.Color(app_type["color"] or 0x5865F2))
        if app_type["image_url"]:
            embed.set_image(url=app_type["image_url"])
        await interaction.channel.send(embed=embed, view=ApplicationTypeView(self, [app_type]))
        await interaction.response.send_message("✅ تم إرسال Panel التقديم.", ephemeral=True)

    @app_commands.command(name="application", description="فتح نظام التقديم")
    async def application(self, interaction):
        types = get_types(interaction.guild.id)
        if not types:
            return await interaction.response.send_message("❌ ما كاين حتى نوع تقديم مفعل. الإدارة خاصها تستعمل `/application-create` أولاً.", ephemeral=True)
        embed = discord.Embed(title="📝 التقديم", description="اختار نوع التقديم اللي بغيتي من الأزرار بالأسفل.", color=0x5865F2)
        await interaction.response.send_message(embed=embed, view=ApplicationTypeView(self, types[:5]))

    @app_commands.command(name="application-create", description="إنشاء نوع تقديم جديد")
    @app_commands.check(is_manager)
    @app_commands.describe(name="اسم التقديم", questions="عدد الأسئلة من 1 إلى 10")
    async def create(self, interaction, name: str, questions: app_commands.Range[int, 1, 10] = 5):
        with connect() as con:
            cur = con.execute("INSERT INTO application_types(guild_id,name,title,description) VALUES(?,?,?,?)", (interaction.guild.id, name, f"تقديم {name}", "اضغط على الزر لبدء التقديم."))
            type_id = cur.lastrowid
            defaults = ["ما هو اسمك؟", "كم هو عمرك؟", "كم ساعة ناشط باليوم؟", "كيف ستفيد السيرفر؟", "اكتب خبرتك في Discord"]
            for position in range(1, questions + 1):
                question = defaults[position - 1] if position <= len(defaults) else f"السؤال {position}"
                con.execute("INSERT INTO application_questions(type_id,position,question,required) VALUES(?,?,?,1)", (type_id, position, question))
        await interaction.response.send_message(f"✅ تم إنشاء **{name}** (ID: `{type_id}`) بـ {questions} أسئلة. استعمل `/application-settings` لضبطه.", ephemeral=True)

    @app_commands.command(name="application-settings", description="فتح إعدادات نوع تقديم")
    @app_commands.check(is_manager)
    async def settings(self, interaction, type_id: int):
        app_type = get_type(type_id)
        if not app_type or app_type["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message("❌ ID غير صحيح.", ephemeral=True)
        embed = discord.Embed(title=f"⚙️ إعدادات {app_type['name']}", description="بدل الإعدادات من الأزرار ثم أرسل الـPanel.", color=app_type["color"] or 0x5865F2)
        await interaction.response.send_message(embed=embed, view=ApplicationSettingsView(self, type_id), ephemeral=True)

    @app_commands.command(name="application-review-channel", description="تحديد روم مراجعة التقديمات")
    @app_commands.check(is_manager)
    async def review_channel(self, interaction, channel: discord.TextChannel, type_id: int):
        with connect() as con:
            con.execute("UPDATE application_types SET review_channel_id=? WHERE id=? AND guild_id=?", (channel.id, type_id, interaction.guild.id))
        await interaction.response.send_message(f"✅ روم المراجعة: {channel.mention}", ephemeral=True)

    @app_commands.command(name="application-result-channel", description="تحديد روم نتائج التقديم")
    @app_commands.check(is_manager)
    async def result_channel(self, interaction, channel: discord.TextChannel, type_id: int):
        with connect() as con:
            con.execute("UPDATE application_types SET result_channel_id=? WHERE id=? AND guild_id=?", (channel.id, type_id, interaction.guild.id))
        await interaction.response.send_message(f"✅ روم النتائج: {channel.mention}", ephemeral=True)

    @app_commands.command(name="application-role", description="تحديد رتبة تعطى بعد القبول")
    @app_commands.check(is_manager)
    async def role(self, interaction, role: discord.Role, type_id: int):
        with connect() as con:
            con.execute("UPDATE application_types SET accepted_role_id=? WHERE id=? AND guild_id=?", (role.id, type_id, interaction.guild.id))
        await interaction.response.send_message(f"✅ رتبة القبول: {role.mention}", ephemeral=True)

    @app_commands.command(name="application-panel", description="إرسال Panel ديال جميع أنواع التقديم")
    @app_commands.check(is_manager)
    async def panel(self, interaction, channel: discord.TextChannel):
        types = get_types(interaction.guild.id)
        if not types:
            return await interaction.response.send_message("❌ أنشئ نوع تقديم أولاً باستعمال `/application-create`.", ephemeral=True)
        embed = discord.Embed(title="📝 التقديم", description="اختار نوع التقديم اللي بغيتي.", color=0x5865F2)
        await channel.send(embed=embed, view=ApplicationTypeView(self, types[:5]))
        await interaction.response.send_message(f"✅ تم إرسال Panel في {channel.mention}.", ephemeral=True)

    @app_commands.command(name="application-list", description="عرض أنواع التقديم")
    @app_commands.check(is_manager)
    async def listing(self, interaction):
        types = get_types(interaction.guild.id)
        if not types:
            return await interaction.response.send_message("ما كاين حتى نوع تقديم.", ephemeral=True)
        text = "\n".join(f"• `{row['id']}` — {row['name']} ({len(get_questions(row['id']))} أسئلة)" for row in types)
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Applications(bot))
