from pathlib import Path

APP_TITLE = "Beerpong Turnier - SoftSwiss Desktop v3"
TEAM_COUNT = 24
TABLE_COUNT = 4
SWISS_GAMES_PER_TEAM = 5
TOP_CUT = 8
PREVIEW_MATCHES = 2

DEFAULT_SWISS_POINT_CAP = 2
DEFAULT_SWISS_GAME_CAP = 1
RELAXED_POINT_CAPS = [2, 3, 4, 5, 99]
RELAXED_GAME_CAPS = [1, 2, 3, 99]

AUTO_REFRESH_MS = 2000
PLAYER_REFRESH_MS = 2000
LOG_LIMIT = 400

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
AUTOSAVE_FILE = DATA_DIR / "autosave.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
MAX_SNAPSHOTS = 20

PHASE_LABELS = {
    "SETUP": "Setup",
    "SWISS": "Swiss-Phase",
    "KO": "KO-Phase",
    "FINISHED": "Beendet",
}

PUBLIC_THEME = {
    "bg": "#f7fbf9",
    "panel": "#eef7f3",
    "card": "#ffffff",
    "accent": "#2bbf9b",
    "accent_soft": "#6fd6bf",
    "text": "#11312b",
    "muted": "#5f756f",
    "ok": "#2a9d6f",
    "warn": "#c7902f",
}
