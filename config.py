import os

class Config:
    # Your API details from my.telegram.org
    API_ID = int(os.environ.get("API_ID", "10389378"))
    API_HASH = os.environ.get("API_HASH", "cdd5c820cb6abeecaef38e2bb8db4860")

    # Your Bot Token
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7513275210:AAGgRzfVk1_nEaekouSY1kWmp0eqD3pNw-I")

    # Your Admin User ID
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "1938030055"))

    # Your MongoDB Connection String
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://soniji:chaloji@cluster0.i5zy74f.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "telegram_bot_v2")
    
    # --- TMDB API Key (Optional, for posters) ---
    TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "5a318417c7f4a722afd9d71df548877b")
    
    # --- Your VPS IP Address and Port for the Web Server ---
    VPS_IP = os.environ.get("VPS_IP", "65.21.183.36")
    
    # Port for the web server (both redirect and streaming)
    VPS_PORT = int(os.environ.get("VPS_PORT", 7071))
    
    # The name of the file that stores your bot's username (for the redirector)
    BOT_USERNAME_FILE = "bot_username.txt"
    
    # ================================================================= #
    # VVVVVV YAHAN PAR NAYA TUTORIAL LINK ADD KIYA GAYA HAI VVVVVV #
    # ================================================================= #
    # Yahan apna tutorial video ya channel ka link daalein
    TUTORIAL_URL = os.environ.get("TUTORIAL_URL", "https://t.me/tutorial_really/2")
