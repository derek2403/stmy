import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
VERIFY_TOPIC_ID = int(os.getenv("VERIFY_TOPIC_ID", "0"))
INTROS_TOPIC_ID = int(os.getenv("INTROS_TOPIC_ID", "0"))
ADMIN_TOPIC_ID = int(os.getenv("ADMIN_TOPIC_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
PIC_HANDLES = os.getenv("PIC_HANDLES", "@abc123, @xyz456")
