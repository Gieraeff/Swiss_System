SoftSwiss Desktop v5
====================

Start:
  python main.py

Struktur:
  main.py               - Einstiegspunkt
  core/config.py        - Konstanten und Farben
  core/models.py        - Datenklassen
  core/scheduler.py     - Paarungslogik für Swiss-Wellen
  core/Swiss.py         - Turnier-Engine, Swiss, Ranking, KO
  core/backup.py        - Autosave / Snapshot
  ui/GUI.py             - Admin- und Spieler-GUI
  tests/simulation.py   - Offline-Simulation und Backup-Test

Hinweis:
  Die GUI arbeitet event-getrieben: sie aktualisiert sich nur bei echten Zustandsänderungen. Das verhindert Flackern und unnötiges Neuzeichnen.
