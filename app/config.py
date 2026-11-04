from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "hotel_management")
DB_USER = os.getenv("DB_USER", "hotel_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))
JWT_ALGORITHM = "HS256"

if not JWT_SECRET_KEY:
    # The app can still start for health checks, but login will fail closed.
    JWT_SECRET_KEY = ""
