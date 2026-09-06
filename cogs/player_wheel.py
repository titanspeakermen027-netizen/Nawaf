from __future__ import annotations

import io
import math
from typing import Mapping

import discord
from PIL import Image, ImageDraw, ImageFont

IMAGE_SIZE = 1200
CENTER = IMAGE_SIZE // 2
RADIUS = 500


def _font(size: int, bold: bool = False):
    paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(paths, size)
    except OSError:
        return ImageFont.load_default()


def _short_name(name: str, max_chars: int = 13) -> str:
    clean = " ".join(name.split())
    return clean if len(clean) <= max_chars else clean[: max_chars - 1] + "…"


def _paste_avatar(canvas: Image.Image, avatar_bytes: bytes, center: tuple[int, int], size: int, ring: bool = False):
    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if ring:
        ring_size = size + 12
        ring_mask = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
        ImageDraw.Draw(ring_mask).ellipse((0, 0, ring_size - 1, ring_size - 1), outline=(255, 215, 64, 255), width=10)
        canvas.alpha_composite(ring_mask, (center[0] - ring_size // 2, center[1] - ring_size // 2))
    canvas.paste(avatar, (center[0] - size // 2, center[1] - size // 2), mask)


def render_player_wheel(
    members: list[discord.Member],
    selected_id: int,
    avatar_bytes: Mapping[int, bytes],
    round_number: int,
) -> bytes:
    """Render an original wheel with every player's avatar + display name.

    The selected segment is rotated so its center points to the fixed pointer at 12 o'clock.
    """
    count = max(1, len(members))
    selected_index = next((i for i, member in enumerate(members) if member.id == selected_id), 0)
    step = 360.0 / count
    selected_center = -90.0
    start_angle = selected_center - step / 2.0 - selected_index * step

    image = Image.new("RGBA", (IMAGE_SIZE, IMAGE_SIZE), (16, 18, 24, 255))
    draw = ImageDraw.Draw(image)

    # Outer board and wheel.
    draw.ellipse(
        (CENTER - RADIUS - 24, CENTER - RADIUS - 24, CENTER + RADIUS + 24, CENTER + RADIUS + 24),
        fill=(8, 10, 14, 255),
    )
    draw.ellipse(
        (CENTER - RADIUS, CENTER - RADIUS, CENTER + RADIUS, CENTER + RADIUS),
        fill=(42, 45, 56, 255),
        outline=(255, 215, 64, 255),
        width=8,
    )

    # Keep a clean, high-contrast alternating palette without relying on external assets.
    segment_fills = ((173, 47, 47, 255), (45, 49, 63, 255), (52, 105, 70, 255), (54, 76, 128, 255))

    for index, member in enumerate(members):
        seg_start = start_angle + index * step
        seg_end = seg_start + step
        bbox = (CENTER - RADIUS, CENTER - RADIUS, CENTER + RADIUS, CENTER + RADIUS)
        fill = (255, 185, 45, 255) if member.id == selected_id else segment_fills[index % len(segment_fills)]
        draw.pieslice(bbox, start=seg_start, end=seg_end, fill=fill, outline=(230, 232, 238, 255), width=3)

        mid = math.radians(seg_start + step / 2.0)
        avatar_radius = int(RADIUS * 0.58)
        ax = int(CENTER + math.cos(mid) * avatar_radius)
        ay = int(CENTER + math.sin(mid) * avatar_radius)
        avatar = avatar_bytes.get(member.id)
        if avatar:
            _paste_avatar(image, avatar, (ax, ay), 86 if count <= 10 else 70, ring=member.id == selected_id)

        name = _short_name(member.display_name)
        label_radius = int(RADIUS * 0.88)
        lx = int(CENTER + math.cos(mid) * label_radius)
        ly = int(CENTER + math.sin(mid) * label_radius)
        font = _font(25 if count <= 10 else 20, bold=member.id == selected_id)
        box = draw.textbbox((0, 0), name, font=font)
        tw = box[2] - box[0]
        th = box[3] - box[1]
        # Small readable background behind the name.
        pad_x, pad_y = 9, 5
        draw.rounded_rectangle(
            (lx - tw / 2 - pad_x, ly - th / 2 - pad_y, lx + tw / 2 + pad_x, ly + th / 2 + pad_y),
            radius=10,
            fill=(0, 0, 0, 170),
        )
        draw.text((lx - tw / 2, ly - th / 2 - box[1]), name, font=font, fill=(255, 255, 255, 255))

    # Center hub.
    draw.ellipse((CENTER - 70, CENTER - 70, CENTER + 70, CENTER + 70), fill=(15, 17, 22, 255), outline=(255, 215, 64, 255), width=8)
    round_font = _font(24, bold=True)
    round_text = f"ROUND {round_number}"
    rbox = draw.textbbox((0, 0), round_text, font=round_font)
    draw.text((CENTER - (rbox[2] - rbox[0]) / 2, CENTER - 13), round_text, font=round_font, fill=(255, 255, 255, 255))

    # Fixed pointer at 12 o'clock.
    pointer = [
        (CENTER, CENTER - RADIUS - 4),
        (CENTER - 30, CENTER - RADIUS + 54),
        (CENTER + 30, CENTER - RADIUS + 54),
    ]
    draw.polygon(pointer, fill=(255, 215, 64, 255), outline=(10, 10, 12, 255))
    draw.ellipse((CENTER - 10, CENTER - RADIUS + 42, CENTER + 10, CENTER - RADIUS + 62), fill=(255, 255, 255, 255))

    # Title.
    title_font = _font(40, bold=True)
    title = "NAWAF • WHEEL OF PLAYERS"
    tbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text((CENTER - (tbox[2] - tbox[0]) / 2, 34), title, font=title_font, fill=(255, 255, 255, 255))

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output.getvalue()


async def collect_avatar_bytes(members: list[discord.Member]) -> dict[int, bytes]:
    avatars: dict[int, bytes] = {}
    for member in members:
        try:
            avatars[member.id] = await (member.display_avatar.replace(size=128).read())
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            continue
    return avatars
