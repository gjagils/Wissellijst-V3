import os
import json
from dotenv import load_dotenv

load_dotenv()

# Spotify
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_SCOPE = "playlist-read-private playlist-modify-public playlist-modify-private user-top-read"

# OpenAI
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# E-mail (optioneel)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")

# Paden
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
QUEUE_FILE = os.path.join(DATA_DIR, "volgende_blokje.txt")
HISTORY_FILE = os.path.join(DATA_DIR, "historie.txt")
SUGGESTIONS_FILE = os.path.join(DATA_DIR, "aanbevelingen.txt")
CACHE_PATH = os.path.join(DATA_DIR, ".cache")
CONFIG_FILE = os.path.join(DATA_DIR, "wissellijsten.json")


def load_wissellijsten():
    """Laad alle wissellijst-configuraties uit JSON."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"wissellijsten": []}


def save_wissellijsten(data):
    """Sla wissellijst-configuraties op als JSON."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_wissellijst(lijst_id):
    """Haal een specifieke wissellijst op via ID."""
    data = load_wissellijsten()
    for wl in data["wissellijsten"]:
        if wl["id"] == lijst_id:
            return wl
    return None


def get_history_file(lijst_id):
    """Geef het pad naar het historie-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"historie_{lijst_id}.txt")


def get_queue_file(lijst_id):
    """Geef het pad naar het wachtrij-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"wachtrij_{lijst_id}.txt")


def get_smaakprofiel_file(lijst_id):
    """Geef het pad naar het smaakprofiel-bestand voor een specifieke wissellijst."""
    return os.path.join(DATA_DIR, f"smaakprofiel_{lijst_id}.txt")
