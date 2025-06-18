import logging
import math
import mimetypes
import time
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from pyrogram.errors import FileIdInvalid

# --- Get the logger instance from the main bot ---
logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({
        "server_status": "running",
        "bot_status": f"connected_as @{bot_username}"
    })

@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    """Renders the HTML watch page for a given file."""
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        
        # We need the render_page utility from the util package
        from util.render_template import render_page
        
        # Render the watch page using the file's message ID in the stream channel
        return web.Response(
            text=await render_page(bot, message_id),
            content_type='text/html'
        )
    except FileIdInvalid:
        return web.Response(text="File not found or link has expired.", status=404)
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler for message_id={message_id}: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    """Handles the actual file streaming with byte range requests."""
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        
        # We need the ByteStreamer utility from the util package
        from util.custom_dl import ByteStreamer
        
        return await ByteStreamer(bot).stream_media(request, message_id)
    except FileIdInvalid:
        return web.Response(text="File not found or link has expired.", status=404)
    except (AttributeError, BadStatusLine, ConnectionResetError) as e:
        logger.warning(f"Streaming connection error for message_id={message_id}: {e}")
        return web.Response(text="Streaming temporarily unavailable, please try again later.", status=503)
    except Exception as e:
        logger.critical(f"Unexpected error in stream handler for message_id={message_id}: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)
