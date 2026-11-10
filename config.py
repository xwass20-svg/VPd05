import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()

DB_PATH = BASE_DIR / "traveler_wallet.db"

if not ACCESS_KEY:
    raise RuntimeError("Не задан ACCESS_KEY (или CURRENCY_API_KEY) в .env")
