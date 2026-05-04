from __future__ import annotations

from pathlib import Path

APP_TITLE = "Beerpong Turnier - SoftSwiss Desktop v6"
TEAM_COUNT = 24
TABLE_COUNT = 4
SWISS_WAVES_TOTAL = 5
SWISS_MATCHES_PER_WAVE = 12
SWISS_GAMES_PER_TEAM = 5
TOP_CUT = 8
PREVIEW_MATCHES = 2

DEFAULT_POINT_CAP = 2
DEFAULT_GAME_CAP = 1
RELAXED_POINT_CAPS = [2, 3, 4, 5, 99]
RELAXED_GAME_CAPS = [1, 2, 99]

AUTO_REFRESH_MS = 1200
PLAYER_REFRESH_MS = 1200
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
    "bg": "#f4fff9",
    "panel": "#e61e1e",
    "card": "#ffffff",
    "accent": "#be1111",
    "accent_soft": "#d40606",
    "accent_dark": "#944747",
    "text": "#14322d",
    "muted": "#5f7671",
    "ok": "#2d9d52",
    "warn": "#c88f2f",
    "line": "#cdebdc",
    "highlight": "#920707",
    "top8": "#9DD5DA",
    "top8_strong": "#cff6e4",
}
