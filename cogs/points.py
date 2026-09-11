from __future__ import annotations

import io
from pathlib import Path
from types import MethodType

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from database import connect

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Keep the cog importable if optional text shaping is unavailable.
    arabic_reshaper = None
    get_display = None

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

CATEGORY_COLUMNS = {
    "individual": "individual_points",
    "group": "group_points",
    "roulette": "roulette_points",
}

# Keep these labels exactly as requested for the visual card.
CATEGORY_LABELS = {
    "roulette": "روليت",
    "group": "جماعية",
    "individual": "فردية",
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
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _shape_text(text: str) -> str:
    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            pass
    return text


def _centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, fill) -> None:
    shaped = _shape_text(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - width) / 2 - bbox[0]
    y = box[1] + (box[3] - box[1] - height) / 2 - bbox[1]
    draw.text((x, y), shaped, font=font, fill=fill)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _paste_avatar(image: Image.Image, avatar_bytes: bytes | None) -> None:
    if not avatar_bytes:
        return
    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        avatar = ImageOps.fit(avatar, (170, 170), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        mask = _rounded_mask(170, 85)
        image.paste(avatar, (92, 170), mask)
    except (OSError, ValueError):
        return


def build_points_image(member: discord.Member, values: dict[str, int], avatar_bytes: bytes | None = None) -> discord.File:
    # بطاقة نقاط مستوحاة مباشرة من التصميم المرجعي: خلفية داكنة،
    # لوحة زرقاء كبيرة، بطاقة العضو، ثم إحصاءات الألعاب الثلاثة.
    width, height = 1536, 620
    image = Image.new("RGB", (width, height), (3, 5, 15))
    draw = ImageDraw.Draw(image)

    # Main blue background card.
    draw.rounded_rectangle((18, 108, width - 72, 595), radius=76, fill=(15, 48, 158))
    draw.rounded_rectangle((24, 114, width - 78, 589), radius=70, outline=(34, 81, 214), width=3)

    # Title pill.
    draw.rounded_rectangle((1218, 18, 1524, 146), radius=64, fill=(18, 52, 159), outline=(48, 101, 255), width=3)
    _centered_text(draw, (1218, 18, 1524, 146), "نقاطي", _font(52), (248, 249, 255))

    # Member information panel.
    draw.rounded_rectangle((58, 158, width - 130, 408), radius=42, fill=(22, 50, 138))
    draw.rounded_rectangle((58, 158, width - 130, 408), radius=42, outline=(12, 33, 104), width=4)

    # Avatar ring.
    draw.ellipse((84, 190, 278, 384), fill=(246, 248, 255))
    draw.ellipse((92, 198, 270, 376), fill=(18, 35, 92))
    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(avatar, (170, 170), method=Image.Resampling.LANCZOS)
            image.paste(avatar, (96, 202), _rounded_mask(170, 85))
        except (OSError, ValueError):
            pass

    display_name = (member.display_name or member.name).strip()
    if len(display_name) > 20:
        display_name = display_name[:19] + "…"

    # Keep the username visually in the same area as the reference.
    draw.text((330, 200), _shape_text(display_name), font=_font(58), fill=(250, 251, 255))
    draw.text((330, 290), _shape_text(f"{values['total']:,} نقطة"), font=_font(54), fill=(250, 251, 255))

    # Statistics panel.
    draw.rounded_rectangle((82, 438, width - 154, 575), radius=30, fill=(10, 30, 96))
    draw.rounded_rectangle((82, 438, width - 154, 575), radius=30, outline=(23, 58, 159), width=2)

    # RTL order matching the reference: فردية | جماعية | روليت.
    columns = [
        ("individual", 82, 535),
        ("group", 535, 988),
        ("roulette", 988, 1382),
    ]
    for index, (key, left, right) in enumerate(columns):
        if index:
            draw.line((left, 460, left, 554), fill=(33, 68, 164), width=2)
        _centered_text(
            draw,
            (left + 12, 454, right - 12, 510),
            f"{CATEGORY_LABELS[key]}: {values[key]:,}",
            _font(38),
            (250, 251, 255),
        )
        subtitle = {
            "individual": "نقاط الألعاب الفردية",
            "group": "نقاط الألعاب الجماعية",
            "roulette": "نقاط الروليت",
        }[key]
        _centered_text(draw, (left + 12, 512, right - 12, 560), subtitle, _font(25), (223, 229, 255))

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="nawaf-points.png")

class Points(commands.Cog):
    """Categorized game points and the image-based `-نقاطي` card."""

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

    async def _reply(self, message: discord.Message, **kwargs):
        kwargs.setdefault("mention_author", False)
        return await message.reply(**kwargs)

    async def handle_prefix(self, message: discord.Message) -> bool:
        content = message.content.strip()
        parts = content.split()
        if not parts or parts[0] != "-نقاطي":
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        member = message.mentions[0] if message.mentions else message.author
        if len(parts) >= 2 and not message.mentions and parts[1].isdigit():
            found = message.guild.get_member(int(parts[1]))
            if found is not None:
                member = found

        values = get_points(message.guild.id, member.id)
        avatar_bytes = None
        try:
            avatar_bytes = await member.display_avatar.read()
        except (discord.HTTPException, discord.NotFound):
            avatar_bytes = None

        file = build_points_image(member, values, avatar_bytes)
        await self._reply(message, file=file)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self.handle_prefix(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Points(bot))
