# server/stream_routes.py (FULL REPLACEMENT)

import logging
import asyncio
import math
from aiohttp import web
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
    message_id_str = request.match_info.get("message_id")
    try:
        message_id = int(message_id_str)
        bot = request.app['bot']
        streamer = ByteStreamer(bot)

        # File ki properties (size, name, etc.) prapt karein
        file_id = await streamer.get_file_properties(message_id)
        
        file_size = file_id.file_size
        range_header = request.headers.get("Range", 0)

        headers = {
            "Content-Type": file_id.mime_type,
            "Content-Disposition": f'{disposition}; filename="{file_id.file_name}"',
            "Accept-Ranges": "bytes",
        }
        
        if range_header:
            from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
            from_bytes = int(from_bytes)
            until_bytes = int(until_bytes) if until_bytes else file_size - 1
            
            if (from_bytes > file_size) or (until_bytes >= file_size):
                return web.Response(status=416) # Range Not Satisfiable
            
            # Yeh hai "Translator" ka logic
            chunk_size = 1024 * 1024  # 1MB block size
            offset = from_bytes - (from_bytes % chunk_size)
            first_part_cut = from_bytes - offset
            last_part_cut = (until_bytes % chunk_size) + 1
            part_count = math.ceil((until_bytes - offset + 1) / chunk_size)
            
            body = streamer.yield_file(
                file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size
            )
            
            headers["Content-Length"] = str(until_bytes - from_bytes + 1)
            headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"
            status = 206 # Partial Content
        else:
            # Poori file stream karein
            body = streamer.yield_file(file_id, 0, 0, file_size, 1, file_size)
            headers["Content-Length"] = str(file_size)
            status = 200

        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        
        async for chunk in body:
            try:
                await response.write(chunk)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Client disconnected for message {message_id}. Stopping stream.")
                break
        
        return response

    except (FileIdError, ValueError) as e:
        logger.warning(f"File ID or configuration error for stream request: {e}")
        return web.Response(status=404, text=f"File not found or link expired: {e}")
    except Exception:
        logger.critical(f"FATAL: Unexpected error in stream/download handler for message_id={message_id_str}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    return await stream_or_download(request, "inline")


@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    return await stream_or_download(request, "attachment")
