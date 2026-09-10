from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import discord
from discord.ext import commands

from cogs.game_channels import is_group_game_channel_allowed

VOTE_SECONDS = 30
GAME_NAMES = (
    "روليت",
    "معركة النرد",
    "مافيا",
    "حرب الفرق",
    "آخر ناجٍ",
    "تحدي الأرقام",
    "تحدي الإيموجي",
    "البحث عن الكنز",
    "مواجهة البقاء",
    "المواجهة",
)


class VoteSession:
    def __init__(self, guild_id: int, channel_id: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.votes: dict[int, int] = {}
        self.message: discord.Message | None = None
        self.task: asyncio.Task | None = None


class VoteSelect(discord.ui.Select):
    def __init__(self, cog: "GameVoting", session: VoteSession):
        self.cog = cog
        self.session = session
        options = [discord.SelectOption(label=name, value=str(index), emoji="🎮") for index, name in enumerate(GAME_NAMES)]
        super().__init__(placeholder="اختر اللعبة التي تريد التصويت لها...", options=options, custom_id="nawaf:game-vote")

    async def callback(self, interaction: discord.Interaction):
        key = (self.session.guild_id, self.session.channel_id)
        if self.cog.sessions.get(key) is not self.session:
            return await interaction.response.send_message("❌ انتهى التصويت.", ephemeral=True)
        if interaction.user.id in self.session.votes:
            return await interaction.response.send_message("❌ لا يمكن التراجع عن التصويت أو التصويت مرة أخرى.", ephemeral=True)
        self.session.votes[interaction.user.id] = int(self.values[0])
        await interaction.response.edit_message(embed=self.cog.build_embed(self.session), view=self.view)


class VoteView(discord.ui.View):
    def __init__(self, cog: "GameVoting", session: VoteSession):
        super().__init__(timeout=VOTE_SECONDS + 5)
        self.add_item(VoteSelect(cog, session))


class GenericGroupGame:
    """Fallback multiplayer event for group-game choices that do not have a dedicated engine yet."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: set[tuple[int, int]] = set()

    async def start(self, channel: discord.TextChannel, game_name: str):
        key = (channel.guild.id, channel.id)
        if key in self.active:
            return await channel.send("❌ توجد فعالية جماعية تعمل بالفعل في هذا الروم.")
        self.active.add(key)
        players: list[int] = []

        class JoinView(discord.ui.View):
            def __init__(self, outer: "GenericGroupGame"):
                super().__init__(timeout=30)
                self.outer = outer

            @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎮")
            async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.bot:
                    return await interaction.response.send_message("❌ البوتات لا تشارك.", ephemeral=True)
                if interaction.user.id in players:
                    return await interaction.response.send_message("❌ أنت مشارك بالفعل.", ephemeral=True)
                if len(players) >= 12:
                    return await interaction.response.send_message("❌ اكتمل الحد الأقصى.", ephemeral=True)
                players.append(interaction.user.id)
                await interaction.response.edit_message(embed=self.outer.lobby_embed(game_name, players), view=self)

            @discord.ui.button(label="خروج", style=discord.ButtonStyle.danger, emoji="🚪")
            async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id in players:
                    players.remove(interaction.user.id)
                    await interaction.response.edit_message(embed=self.outer.lobby_embed(game_name, players), view=self)
                else:
                    await interaction.response.send_message("❌ أنت لست مشاركاً.", ephemeral=True)

        view = JoinView(self)
        message = await channel.send(embed=self.lobby_embed(game_name, players), view=view)
        await asyncio.sleep(30)
        view.stop()
        if len(players) < 2:
            await channel.send(f"❌ لم يكتمل العدد، تم إلغاء **{game_name}**.")
        else:
            winner = random.choice(players)
            await channel.send(f"🏆 انتهت **{game_name}**! الفائز: <@{winner}>.")
        self.active.discard(key)

    @staticmethod
    def lobby_embed(name: str, players: list[int]) -> discord.Embed:
        return discord.Embed(
            title=f"🎮 {name}",
            description="انضم من الزر بالأسفل.\n\n" + ("\n".join(f"- <@{user_id}>" for user_id in players) or "- لا يوجد مشاركون بعد."),
            color=discord.Color.blurple(),
        )


class GameVoting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[tuple[int, int], VoteSession] = {}
        self.generic = GenericGroupGame(bot)

    def build_embed(self, session: VoteSession) -> discord.Embed:
        counts = [0] * len(GAME_NAMES)
        for game_index in session.votes.values():
            counts[game_index] += 1
        lines = [f"**{index + 1}.** {name} — **{counts[index]}** صوت" for index, name in enumerate(GAME_NAMES)]
        embed = discord.Embed(
            title="🗳️ التصويت على اللعبة الجماعية",
            description="اختر لعبة واحدة. **لا يمكن التراجع عن التصويت أو تغييره.**\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"ينتهي التصويت خلال {VOTE_SECONDS} ثانية • عدد المصوتين: {len(session.votes)}")
        return embed

    async def launch_winner(self, message: discord.Message, game_name: str):
        if game_name == "روليت":
            cog = self.bot.get_cog("RouletteMultiMessage")
            if cog:
                proxy = SimpleNamespace(guild=message.guild, channel=message.channel, author=message.guild.owner)
                await cog.start_lobby(proxy)
                return
        if game_name == "معركة النرد":
            cog = self.bot.get_cog("Games")
            if cog:
                proxy = SimpleNamespace(guild=message.guild, channel=message.channel, author=message.guild.owner)
                await cog.start_lobby(proxy)
                return
        await self.generic.start(message.channel, game_name)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.content.strip() != "-تصويت":
            return
        if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
            return await message.reply("❌ هاد الروم ما مسموحش فيه التصويت على الألعاب الجماعية.", mention_author=False)
        key = (message.guild.id, message.channel.id)
        if key in self.sessions:
            return await message.reply("❌ كاين تصويت مفتوح بالفعل.", mention_author=False)

        session = VoteSession(*key)
        self.sessions[key] = session
        view = VoteView(self, session)
        session.message = await message.channel.send(embed=self.build_embed(session), view=view)
        try:
            await asyncio.sleep(VOTE_SECONDS)
            if not session.votes:
                await message.channel.send("❌ انتهى التصويت بدون أي أصوات.")
                return
            counts = [0] * len(GAME_NAMES)
            for game_index in session.votes.values():
                counts[game_index] += 1
            highest = max(counts)
            winner_name = random.choice([GAME_NAMES[index] for index, value in enumerate(counts) if value == highest])
            await message.channel.send(f"**انتهى التصويت باختيار لعبة {winner_name}، ستبدأ اللعبة بعد قليل**")
            await asyncio.sleep(1.5)
            await self.launch_winner(message, winner_name)
        finally:
            self.sessions.pop(key, None)
            view.stop()


async def setup(bot: commands.Bot):
    await bot.add_cog(GameVoting(bot))
