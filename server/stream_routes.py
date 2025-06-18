import logging
import asyncio # <-- Naya import add karein
import aiohttp # <-- Naya import add karein
from aiohttp import web
from pyrogram.errors import FileIdInvalid
from util.custom_dl import ByteStreamer

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({
        "server_status": "running",
        "bot_status": f"connected_as @{bot_username}"
    })

@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    """Handles browser requests for favicon.ico to keep logs clean."""
    return web.Response(status=204)

@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        from util.render_template import render_page
        return web.Response(
            text=await render_page(bot, message_id),
            content_type='text/html'
        )
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler for message_id={request.match_info.get('message_id')}: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


# ================================================================= #
# VVVVVV YAHAN SE DONO HANDLERS MEIN BADLAV KIYA GAYA HAI VVVVVV #
# ================================================================= #

@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    """Handles video playback requests with robust error handling."""
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        return await ByteStreamer(bot).handle_stream_and_download(request, message_id, "inline")
        
    except (FileIdInvalid, FileNotFoundError, web.HTTPNotFound):
        return web.Response(text="File not found or link has expired.", status=404)
        
    except (ConnectionError, asyncio.CancelledError, aiohttp.ClientError) as e:
        logger.warning(f"Stream connection issue for message_id {request.match_info.get('message_id')}: {type(e).__name__}. Client likely disconnected.")
        return web.Response(status=200) # Gracefully close the connection
        
    except Exception:
        message_id = request.match_info.get('message_id')
        logger.critical(f"FATAL: Unexpected error in stream handler for message_id={message_id}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    """Handles direct download requests with robust error handling."""
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        return await ByteStreamer(bot).handle_stream_and_download(request, message_id, "attachment")

    except (FileIdInvalid, FileNotFoundError, web.HTTPNotFound):
        return web.Response(text="File not found or link has expired.", status=404)
        
    except (ConnectionError, asyncio.CancelledError, aiohttp.ClientError) as e:
        logger.warning(f"Download connection issue for message_id {request.match_info.get('message_id')}: {type(e).__name__}. Client likely disconnected.")
        return web.Response(status=200) # Gracefully close the connection

    except Exception:
        message_id = request.match_info.get('message_id')
        logger.critical(f"FATAL: Unexpected error in download handler for message_id={message_id}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)
