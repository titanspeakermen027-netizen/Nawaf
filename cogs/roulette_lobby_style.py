from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands

from cogs.game_channels import is_group_game_channel_allowed

ROULETTE_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/1543608188975325268/1544727017243549826/"
    "a3a22b8922412e080f008b2177c2ba80c5ba947a7c003716e06b184059052e15.png?"
    "ex=6a9ed4a4&is=6a9d8324&hm=584af97554c288cbffecb3b70e8854f0774613af5e63b3a94c3d1981732a76dc&"
)


class RouletteLobbyStyle(commands.Cog):
    """Roulette lobby presentation and lifecycle integration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patched = False

    async def cog_load(self):
        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is None or self._patched:
            return

        def lobby_embed(session, guild: discord.Guild) -> discord.Embed:
            expires_at = getattr(session, "lobby_expires_at", int(time.time()) + 30)
            mentions = []
            for uid in session.players:
                member = guild.get_member(uid)
                if member and not member.bot:
                    mentions.append(f"- {member.mention}")
            participants = "\n".join(mentions) if mentions else "- مازال حتى لاعب."
            description = (
                "**__شرح اللعبة:__**\n"
                "1- انضم للعبة عبر الزر الاخضر الموجود في الأسفل.\n"
                "2- تدور العجلة كل جولة وتختار لاعباً.\n"
                "3- اللاعب المختار يمكنه طرد لاعب، ينسحب، أو يستخدم خاصية من حقيبته.\n"
                "4- في اخر جولة تدور فيها العجله، من يتم اختياره يفوز في اللعبة.\n\n"
                f"**__عدد المشاركين ({len(session.players)}/{session.max_players}):__**\n\n"
                f"{participants}\n\n"
                f"⏳ <t:{expires_at}:R>"
            )
            embed = discord.Embed(
                title=guild.name,
                description=description,
                color=discord.Color.blurple(),
            )
            embed.set_image(url=ROULETTE_IMAGE_URL)
            return embed

        async def update_lobby(session, remaining=30):
            if not session.lobby_message or getattr(session, "cancelled", False):
                return
            guild = self.bot.get_guild(session.guild_id)
            if not guild:
                return
            try:
                await session.lobby_message.edit(embed=lobby_embed(session, guild))
            except (discord.HTTPException, discord.Forbidden):
                pass

        async def styled_start_lobby(message: discord.Message):
            roulette_instance = roulette
            if not is_group_game_channel_allowed(message.guild.id, message.channel.id):
                return await message.reply(
                    "❌ هاد الروم ما مسموحش فيه الألعاب الجماعية.", mention_author=False
                )

            key = (message.guild.id, message.channel.id)
            if key in roulette_instance.sessions:
                return await message.reply(
                    "❌ كاينة روليت مفتوحة فهاد الروم.", mention_author=False
                )

            module = __import__(
                "cogs.roulette_multi_message",
                fromlist=["Session", "LobbyView"],
            )
            session_obj = module.Session(message.guild.id, message.channel.id, message.author.id)
            session_obj.cancelled = False
            session_obj.lobby_task = asyncio.current_task()
            roulette_instance.sessions[key] = session_obj
            session_obj.lobby_expires_at = int(time.time()) + getattr(module, "LOBBY_SECONDS", 30)

            webhook = await roulette_instance.get_game_webhook(message.guild, message.channel)
            session_obj.lobby_webhook = webhook

            embed = lobby_embed(session_obj, message.guild)
            view = module.LobbyView(roulette_instance, session_obj)

            if webhook is not None:
                try:
                    session_obj.lobby_message = await webhook.send(
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True),
                        wait=True,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    roulette_instance.game_webhooks.pop(key, None)
                    session_obj.lobby_message = await message.channel.send(
                        embed=embed,
                        view=view,
                    )
            else:
                session_obj.lobby_message = await message.channel.send(
                    embed=embed,
                    view=view,
                )

            try:
                lobby_seconds = getattr(module, "LOBBY_SECONDS", 30)
                min_players = getattr(module, "MIN_PLAYERS", 4)
                for _remaining in range(lobby_seconds - 1, -1, -1):
                    await asyncio.sleep(1)
                    if (
                        roulette_instance.sessions.get(key) is not session_obj
                        or session_obj.active
                        or session_obj.cancelled
                    ):
                        return
                    await update_lobby(session_obj, _remaining)

                if session_obj.cancelled or roulette_instance.sessions.get(key) is not session_obj:
                    return

                if len(session_obj.players) < min_players:
                    roulette_instance.sessions.pop(key, None)
                    session_obj.cancelled = True
                    failure = f"❌ الغيت اللعبة عشان ما دخل على الاقل {min_players} لاعبين"
                    try:
                        await session_obj.lobby_message.edit(view=None)
                    except discord.HTTPException:
                        pass
                    try:
                        await session_obj.lobby_message.reply(failure, mention_author=False)
                    except discord.HTTPException:
                        await message.channel.send(failure)
                    return

                session_obj.active = True
                try:
                    await session_obj.lobby_message.edit(view=None)
                except discord.HTTPException:
                    pass

                await session_obj.lobby_message.reply(
                    "**اللاعبين تجهزو اللعبة راح تبدا بعد شوي**",
                    mention_author=False,
                )

                try:
                    from cogs.premium import is_premium, UPSELL_TEXT
                    if not is_premium(session_obj.guild_id):
                        await roulette_instance.game_send(
                            session_obj,
                            message.channel,
                            content=UPSELL_TEXT,
                        )
                except Exception:
                    pass

                await asyncio.sleep(1)
                if not session_obj.cancelled:
                    await roulette_instance.run_game(session_obj, message.channel)
            except asyncio.CancelledError:
                session_obj.cancelled = True
                roulette_instance.sessions.pop(key, None)
                raise
            finally:
                if roulette_instance.sessions.get(key) is session_obj:
                    roulette_instance.sessions.pop(key, None)

        roulette.lobby_embed = lobby_embed
        roulette.update_lobby = update_lobby
        roulette.start_lobby = styled_start_lobby
        self._patched = True


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteLobbyStyle(bot))
