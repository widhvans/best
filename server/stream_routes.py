# server/stream_routes.py (FULL REPLACEMENT)

import logging
import asyncio
import aiohttp
import math
from aiohttp import web
from pyrogram.errors import FileIdInvalid
from util.custom_dl import ByteStreamer
from util.file_properties import FileIdError

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
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)

async def stream_or_download(request: web.Request, disposition: str):
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        
        streamer = ByteStreamer(bot)
        file_id = await streamer.get_file_properties(message_id)
        
        file_size = file_id.file_size
        range_header = request.headers.get("Range", 0)

        headers = {
            "Content-Type": file_id.mime_type,
            "Content-Disposition": f'{disposition}; filename="{file_id.file_name}"',
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size)
        }
        
        if range_header:
            from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
            from_bytes = int(from_bytes)
            until_bytes = int(until_bytes) if until_bytes else file_size - 1
        else:
            from_bytes = 0
            until_bytes = file_size - 1

        if (until_bytes > file_size) or (from_bytes < 0):
            return web.Response(status=416) # Range Not Satisfiable

        chunk_size = 1024 * 1024 # 1MB
        offset = from_bytes - (from_bytes % chunk_size)
        first_part_cut = from_bytes - offset
        last_part_cut = (until_bytes % chunk_size) + 1
        part_count = math.ceil((until_bytes - offset) / chunk_size)
        
        body = streamer.yield_file(
            file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size
        )
        
        response = web.StreamResponse(
            status=206 if range_header else 200,
            headers=headers
        )
        await response.prepare(request)
        
        async for chunk in body:
            try:
                await response.write(chunk)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Client disconnected for message_id {message_id}. Stream stopped.")
                break # Stop sending data if client disconnects
        
        return response

    except (FileIdInvalid, FileIdError, web.HTTPNotFound):
        return web.Response(text="File not found or link has expired.", status=404)
    except Exception:
        logger.critical(f"FATAL: Unexpected error in stream/download handler", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)

@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    return await stream_or_download(request, "inline")

@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    return await stream_or_download(request, "attachment")
