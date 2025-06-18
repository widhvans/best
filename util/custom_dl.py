# util/custom_dl.py (FULL REPLACEMENT)

import logging
from pyrogram import Client, raw
from util.file_properties import get_file_properties, FileIdError

logger = logging.getLogger(__name__)

class ByteStreamer:
    def __init__(self, client: Client):
        self.client: Client = client

    async def get_file_properties(self, message_id):
        return await get_file_properties(self.client, message_id)

    @staticmethod
    def get_location(file_id):
        return raw.types.InputDocumentFileLocation(
            id=file_id.media_id,
            access_hash=file_id.access_hash,
            file_reference=file_id.file_reference,
            thumb_size=""
        )

    async def yield_file(self, file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size):
        location = self.get_location(file_id)
        current_part = 1

        while current_part <= part_count:
            try:
                # Hum seedhe client.invoke ka istemal karenge jo DC routing ko aaram se handle karta hai
                chunk = await self.client.invoke(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=chunk_size
                    )
                )
                
                if isinstance(chunk, raw.types.upload.File):
                    # Pehle aur aakhri chunk ko kaat kar bhejein
                    if current_part == 1 and part_count > 1:
                        yield chunk.bytes[first_part_cut:]
                    elif current_part == part_count and part_count > 1:
                        yield chunk.bytes[:last_part_cut]
                    else:
                        yield chunk.bytes
                    
                    offset += chunk_size
                    current_part += 1
                else:
                    logger.warning(f"Did not receive a file chunk, stopping. Type: {type(chunk)}")
                    break
            except Exception as e:
                logger.error(f"Error fetching chunk at offset {offset}: {e}", exc_info=True)
                break
