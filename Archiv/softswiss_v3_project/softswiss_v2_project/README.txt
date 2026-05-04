SoftSwiss Desktop v2
====================

Start:
  python main.py

Ordnerstruktur:
  main.py                -> MainApp, Startpunkt
  core/
    config.py            -> Konstanten und Pfade
    models.py            -> Domänenmodelle (Team, Match, KOSlot, TournamentState)
    scheduler.py         -> SoftSwiss-Auslosung / Pairing
    Swiss.py             -> Turnier-Engine, Ranking, KO-Phase, Ergebnisverarbeitung
    backup.py            -> Autosave und Snapshot-Verwaltung
  ui/
    GUI.py               -> Admin-GUI + Spieleranzeige
  tests/
    simulation.py        -> automatische Turniersimulation / Integritätstest
  data/
    autosave.json        -> wird beim Betrieb automatisch erzeugt
    snapshots/           -> optionale Snapshots

Regeln in dieser Version:
  - 24 Teams, 4 Tische
  - SoftSwiss mit 5 Swiss-Spielen pro Team
  - Keine Rematches in Swiss
  - Ranking: Punkte > Cups-Differenz > Buchholz > Start-Seed
  - OT: Sieger erhält nur die Differenz (13 - Verliererbecher), Verlierer 0 Cups
  - KO: fixes 1v8, 2v7, 3v6, 4v5
  - Spiel um Platz 3 enthalten

Wichtige Hinweise:
  - Die "Next-Up"-Vorschau ist eine Live-Prognose und kann sich nach jedem Ergebnis ändern.
  - Regulär wird nur mit strengen SoftSwiss-Regeln aufgefüllt.
  - Wenn kein Match gefunden wird, kann die Turnierleitung manuell "Erweiterte Suche" auslösen.

Simulation:
  python tests/simulation.py
