from __future__ import annotations

from pathlib import Path

APP_TITLE = "Beerpong Turnier - SoftSwiss Desktop v6"
TEAM_COUNT = 24
TABLE_COUNT = 5
SWISS_PRIMARY_TABLE_COUNT = 4
B_GROUP_TABLE_NUMBER = 5
B_GROUP_TABLE_LABEL = "Tisch 5"
B_GROUP_TEAM_COUNT = 4
SWISS_WAVES_TOTAL = 5
SWISS_MATCHES_PER_WAVE = 12
SWISS_GAMES_PER_TEAM = 5
SWISS_WAVE_PREPARE_RATIO = 0.70
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
    # Hintergrund (sehr hell, reflektionsarm)
    "bg": "#F6F8F3",

    # Panels (neutral + leichter Bier-Ton)
    "panel": "#EEF2EA",
    "surface": "#F9FBF7",
    "surface_alt": "#F1F5EE",

    # Karten / Listen
    "card": "#FFFFFF",
    "row": "#FFFFFF",
    "row_alt": "#F7FAF5",
    "shadow": "#DDE5DA",

    # Hauptfarbe (Beerpong Cup Rot)
    "accent": "#C84D32",

    # Hover / Buttons
    "accent_soft": "#FFF0E8",
    "accent_warm": "#F2B35D",

    # Dunkler Rotton für wichtige Elemente
    "accent_dark": "#84301F",

    # Text (sehr dunkles Grün statt Schwarz → angenehmer draußen)
    "text": "#17251F",
    "text_soft": "#33443C",

    # Sekundärer Text
    "muted": "#69756D",

    # Erfolg / Sieg
    "ok": "#287D68",
    "ok_soft": "#E8F5EF",

    # Warnung / OT / kritisch
    "warn": "#D7A642",
    "warn_soft": "#FFF6DB",

    # Sehr dezente Linien und Trennungen
    "line": "#E1E8DE",
    "line_soft": "#EDF2EA",

    # Highlight (aktive Spiele / Fokus)
    "highlight": "#E36A3D",
    "highlight_soft": "#FFF3EC",

    # Top 8: erkennbar, aber ohne schwere Flaechen
    "top8": "#F1FAF4",

    # Top 8 stark (Cutline)
    "top8_strong": "#E4F5EA",
    "top8_strip": "#45A875",
    "top8_badge": "#DDF2E6",
}
