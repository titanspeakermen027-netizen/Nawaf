from __future__ import annotations

import io
from pathlib import Path
from types import MethodType

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from database import connect

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
POINT_ART = ASSETS_DIR / "points.png"
POINT_ART_FALLBACK = ASSETS_DIR / "points_1.png"

CATEGORY_COLUMNS = {
    "individual": "individual_points",
    "group": "group_points",
    "roulette": "roulette_points",
}

CATEGORY_LABELS = {
    "individual": "🎯 الألعاب الفردية",
    "group": "👥 الألعاب الجماعية",
    "roulette": "🎡 الروليت",
}


def ensure_point_columns() -> None:
    with connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(points)")}
        added_any = False
        for column in CATEGORY_COLUMNS.values():
            if column not in columns:
                con.execute(f"ALTER TABLE points ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
                added_any = True

        # Older Nawaf versions kept all game points in the legacy `points`
        # column. Preserve that balance as group-game points during migration
        # instead of silently resetting users to zero.
        if added_any:
            con.execute(
                "UPDATE points SET group_points=points "
                "WHERE individual_points=0 AND group_points=0 AND roulette_points=0"
            )

        # Keep the legacy total synchronized with the three categories.
        con.execute(
            "UPDATE points SET points=individual_points+group_points+roulette_points"
        )


def add_category_points(guild_id: int, user_id: int, amount: int, category: str) -> None:
    if category not in CATEGORY_COLUMNS:
        raise ValueError(f"Unknown points category: {category}")
    amount = int(amount)
    if amount == 0:
        return
    ensure_point_columns()
    column = CATEGORY_COLUMNS[category]
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO points(guild_id,user_id,points,individual_points,group_points,roulette_points) "
            "VALUES(?,?,0,0,0,0)",
            (guild_id, user_id),
        )
        con.execute(
            f"UPDATE points SET {column}={column}+?, points=individual_points+group_points+roulette_points "
            "WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id),
        )


