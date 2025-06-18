import logging
import asyncio
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyromod import Client
from aiohttp import web
from config import Config
from database.db import (
    get_user, save_file_data, get_owner_db_channel, get_stream_channel
)
from utils.helpers import create_post, clean_filename, notify_and_remove_invalid_channel, get_title_key

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyromod").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# Web Server Redirect Handler (for /get/... links)
async def handle_redirect(request):
    file_unique_id = request.match_info.get('file_unique_id', None)
    if not file_unique_id: return web.Response(text="File ID missing.", status=400)
    try:
        with open(Config.BOT_USERNAME_FILE, 'r') as f: bot_username = f.read().strip().replace("@", "")
    except FileNotFoundError:
        logger.error(f"FATAL: Bot username file not found at {Config.BOT_USERNAME_FILE}")
        return web.Response(text="Bot configuration error.", status=500)
    return web.HTTPFound(f"https://t.me/{bot_username}?start=get_{file_unique_id}")


class Bot(Client):
    def __init__(self):
        super().__init__("FinalStorageBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN, plugins=dict(root="handlers"))
        self.me = None
        self.web_app = None
        self.web_runner = None
        
        # --- MODIFIED: Add attributes from both bots ---
        self.owner_db_channel_id = None
        self.stream_channel_id = None
        self.file_queue = asyncio.Queue()
        self.open_batches = {}
        self.notification_flags = {}
        self.notification_timers = {}
        # --- END MODIFIED ---
        
        # Add config values to instance for easy access in web handlers
        self.vps_ip = Config.VPS_IP
        self.vps_port = Config.VPS_PORT

    def _reset_notification_flag(self, channel_id):
        self.notification_flags[channel_id] = False
        logger.info(f"Notification flag reset for channel {channel_id}.")

    async def _finalize_batch(self, user_id, batch_key):
        # ... (This function remains largely the same, but it will now create posts with stream buttons thanks to the helpers.py change) ...
        notification_messages = []
        try:
            if user_id not in self.open_batches or batch_key not in self.open_batches[user_id]: return
            batch_data = self.open_batches[user_id].pop(batch_key)
            messages = batch_data['messages']
            if not messages: return
            
            first_filename = getattr(messages[0], messages[0].media.value).file_name
            batch_display_title, _ = clean_filename(first_filename)

            user = await get_user(user_id)
            post_channels = user.get('post_channels', [])
            if not user or not post_channels: return

            valid_post_channels = []
            for channel_id in post_channels:
                if await notify_and_remove_invalid_channel(self, user_id, channel_id, "Post"):
                    valid_post_channels.append(channel_id)
            
            if not valid_post_channels:
                logger.warning(f"User {user_id} has no valid post channels for batch '{batch_display_title}'.")
                await self.send_message(user_id, f"⚠️ Could not post the batch for **{batch_display_title}** because you have no valid Post Channels configured.")
                return

            for channel_id in valid_post_channels:
                if not self.notification_flags.get(channel_id):
                    self.notification_flags[channel_id] = True
                    logger.info(f"Sending 'coming soon' notification to {channel_id}.")
                    msg = await self.send_with_protection(self.send_message, channel_id, "<i>✨ New releases are coming...</i>", parse_mode=ParseMode.HTML)
                    if msg: notification_messages.append(msg)
                    if self.notification_timers.get(channel_id): self.notification_timers[channel_id].cancel()
                    self.notification_timers[channel_id] = asyncio.get_event_loop().call_later(60, self._reset_notification_flag, channel_id)

            posts_to_send = await create_post(self, user_id, messages)
            
            for channel_id in valid_post_channels:
                for post in posts_to_send:
                    poster, caption, footer = post
                    if poster: await self.send_with_protection(self.send_photo, channel_id, poster, caption=caption, reply_markup=footer)
                    else: await self.send_with_protection(self.send_message, channel_id, caption, reply_markup=footer, disable_web_page_preview=True)
                    await asyncio.sleep(2)
        except Exception as e: logger.exception(f"Error finalizing batch {batch_key}: {e}")
        finally:
            for sent_msg in notification_messages:
                await self.send_with_protection(sent_msg.delete)
            if user_id in self.open_batches and not self.open_batches[user_id]:
                del self.open_batches[user_id]

    # --- MODIFIED: file_processor_worker now copies to stream channel and saves stream_id ---
    async def file_processor_worker(self):
        logger.info("File Processor Worker started.")
        while True:
            try:
                message, user_id = await self.file_queue.get()
                
                # Ensure both required channels are set by the admin
                if not self.owner_db_channel_id: self.owner_db_channel_id = await get_owner_db_channel()
                if not self.stream_channel_id: self.stream_channel_id = await get_stream_channel()

                if not self.owner_db_channel_id:
                    logger.error("Owner DB Channel not set. File processing skipped."); continue
                if not self.stream_channel_id:
                    logger.error("Stream Channel not set. File processing skipped."); continue
                
                # 1. Copy to Owner DB for backup/get_links
                copied_message = await self.send_with_protection(message.copy, self.owner_db_channel_id)
                if not copied_message: continue

                # 2. Copy to Stream Channel for streaming
                stream_message = await self.send_with_protection(message.copy, self.stream_channel_id)
                if not stream_message: continue

                # 3. Save both message IDs to the database
                await save_file_data(user_id, message, copied_message, stream_message)
                
                # The rest of the batching logic remains the same
                filename = getattr(copied_message, copied_message.media.value).file_name
                title_key = get_title_key(filename)
                if not title_key:
                    logger.warning(f"Could not generate a title key for filename: {filename}"); continue

                self.open_batches.setdefault(user_id, {})
                loop = asyncio.get_event_loop()

                if title_key in self.open_batches[user_id]:
                    batch = self.open_batches[user_id][title_key]
                    batch['messages'].append(copied_message)
                    if batch.get('timer'): batch['timer'].cancel()
                    batch['timer'] = loop.call_later(7, lambda key=title_key: asyncio.create_task(self._finalize_batch(user_id, key)))
                    logger.info(f"Added to batch with key '{title_key}'")
                else:
                    self.open_batches[user_id][title_key] = {
                        'messages': [copied_message],
                        'timer': loop.call_later(7, lambda key=title_key: asyncio.create_task(self._finalize_batch(user_id, key)))
                    }
                    logger.info(f"Created new batch with key '{title_key}'")
            except Exception as e:
                logger.exception(f"CRITICAL Error in file_processor_worker: {e}")
            finally:
                self.file_queue.task_done()
    # --- END MODIFIED ---
    
    async def send_with_protection(self, coro, *args, **kwargs):
        while True:
            try:
                return await coro(*args, **kwargs)
            except FloodWait as e:
                logger.warning(f"FloodWait of {e.value}s detected. Sleeping..."); await asyncio.sleep(e.value + 2)
            except Exception as e:
                logger.error(f"SEND_PROTECTION: An error occurred: {e}"); raise

    # --- MODIFIED: start_web_server now includes all routes ---
    async def start_web_server(self):
        """Starts a single web server for both redirect and stream routes."""
        from server.stream_routes import routes as stream_routes
        
        self.web_app = web.Application()
        self.web_app['bot'] = self # Make bot instance available to handlers
        
        # Add routes from both functionalities
        self.web_app.router.add_get("/get/{file_unique_id}", handle_redirect)
        self.web_app.add_routes(stream_routes)
        
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, self.vps_ip, self.vps_port)
        await site.start()
        logger.info(f"Web server started at http://{self.vps_ip}:{self.vps_port}")
    # --- END MODIFIED ---

    async def start(self):
        await super().start()
        self.me = await self.get_me()
        
        # Load channel IDs from DB
        self.owner_db_channel_id = await get_owner_db_channel()
        self.stream_channel_id = await get_stream_channel()
        
        if self.owner_db_channel_id: logger.info(f"Loaded Owner DB ID [{self.owner_db_channel_id}]")
        else: logger.warning("Owner DB ID not set. Use 'Set Owner DB' as admin.")
        
        if self.stream_channel_id: logger.info(f"Loaded Stream Channel ID [{self.stream_channel_id}]")
        else: logger.warning("Stream Channel ID not set. Use 'Set Stream Channel' as admin.")
            
        try:
            with open(Config.BOT_USERNAME_FILE, 'w') as f: f.write(f"@{self.me.username}")
            logger.info(f"Updated bot username to @{self.me.username}")
        except Exception as e: logger.error(f"Could not write to {Config.BOT_USERNAME_FILE}: {e}")
        
        asyncio.create_task(self.file_processor_worker())
        
        await self.start_web_server()
        
        logger.info(f"Bot @{self.me.username} started successfully.")

    async def stop(self, *args):
        logger.info("Stopping bot...")
        if self.web_runner: await self.web_runner.cleanup()
        await super().stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
