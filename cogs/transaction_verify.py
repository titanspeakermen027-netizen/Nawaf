from __future__ import annotations

import io
import random
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont


CODE_LENGTH = 6


def generate_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(CODE_LENGTH))


def build_code_image(code: str) -> discord.File:
    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 110)
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), code, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(((900 - width) / 2, (300 - height) / 2 - 8), code, fill="black", font=font)

    # Light noise lines make OCR/copying less trivial while staying readable.
    for _ in range(12):
        x1 = random.randint(20, 880)
        y1 = random.randint(20, 280)
        x2 = random.randint(20, 880)
        y2 = random.randint(20, 280)
        draw.line((x1, y1, x2, y2), fill=(180, 180, 180), width=2)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="transfer-verification.png")


async def wait_for_verification(bot, message: discord.Message, expected_code: str, timeout: float = 60) -> Optional[discord.Message]:
    def check(candidate: discord.Message) -> bool:
        return (
            candidate.author.id == message.author.id
            and candidate.channel.id == message.channel.id
            and not candidate.author.bot
        )

    try:
        candidate = await bot.wait_for("message", timeout=timeout, check=check)
    except TimeoutError:
        return None

    if candidate.content.strip() != expected_code:
        return None
    return candidate
