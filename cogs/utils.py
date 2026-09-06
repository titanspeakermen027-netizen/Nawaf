import discord

async def safe_send(member: discord.Member, content=None, **kwargs):
    try:
        return await member.send(content, **kwargs)
    except (discord.Forbidden, discord.HTTPException):
        return None
