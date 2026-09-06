from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass
from typing import Callable

import discord
from discord.ext import commands

from database import connect

INDIVIDUAL_REWARD = 1
ROUND_TIMEOUT = 20

WORDS = [
    "discord", "nawaf", "morocco", "gaming", "python", "server", "player",
    "rocket", "football", "computer", "keyboard", "internet", "champion", "victory",
    "dragon", "castle", "banana", "orange", "window", "sunrise",
]

FLAGS = {
    "🇲🇦": "المغرب", "🇫🇷": "فرنسا", "🇪🇸": "إسبانيا", "🇵🇹": "البرتغال",
    "🇩🇿": "الجزائر", "🇹🇳": "تونس", "🇪🇬": "مصر", "🇸🇦": "السعودية",
    "🇯🇵": "اليابان", "🇰🇷": "كوريا", "🇧🇷": "البرازيل", "🇺🇸": "أمريكا",
    "🇮🇹": "إيطاليا", "🇩🇪": "ألمانيا", "🇨🇦": "كندا", "🇬🇧": "بريطانيا",
}

COLORS = {
    "أحمر": "red", "red": "red", "أزرق": "blue", "blue": "blue",
    "أخضر": "green", "green": "green", "أصفر": "yellow", "yellow": "yellow",
    "بنفسجي": "purple", "purple": "purple", "برتقالي": "orange", "orange": "orange",
    "وردي": "pink", "pink": "pink", "أسود": "black", "black": "black",
    "أبيض": "white", "white": "white",
}

COLOR_EMOJIS = {
    "red": "🟥", "blue": "🟦", "green": "🟩", "yellow": "🟨",
    "purple": "🟪", "orange": "🟧", "pink": "🩷", "black": "⬛", "white": "⬜",
}

EMOJIS = ["😀", "😂", "😎", "🤔", "😍", "😡", "😭", "😴", "🤯", "🥳", "😱", "🤖"]


def add_individual_point(guild_id: int, user_id: int, amount: int = INDIVIDUAL_REWARD) -> None:
    try:
        from cogs.points import add_category_points
        add_category_points(guild_id, user_id, amount, "individual")
        return
    except Exception:
        pass
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO points(guild_id,user_id,points) VALUES(?,?,0)", (guild_id, user_id))
        con.execute("UPDATE points SET points=points+? WHERE guild_id=? AND user_id=?", (amount, guild_id, user_id))


def normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


@dataclass
class MiniRound:
    guild_id: int
    channel_id: int
    game_id: str
    answer: str | None = None
    started_at: float = 0.0
    message_id: int | None = None


