import aiohttp
import logging
from database.db import get_user

logger = logging.getLogger(__name__)

async def get_shortlink(link_to_shorten, user_id):
    """Shortens the provided link using the user's settings."""
    user = await get_user(user_id)
    if not user or not user.get('shortener_enabled') or not user.get('shortener_url'):
        # If shortener is disabled or not set, return the original link
        return link_to_shorten

    URL = user['shortener_url'].strip()
    API = user['shortener_api'].strip()

    try:
        url = f'https://{URL}/api'
        params = {'api': API, 'url': link_to_shorten}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                data = await response.json(content_type=None)
                if data.get("status") == "success" and data.get("shortenedUrl"):
                    return data["shortenedUrl"]
                else:
                    logger.error(f"Shortener error for user {user_id}: {data.get('message', 'Unknown error')}")
                    # On failure, return the original un-shortened link
                    return link_to_shorten
    except Exception as e:
        logger.error(f"HTTP Error during shortening for user {user_id}: {e}")
        return link_to_shorten
