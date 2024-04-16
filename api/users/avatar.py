import colorsys
import hashlib
import os

import aiofiles
import httpx
from fastapi import Request, Response

from ayon_server.api.dependencies import CurrentUser, UserName
from ayon_server.config import ayonconfig
from ayon_server.exceptions import NotFoundException
from ayon_server.helpers.thumbnails import process_thumbnail
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis

from .router import router

REDIS_NS = "user.avatar"


def generate_color(name: str, saturation: float = 0.25, lightness: float = 0.38) -> str:
    """
    Generates a deterministic color based on the hue
    derived from hashing the input string.
    Keeps saturation and lightness constant.

    Parameters:
    - name: The input string to hash for color generation.
    - saturation: The saturation level of the color (0 to 1).
    - lightness: The lightness level of the color (0 to 1).

    Returns:
    - A hex color code as a string.
    """

    hash_bytes = hashlib.sha256(name.encode("utf-8")).digest()
    hue = int(hash_bytes[0]) * 360 // 256
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, lightness, saturation)
    color_code = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    return color_code


def create_initials_svg(
    name: str,
    full_name: str = "",
    width: int = 100,
    height: int = 100,
    text_color: str = "white",
) -> str:
    _used_name = full_name or name
    initials = "".join([n[0] for n in _used_name.split()])
    initials = initials.upper()

    bg_color = generate_color(f"{name}{full_name}")

    svg_template = f"""
    <svg width="{width}px" height="{height}px" xmlns="http://www.w3.org/2000/svg">
      <rect width="100%" height="100%" fill="{bg_color}"/>
      <text
        x="50%"
        y="50%"
        dominant-baseline="middle"
        text-anchor="middle"
        fill="{text_color}"
        font-size="{height // 2}px"
        font-family="Arial"
      >
        {initials}
      </text>
    </svg>
    """

    return svg_template.strip()


async def load_avatar_file(user_name: str) -> bytes:
    for ext in ["jpeg", "png", "jpg", "svg"]:
        avatar_path = os.path.join(ayonconfig.avatar_dir, f"{user_name}.{ext}")
        if os.path.exists(avatar_path):
            async with aiofiles.open(avatar_path, "rb") as f:
                return await f.read()
    raise FileNotFoundError


async def obtain_avatar(user_name: str) -> bytes:
    # skip loading the entire user object.
    # we just need one single attribute

    res = await Postgres.fetch(
        """
        SELECT
            attrib->>'avatarUrl' as url,
            attrib->>'fullName' as full_name
        FROM users WHERE name = $1
        """,
        user_name,
    )

    if not res:
        raise NotFoundException("User not found")

    if res[0]["url"]:
        avatar_url = res[0]["url"]
        async with httpx.AsyncClient() as client:
            response = await client.get(avatar_url)
            avatar_bytes = response.content
        avatar_bytes = await process_thumbnail(avatar_bytes)
    else:
        try:
            avatar_bytes = await load_avatar_file(user_name)
        except FileNotFoundError:
            full_name = res[0]["full_name"] or ""
            avatar_bytes = create_initials_svg(user_name, full_name).encode()

    await Redis.set(REDIS_NS, user_name, avatar_bytes)
    return avatar_bytes


@router.get("/{user_name}/avatar")
async def get_avatar(user_name: UserName, _: CurrentUser) -> Response:
    avatar_bytes = await Redis.get(REDIS_NS, user_name)

    if not avatar_bytes:
        avatar_bytes = await obtain_avatar(user_name)

    if avatar_bytes[0:4] == b"\x89PNG":
        return Response(content=avatar_bytes, media_type="image/png")
    elif avatar_bytes[0:2] == b"\xff\xd8":
        return Response(content=avatar_bytes, media_type="image/jpeg")
    elif avatar_bytes[0:4] == b"<svg":
        return Response(content=avatar_bytes, media_type="image/svg+xml")

    raise NotFoundException("Invalid avatar format")


@router.put("/{user_name}/avatar")
async def upload_avatar(user: CurrentUser, request: Request, user_name: UserName):
    mime_to_ext = {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/svg+xml": "svg",
    }

    if user.name != user_name and not user.is_admin:
        raise NotFoundException("Invalid avatar format")

    mime = request.headers.get("Content-Type")
    if mime not in mime_to_ext:
        raise NotFoundException("Invalid avatar format")
    avatar_bytes = await request.body()

    if not os.path.isdir(ayonconfig.avatar_dir):
        os.makedirs(ayonconfig.avatar_dir)

    avatar_path = os.path.join(
        ayonconfig.avatar_dir, f"{user_name}.{mime_to_ext[mime]}"
    )
    async with aiofiles.open(avatar_path, "wb") as f:
        await f.write(avatar_bytes)

    await Redis.delete(REDIS_NS, user_name)