class MiniGames(commands.Cog):
    """Ten fast mini-games based on the publicly documented Fizbo mini-game mechanics."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: dict[tuple[int, int], MiniRound] = {}
        self.handlers: dict[str, Callable] = {
            # Official Arabic shortcuts used by Fizbo.
            "-اسرع": self.fast_type,
            "-زر": self.fast_click,
            "-فكك": self.text_split,
            "-ادمج": self.merge_text,
            "-اعلام": self.guess_flag,
            "-اعكس": self.text_reverse,
            "-صحح": self.correct_letter,
            "-ترتيب": self.sort_numbers,
            "-لون": self.guess_color,
            "-ايموجي": self.find_emoji,
            # Existing English/transliterated aliases are kept for compatibility.
            "-فاستكليك": self.fast_click,
            "-fastclick": self.fast_click,
            "-فاستتايب": self.fast_type,
            "-fasttype": self.fast_type,
            "-تكستسبليت": self.text_split,
            "-textsplit": self.text_split,
            "-ميرجتكست": self.merge_text,
            "-mergetext": self.merge_text,
            "-خمنالعلم": self.guess_flag,
            "-guessflag": self.guess_flag,
            "-ريكفرس": self.text_reverse,
            "-textreverse": self.text_reverse,
            "-صححالحرف": self.correct_letter,
            "-correctletter": self.correct_letter,
            "-رتبالارقام": self.sort_numbers,
            "-sortnumbers": self.sort_numbers,
            "-خمناللون": self.guess_color,
            "-guesscolor": self.guess_color,
            "-خمنالايموجي": self.find_emoji,
            "-findemoji": self.find_emoji,
            # Text Reveal's documented Arabic command.
            "-اكشف الكلمة": self.text_reveal,
            "-اكشفالكلمة": self.text_reveal,
            "-textreveal": self.text_reveal,
        }

    def key(self, message: discord.Message) -> tuple[int, int]:
        return message.guild.id, message.channel.id

    async def finish(self, message: discord.Message, content: str, winner: discord.Member | None = None, delete_source: bool = False):
        if winner is not None:
            add_individual_point(message.guild.id, winner.id)
            content += f"\n🏆 {winner.mention} ربح **+{INDIVIDUAL_REWARD} نقطة**."
        await message.channel.send(content)
        if delete_source:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    def begin(self, message: discord.Message, game_id: str, answer: str | None) -> MiniRound | None:
        key = self.key(message)
        if key in self.active:
            return None
        round_ = MiniRound(message.guild.id, message.channel.id, game_id, answer, time.monotonic())
        self.active[key] = round_
        return round_

    def end(self, message: discord.Message) -> None:
        self.active.pop(self.key(message), None)

    async def winner_listener(self, message: discord.Message, predicate: Callable[[discord.Message], bool], timeout: float = ROUND_TIMEOUT) -> discord.Member | None:
        try:
            while True:
                candidate = await self.bot.wait_for(
                    "message",
                    timeout=timeout,
                    check=lambda m: m.guild and m.channel.id == message.channel.id and not m.author.bot,
                )
                if predicate(candidate):
                    return candidate.author
        except asyncio.TimeoutError:
            return None

    async def start_embed(self, message: discord.Message, title: str, description: str) -> discord.Message:
        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
        return await message.channel.send(embed=embed)

    async def fast_click(self, message: discord.Message):
        if self.begin(message, "fast_click", None) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        class ClickView(discord.ui.View):
            def __init__(self, cog: MiniGames, source: discord.Message):
                super().__init__(timeout=ROUND_TIMEOUT)
                self.cog, self.source = cog, source
                self.live_index = random.randrange(16)
                for i in range(16):
                    button = discord.ui.Button(label="ضغط", style=discord.ButtonStyle.secondary, row=i // 4)
                    async def callback(interaction: discord.Interaction, index=i, btn=button):
                        if index != self.live_index:
                            return await interaction.response.send_message("❌ الزر مازال ما ولاش أخضر.", ephemeral=True)
                        await interaction.response.edit_message(content=f"✅ {interaction.user.mention} ضغط فالمكان الصحيح!", view=None)
                        cog.end(source)
                        add_individual_point(source.guild.id, interaction.user.id)
                        await source.channel.send(f"🏆 {interaction.user.mention} ربح **+{INDIVIDUAL_REWARD} نقطة**.")
                        self.stop()
                    button.callback = callback
                    self.add_item(button)
        view = ClickView(self, message)
        msg = await message.channel.send("🖱️ **Fast Click**\nكاينين **16 أزرار**. تسنى حتى واحد منهم يولي أخضر ومن بعد ضغط عليه!", view=view)
        view.children[view.live_index].style = discord.ButtonStyle.success
        await msg.edit(view=view)
        await view.wait()
        if self.key(message) in self.active:
            self.end(message)
            await message.channel.send("⏱️ سالا الوقت وما ضغط حتى واحد.")

    async def fast_type(self, message: discord.Message):
        word = random.choice(WORDS)
        if self.begin(message, "fast_type", word) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "⚡ Fast Type", "كتب بالضبط الكلمة/الجملة اللي باينة تحت بسرعة!\n\n" + f"**`{word}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(word))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الكلمة الصحيحة هي **{word}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{word}**.")

    async def text_split(self, message: discord.Message):
        word = random.choice(WORDS)
        answer = " ".join(word)
        if self.begin(message, "text_split", answer) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "✂️ Text Split", f"كتب كل حرف مفصول بمسافة.\n\n**`{answer}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(answer))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الجواب الصحيح هو **{answer}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{answer}**.")

    async def merge_text(self, message: discord.Message):
        word = random.choice(WORDS)
        chunks = "  ".join(list(word))
        if self.begin(message, "merge_text", word) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🔗 Merge Text", f"جمع الحروف وكتب الكلمة كاملة بلا مسافات.\n\n**`{chunks}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(word))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الكلمة هي **{word}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{word}**.")

    async def guess_flag(self, message: discord.Message):
        flag, country = random.choice(list(FLAGS.items()))
        if self.begin(message, "guess_flag", country) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🚩 Guess The Flag", f"شنو هي الدولة ديال هاد العلم؟\n\n# {flag}")
        aliases = {normalize(country), normalize(country.replace("أمريكا", "الولايات المتحدة"))}
        winner = await self.winner_listener(message, lambda m: normalize(m.content) in aliases)
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الدولة هي **{country}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{country}**.")

    async def text_reverse(self, message: discord.Message):
        word = random.choice(WORDS)
        reversed_word = word[::-1]
        if self.begin(message, "text_reverse", word) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🔁 Text Reverse", f"رجّع هاد الكلمة للأصل.\n\n**`{reversed_word}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(word))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الكلمة الأصلية هي **{word}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{word}**.")

    async def correct_letter(self, message: discord.Message):
        word = random.choice(WORDS)
        pos = random.randrange(len(word))
        original = word[pos]
        letters = list(word)
        choices = [c for c in "abcdefghijklmnopqrstuvwxyz" if c != original]
        letters[pos] = random.choice(choices)
        wrong = "".join(letters)
        if self.begin(message, "correct_letter", word) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "✏️ Correct Letter", f"كاين حرف واحد غالط. صايح الكلمة وكتبها كاملة.\n\n**`{wrong}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(word))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الكلمة الصحيحة هي **{word}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الجواب كان **{word}**.")

    async def sort_numbers(self, message: discord.Message):
        numbers = random.sample(range(1, 100), 6)
        shuffled = numbers[:]
        random.shuffle(shuffled)
        answer = " ".join(map(str, sorted(numbers)))
        if self.begin(message, "sort_numbers", answer) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        shown = "  •  ".join(map(str, shuffled))
        await self.start_embed(message, "🔢 Sort Numbers", f"رتب الأرقام تصاعدياً وأرسلهم بالترتيب.\n\n**{shown}**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(answer))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الترتيب الصحيح هو **{answer}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الترتيب الصحيح هو **{answer}**.")

    async def guess_color(self, message: discord.Message):
        color = random.choice(list(COLOR_EMOJIS))
        display = COLOR_EMOJIS[color]
        if self.begin(message, "guess_color", color) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🎨 Guess The Color", f"شنو اسم هاد اللون؟\n\n# {display}")
        aliases = {key for key, value in COLORS.items() if value == color}
        winner = await self.winner_listener(message, lambda m: normalize(m.content) in {normalize(a) for a in aliases})
        self.end(message)
        label = next((k for k in aliases if any('\u0600' <= ch <= '\u06ff' for ch in k)), color)
        if winner:
            await self.finish(message, f"✅ اللون هو **{label}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. اللون هو **{label}**.")

    async def find_emoji(self, message: discord.Message):
        emoji = random.choice(EMOJIS)
        if self.begin(message, "emoji", emoji) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🔍 Find The Emoji", f"رسل نفس الإيموجي بالضبط.\n\n# {emoji}")
        winner = await self.winner_listener(message, lambda m: m.content.strip() == emoji)
        self.end(message)
        if winner:
            await self.finish(message, "✅ الإيموجي مطابق.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الإيموجي الصحيح هو {emoji}")

    async def text_reveal(self, message: discord.Message):
        word = random.choice(WORDS)
        hidden = list("_" * len(word))
        # Reveal a small number of letters while preserving the core mechanic:
        # players submit the completed word one letter at a time.
        reveal_count = max(1, len(word) // 4)
        for index in random.sample(range(len(word)), reveal_count):
            hidden[index] = word[index]
        pattern = " ".join(hidden)
        if self.begin(message, "text_reveal", word) is None:
            return await message.channel.send("⚠️ كاين mini-game خدام دابا فهاد الروم.")
        await self.start_embed(message, "🃏 Text Reveal", f"كمّل الكلمة وخمّنها.\n\n**`{pattern}`**")
        winner = await self.winner_listener(message, lambda m: normalize(m.content) == normalize(word))
        self.end(message)
        if winner:
            await self.finish(message, f"✅ الكلمة هي **{word}**.", winner)
        else:
            await message.channel.send(f"⏱️ سالا الوقت. الكلمة كانت **{word}**.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        handler = self.handlers.get(content)
        if handler is None:
            return
        await handler(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(MiniGames(bot))
