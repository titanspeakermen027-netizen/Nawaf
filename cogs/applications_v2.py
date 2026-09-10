from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from database import connect, get_config
from cogs.access_control import can_control_bot

MAX_QUESTIONS = 10


def is_manager(member: discord.Member) -> bool:
    return can_control_bot(member)


def get_types(guild_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_types WHERE guild_id=? AND enabled=1 ORDER BY id", (guild_id,)).fetchall()


def get_type(type_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_types WHERE id=?", (type_id,)).fetchone()


def get_questions(type_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM application_questions WHERE type_id=? ORDER BY position", (type_id,)).fetchall()


def color(value):
    try:
        return discord.Color(int(value or 0x5865F2))
    except (TypeError, ValueError):
        return discord.Color.blurple()


class ApplyFirstModal(discord.ui.Modal):
    def __init__(self, cog, type_id, questions):
        super().__init__(title="التقديم • الأسئلة 1-5")
        self.cog, self.type_id, self.questions = cog, type_id, questions
        for q in questions[:5]:
            field = discord.ui.TextInput(label=q["question"][:45], required=bool(q["required"]), style=discord.TextStyle.paragraph, max_length=1000)
            field.position = q["position"]
            self.add_item(field)

    async def on_submit(self, interaction):
        answers = {str(item.position): item.value.strip() for item in self.children if getattr(item, "value", None) is not None}
        total = len(self.questions)
        if total <= 5:
            await self.cog.finish_application(interaction, self.type_id, answers)
            return
        self.cog.drafts[(interaction.guild.id, interaction.user.id, self.type_id)] = answers
        await interaction.response.send_message("✅ تم حفظ الأسئلة الأولى. اضغط **متابعة** لإكمال باقي الأسئلة.", view=ContinueApplicationView(self.cog, self.type_id), ephemeral=True)


class ApplySecondModal(discord.ui.Modal):
    def __init__(self, cog, type_id, questions):
        super().__init__(title="التقديم • الأسئلة 6-10")
        self.cog, self.type_id, self.questions = cog, type_id, questions
        for q in questions[5:10]:
            field = discord.ui.TextInput(label=q["question"][:45], required=bool(q["required"]), style=discord.TextStyle.paragraph, max_length=1000)
            field.position = q["position"]
            self.add_item(field)

    async def on_submit(self, interaction):
        key = (interaction.guild.id, interaction.user.id, self.type_id)
        answers = dict(self.cog.drafts.pop(key, {}))
        answers.update({str(item.position): item.value.strip() for item in self.children if getattr(item, "value", None) is not None})
        await self.cog.finish_application(interaction, self.type_id, answers)


class ContinueApplicationView(discord.ui.View):
    def __init__(self, cog, type_id):
        super().__init__(timeout=300)
        self.cog, self.type_id = cog, type_id

    @discord.ui.button(label="متابعة", style=discord.ButtonStyle.primary, emoji="➡️")
    async def continue_(self, interaction, button):
        questions = get_questions(self.type_id)
        await interaction.response.send_modal(ApplySecondModal(self.cog, self.type_id, questions))
        self.stop()


class ApplicationTypeSelect(discord.ui.Select):
    def __init__(self, cog, rows):
        options = [discord.SelectOption(label=r["name"][:100], description=(r["description"] or "اضغط للبدء")[:100], emoji="📝", value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="اختر نوع التقديم...", options=options, custom_id="nawaf:application:type")
        self.cog = cog

    async def callback(self, interaction):
        await self.cog.start_application(interaction, int(self.values[0]))


class ApplicationPanel(discord.ui.View):
    def __init__(self, cog, rows):
        super().__init__(timeout=None)
        self.add_item(ApplicationTypeSelect(cog, rows))


class ReviewView(discord.ui.View):
    def __init__(self, cog, application_id):
        super().__init__(timeout=None)
        self.cog, self.application_id = cog, application_id

    @discord.ui.button(label="قبول", style=discord.ButtonStyle.success, emoji="✅", custom_id="nawaf:application:accept")
    async def accept(self, interaction, button):
        await self.cog.process_decision(interaction, self.application_id, "accepted", None)

    @discord.ui.button(label="رفض", style=discord.ButtonStyle.danger, emoji="❌", custom_id="nawaf:application:reject")
    async def reject(self, interaction, button):
        await self.cog.process_decision(interaction, self.application_id, "rejected", None)

    @discord.ui.button(label="رفض بسبب", style=discord.ButtonStyle.secondary, emoji="📝")
    async def reject_reason(self, interaction, button):
        await interaction.response.send_modal(RejectReasonModal(self.cog, self.application_id))


class RejectReasonModal(discord.ui.Modal):
    def __init__(self, cog, application_id):
        super().__init__(title="سبب رفض التقديم")
        self.cog, self.application_id = cog, application_id
        self.reason = discord.ui.TextInput(label="السبب", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        await self.cog.process_decision(interaction, self.application_id, "rejected", self.reason.value.strip())


class QuestionEditorModal(discord.ui.Modal):
    def __init__(self, cog, type_id, page, existing):
        super().__init__(title=f"تعديل الأسئلة • الصفحة {page}")
        self.cog, self.type_id, self.page = cog, type_id, page
        start = (page - 1) * 5
        for index in range(5):
            position = start + index + 1
            q = existing[position - 1] if position <= len(existing) else None
            field = discord.ui.TextInput(label=f"السؤال {position}", required=False, max_length=200, default=(q["question"] if q else ""))
            self.add_item(field)

    async def on_submit(self, interaction):
        start = (self.page - 1) * 5
        values = [field.value.strip() for field in self.children]
        with connect() as con:
            for index, text in enumerate(values):
                position = start + index + 1
                if text:
                    con.execute("INSERT INTO application_questions(type_id,position,question,required) VALUES(?,?,?,1) ON CONFLICT(type_id,position) DO UPDATE SET question=excluded.question", (self.type_id, position, text))
                else:
                    con.execute("DELETE FROM application_questions WHERE type_id=? AND position=?", (self.type_id, position))
        await interaction.response.send_message(f"✅ تم حفظ أسئلة الصفحة {self.page}.", ephemeral=True)


class QuestionsPageView(discord.ui.View):
    def __init__(self, cog, type_id):
        super().__init__(timeout=300)
        self.cog, self.type_id = cog, type_id

    async def open_page(self, interaction, page):
        questions = get_questions(self.type_id)
        if not questions:
            questions = []
        await interaction.response.send_modal(QuestionEditorModal(self.cog, self.type_id, page, questions))

    @discord.ui.button(label="أسئلة 1-5", style=discord.ButtonStyle.primary)
    async def p1(self, interaction, button): await self.open_page(interaction, 1)
    @discord.ui.button(label="أسئلة 6-10", style=discord.ButtonStyle.primary)
    async def p2(self, interaction, button): await self.open_page(interaction, 2)


class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drafts: dict[tuple[int, int, int], dict[str, str]] = {}

    async def cog_load(self):
        await self.register_persistent_panels()

    async def register_persistent_panels(self):
        for guild in self.bot.guilds:
            rows = get_types(guild.id)
            for start in range(0, len(rows), 25):
                self.bot.add_view(ApplicationPanel(self, rows[start:start + 25]))
            with connect() as con:
                pending = con.execute("SELECT id FROM applications WHERE guild_id=? AND status='pending'", (guild.id,)).fetchall()
            for row in pending:
                self.bot.add_view(ReviewView(self, row["id"]))

    async def start_application(self, interaction, type_id):
        typ = get_type(type_id)
        if not typ or typ["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message("❌ نوع التقديم غير موجود.", ephemeral=True)
        questions = get_questions(type_id)
        if not questions:
            return await interaction.response.send_message("❌ الإدارة لم تضبط الأسئلة بعد.", ephemeral=True)
        await interaction.response.send_modal(ApplyFirstModal(self, type_id, questions))

    async def finish_application(self, interaction, type_id, answers):
        typ = get_type(type_id)
        if not typ: return await interaction.response.send_message("❌ نوع التقديم غير موجود.", ephemeral=True)
        with connect() as con:
            cur = con.execute("INSERT INTO applications(guild_id,user_id,type_id,answers,status) VALUES(?,?,?,?, 'pending')", (interaction.guild.id, interaction.user.id, type_id, json.dumps(answers, ensure_ascii=False)))
            app_id = cur.lastrowid
        review = interaction.guild.get_channel(typ["review_channel_id"]) if typ["review_channel_id"] else None
        if review:
            e = discord.Embed(title=f"📝 {typ['name']} • #{app_id}", description=f"**المتقدم:** {interaction.user.mention}\n**النوع:** {typ['name']}", color=color(typ["color"]))
            for q in get_questions(type_id): e.add_field(name=q["question"], value=answers.get(str(q["position"]), "—")[:1024], inline=False)
            try: await review.send(embed=e, view=ReviewView(self, app_id))
            except discord.HTTPException: pass
        await interaction.response.send_message("✅ تم إرسال التقديم للإدارة.", ephemeral=True)

    async def process_decision(self, interaction, application_id, status, reason):
        if not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
            return await interaction.response.send_message("❌ للإدارة فقط.", ephemeral=True)
        with connect() as con:
            row = con.execute("SELECT * FROM applications WHERE id=? AND guild_id=?", (application_id, interaction.guild.id)).fetchone()
            if not row or row["status"] != "pending": return await interaction.response.send_message("❌ هذا التقديم غير متاح للمراجعة.", ephemeral=True)
            con.execute("UPDATE applications SET status=?, reviewer_id=?, review_reason=? WHERE id=?", (status, interaction.user.id, reason, application_id))
        typ=get_type(row["type_id"]); applicant=interaction.guild.get_member(row["user_id"])
        result_channel=interaction.guild.get_channel(typ["result_channel_id"]) if typ and typ["result_channel_id"] else None
        result_text = "✅ تم قبول تقديمك." if status == "accepted" else "❌ تم رفض تقديمك."
        if reason: result_text += f"\n**السبب:** {reason}"
        if status == "accepted" and typ and typ["accepted_role_id"] and applicant:
            role=interaction.guild.get_role(typ["accepted_role_id"])
            if role:
                try: await applicant.add_roles(role, reason="Application accepted")
                except discord.HTTPException: pass
        e=discord.Embed(title=f"📢 نتيجة التقديم #{application_id}",description=f"{applicant.mention if applicant else f'<@{row[\"user_id\"]}>'}\n\n{result_text}",color=discord.Color.green() if status=="accepted" else discord.Color.red())
        e.add_field(name="المراجع",value=interaction.user.mention)
        sent=False
        if result_channel:
            try: await result_channel.send(embed=e); sent=True
            except discord.HTTPException: pass
        if applicant:
            try: await applicant.send(embed=e)
            except discord.HTTPException: pass
        await interaction.response.edit_message(embed=e, view=None)
        await interaction.followup.send("✅ تم حفظ القرار وإرسال النتيجة." if sent else "⚠️ تم حفظ القرار، لكن لم يتم تحديد/الوصول إلى روم النتائج.", ephemeral=True)

    @app_commands.command(name="application", description="فتح لوحة التقديم")
    async def application(self, interaction):
        rows=get_types(interaction.guild.id)
        if not rows: return await interaction.response.send_message("❌ لا توجد أنواع تقديم مفعلة.",ephemeral=True)
        await interaction.response.send_message(embed=discord.Embed(title="📝 التقديم",description="اختر نوع التقديم.",color=0x5865F2),view=ApplicationPanel(self,rows[:25]))

    @app_commands.command(name="application-create",description="إنشاء نوع تقديم")
    async def create(self,interaction,name:str,questions:app_commands.Range[int,1,10]=5):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        with connect() as con:
            cur=con.execute("INSERT INTO application_types(guild_id,name,title,description) VALUES(?,?,?,?)",(interaction.guild.id,name,f"تقديم {name}","اضغط لبدء التقديم.")); type_id=cur.lastrowid
            defaults=["ما اسمك؟","كم عمرك؟","كم ساعة تنشط يومياً؟","كيف ستفيد السيرفر؟","ما خبرتك في Discord؟"]
            for p in range(1,questions+1): con.execute("INSERT INTO application_questions(type_id,position,question,required) VALUES(?,?,?,1)",(type_id,p,defaults[p-1] if p<=5 else f"السؤال {p}"))
        await interaction.response.send_message(f"✅ تم إنشاء **{name}** بمعرف `{type_id}`. تقدر تعدل الأسئلة من `/application-questions`.",ephemeral=True)

    @app_commands.command(name="application-questions",description="تعديل أسئلة التقديم")
    async def questions(self,interaction,type_id:int):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        typ=get_type(type_id)
        if not typ or typ["guild_id"]!=interaction.guild.id: return await interaction.response.send_message("❌ معرف التقديم غير صحيح.",ephemeral=True)
        qs=get_questions(type_id)
        await interaction.response.send_message(embed=discord.Embed(title=f"📝 أسئلة {typ['name']}",description=f"عدد الأسئلة: **{len(qs)}/{MAX_QUESTIONS}**\nاختار الصفحة التي تريد تعديلها.",color=0x5865F2),view=QuestionsPageView(self,type_id),ephemeral=True)

    @app_commands.command(name="application-review-channel",description="تحديد روم مراجعة التقديم")
    async def review_channel(self,interaction,channel:discord.TextChannel,type_id:int):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        with connect() as con: con.execute("UPDATE application_types SET review_channel_id=? WHERE id=? AND guild_id=?",(channel.id,type_id,interaction.guild.id))
        await interaction.response.send_message(f"✅ روم المراجعة: {channel.mention}",ephemeral=True)

    @app_commands.command(name="application-result-channel",description="تحديد روم نتائج التقديم")
    async def result_channel(self,interaction,channel:discord.TextChannel,type_id:int):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        with connect() as con: con.execute("UPDATE application_types SET result_channel_id=? WHERE id=? AND guild_id=?",(channel.id,type_id,interaction.guild.id))
        await interaction.response.send_message(f"✅ روم النتائج: {channel.mention}",ephemeral=True)

    @app_commands.command(name="application-role",description="تحديد رتبة القبول")
    async def role(self,interaction,role:discord.Role,type_id:int):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        with connect() as con: con.execute("UPDATE application_types SET accepted_role_id=? WHERE id=? AND guild_id=?",(role.id,type_id,interaction.guild.id))
        await interaction.response.send_message(f"✅ رتبة القبول: {role.mention}",ephemeral=True)

    @app_commands.command(name="application-panel",description="إرسال لوحة التقديم")
    async def panel(self,interaction,channel:discord.TextChannel):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        rows=get_types(interaction.guild.id)
        if not rows: return await interaction.response.send_message("❌ أنشئ نوع تقديم أولاً.",ephemeral=True)
        await channel.send(embed=discord.Embed(title="📝 التقديم",description="اختر نوع التقديم.",color=0x5865F2),view=ApplicationPanel(self,rows[:25]))
        await interaction.response.send_message(f"✅ تم إرسال اللوحة في {channel.mention}.",ephemeral=True)

    @app_commands.command(name="application-send-result",description="إعادة إرسال نتيجة تقديم إلى روم النتائج")
    async def send_result(self,interaction,application_id:int):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        with connect() as con: row=con.execute("SELECT * FROM applications WHERE id=? AND guild_id=?",(application_id,interaction.guild.id)).fetchone()
        if not row or row["status"]=="pending": return await interaction.response.send_message("❌ التقديم غير موجود أو لم تتم مراجعته بعد.",ephemeral=True)
        typ=get_type(row["type_id"]); ch=interaction.guild.get_channel(typ["result_channel_id"]) if typ and typ["result_channel_id"] else None
        if not ch: return await interaction.response.send_message("❌ لم يتم تحديد روم النتائج.",ephemeral=True)
        ok="✅ مقبول" if row["status"]=="accepted" else "❌ مرفوض"; e=discord.Embed(title=f"📢 نتيجة التقديم #{application_id}",description=f"المتقدم: <@{row['user_id']}>\nالنتيجة: **{ok}**\nالمراجع: <@{row['reviewer_id']}>"+(f"\nالسبب: {row['review_reason']}" if row["review_reason"] else ""),color=discord.Color.green() if row["status"]=="accepted" else discord.Color.red())
        await ch.send(embed=e); await interaction.response.send_message("✅ تم إرسال النتيجة.",ephemeral=True)

    @app_commands.command(name="application-list",description="عرض أنواع التقديم")
    async def listing(self,interaction):
        if not is_manager(interaction.user): return await interaction.response.send_message("❌ للإدارة فقط.",ephemeral=True)
        rows=get_types(interaction.guild.id)
        text="\n".join(f"• `{r['id']}` — {r['name']} ({len(get_questions(r['id']))}/10 أسئلة)" for r in rows) or "لا توجد أنواع."
        await interaction.response.send_message(text,ephemeral=True)


async def setup(bot):
    await bot.add_cog(Applications(bot))