def get_points(guild_id: int, user_id: int) -> dict[str, int]:
    ensure_point_columns()
    with connect() as con:
        row = con.execute(
            "SELECT individual_points, group_points, roulette_points "
            "FROM points WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    if not row:
        return {"individual": 0, "group": 0, "roulette": 0, "total": 0}
    individual = int(row["individual_points"] or 0)
    group = int(row["group_points"] or 0)
    roulette = int(row["roulette_points"] or 0)
    return {
        "individual": individual,
        "group": group,
        "roulette": roulette,
        "total": individual + group + roulette,
    }


def _font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_background(path: Path | None, width: int = 1200, height: int = 675) -> Image.Image:
    if path and path.is_file():
        try:
            image = Image.open(path).convert("RGB")
            scale = max(width / image.width, height / image.height)
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
            left = max(0, (image.width - width) // 2)
            top = max(0, (image.height - height) // 2)
            return image.crop((left, top, left + width, top + height))
        except (OSError, ValueError):
            pass
    return Image.new("RGB", (width, height), (18, 16, 23))


def build_points_image(member: discord.Member, values: dict[str, int]) -> discord.File:
    background_path = POINT_ART if POINT_ART.is_file() else POINT_ART_FALLBACK
    image = _fit_background(background_path)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rounded_rectangle(
        (48, 42, image.width - 48, image.height - 42),
        radius=38,
        fill=(9, 9, 14, 205),
        outline=(255, 255, 255, 105),
        width=3,
    )
    draw.rounded_rectangle(
        (82, 78, image.width - 82, 173),
        radius=24,
        fill=(255, 255, 255, 25),
    )

    title_font = _font(50)
    name_font = _font(31)
    label_font = _font(28)
    value_font = _font(34)
    total_font = _font(44)
    small_font = _font(22, bold=False)

    title = "نقاطي"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((image.width - (title_box[2] - title_box[0])) / 2, 98), title, font=title_font, fill=(255, 255, 255, 255))

    display_name = member.display_name or member.name
    if len(display_name) > 24:
        display_name = display_name[:23] + "…"
    name_box = draw.textbbox((0, 0), display_name, font=name_font)
    draw.text(((image.width - (name_box[2] - name_box[0])) / 2, 190), display_name, font=name_font, fill=(235, 235, 242, 255))

    rows = [
        (CATEGORY_LABELS["individual"], values["individual"]),
        (CATEGORY_LABELS["group"], values["group"]),
        (CATEGORY_LABELS["roulette"], values["roulette"]),
    ]

    y = 255
    for label, value in rows:
        draw.rounded_rectangle((105, y, image.width - 105, y + 88), radius=22, fill=(255, 255, 255, 18), outline=(255, 255, 255, 45), width=2)
        draw.text((132, y + 25), label, font=label_font, fill=(248, 248, 252, 255))
        value_text = str(value)
        value_box = draw.textbbox((0, 0), value_text, font=value_font)
        draw.text((image.width - 132 - (value_box[2] - value_box[0]), y + 20), value_text, font=value_font, fill=(255, 255, 255, 255))
        y += 103

    total_text = f"⭐ المجموع: {values['total']}"
    total_box = draw.textbbox((0, 0), total_text, font=total_font)
    draw.rounded_rectangle((175, 585, image.width - 175, 655), radius=22, fill=(255, 255, 255, 28))
    draw.text(((image.width - (total_box[2] - total_box[0])) / 2, 594), total_text, font=total_font, fill=(255, 255, 255, 255))

    footer = "Nawaf Points System"
    footer_box = draw.textbbox((0, 0), footer, font=small_font)
    draw.text(((image.width - (footer_box[2] - footer_box[0])) / 2, 670 - (footer_box[3] - footer_box[1])), footer, font=small_font, fill=(220, 220, 228, 210))

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="points.png")


class Points(commands.Cog):
    """Categorized game points and an image-based points card."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patched = False

    def _patch_legacy_point_methods(self) -> None:
        if self._patched:
            return

        games = self.bot.get_cog("Games")
        if games is not None and not getattr(games, "_nawaf_points_patched", False):
            def games_add_points(_self, guild_id: int, user_id: int, amount: int):
                add_category_points(guild_id, user_id, amount, "group")
            games.add_points = MethodType(games_add_points, games)
            games._nawaf_points_patched = True

        dice = self.bot.get_cog("DiceUpgrade")
        if dice is not None and not getattr(dice, "_nawaf_points_patched", False):
            def dice_add_points(_self, guild_id: int, user_id: int, amount: int):
                add_category_points(guild_id, user_id, amount, "group")
            dice.add_points = MethodType(dice_add_points, dice)
            dice._nawaf_points_patched = True

        roulette = self.bot.get_cog("RouletteMultiMessage")
        if roulette is not None and not getattr(roulette, "_nawaf_points_patched", False):
            def roulette_add_points(_self, guild_id: int, user_id: int, amount: int):
                add_category_points(guild_id, user_id, amount, "roulette")
            roulette.add_points = MethodType(roulette_add_points, roulette)
            roulette._nawaf_points_patched = True

        self._patched = True

    async def cog_load(self):
        ensure_point_columns()
        self._patch_legacy_point_methods()

    async def points_for(self, guild_id: int, user_id: int) -> dict[str, int]:
        return get_points(guild_id, user_id)

    async def _reply(self, message: discord.Message, content: str, **kwargs):
        kwargs.setdefault("mention_author", False)
        return await message.reply(content, **kwargs)

    async def handle_prefix(self, message: discord.Message) -> bool:
        content = message.content.strip()
        parts = content.split()
        if not parts or parts[0] not in {"-نقاطي", "-نقاط"}:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        member = message.mentions[0] if message.mentions else message.author
        if len(parts) >= 2 and not message.mentions and parts[1].isdigit():
            found = message.guild.get_member(int(parts[1]))
            if found is not None:
                member = found

        values = get_points(message.guild.id, member.id)
        file = build_points_image(member, values)
        await self._reply(message, file=file)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self.handle_prefix(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Points(bot))
