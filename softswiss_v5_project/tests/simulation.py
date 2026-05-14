from __future__ import annotations

import random
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.backup import BackupManager
from core.config import TEAM_COUNT
from core.Swiss import SwissTournamentEngine


def _choose_winner(engine: SwissTournamentEngine, match) -> int:
    team_a = engine.get_team(match.team_a)
    team_b = engine.get_team(match.team_b)
    # Slight bias toward the better ranked team so the simulation behaves realistically.
    score_a = (team_a.swiss_points, team_a.seed * -1)
    score_b = (team_b.swiss_points, team_b.seed * -1)
    if score_a == score_b:
        return match.team_a if random.random() < 0.5 else match.team_b
    return match.team_a if score_a > score_b else match.team_b


def _random_loser_cups(is_overtime: bool) -> int:
    if is_overtime:
        return random.randint(10, 12)
    return random.randint(0, 9)


def simulate_turnier(seed: int = 42) -> Dict[str, object]:
    random.seed(seed)
    engine = SwissTournamentEngine()
    names = [f"Team {i:02d}" for i in range(1, TEAM_COUNT + 1)]
    engine.new_tournament(names)

    rematches = set()
    max_games_diff = 0

    while engine.state.phase != "FINISHED":
        active = engine.active_matches()
        if not active:
            if engine.state.phase == "SWISS" and engine.swiss_complete():
                engine.start_knockout()
                continue
            # In Swiss this means the next wave should be generated automatically.
            engine.fill_free_tables(relaxed=False)
            active = engine.active_matches()
            if not active and engine.state.phase == "FINISHED":
                break
            if not active:
                raise RuntimeError(f"Kein aktives Match in Phase {engine.state.phase}")

        # Track a rough fairness metric.
        games = [team.swiss_games_played for team in engine.state.teams.values()]
        max_games_diff = max(max_games_diff, max(games) - min(games))

        match = active[0]
        pair = tuple(sorted((match.team_a, match.team_b)))
        if match.phase == "SWISS":
            if pair in rematches:
                raise RuntimeError(f"Rematch entdeckt: {pair}")
            rematches.add(pair)

        winner = _choose_winner(engine, match)
        is_overtime = random.random() < 0.18
        loser_cups = _random_loser_cups(is_overtime=is_overtime)
        engine.submit_result(match.table, winner, is_overtime=is_overtime, loser_cups_hit=loser_cups)


    completed_games = [team.swiss_games_played for team in engine.state.teams.values()]
    report = {
        "phase": engine.state.phase,
        "podium": [engine.team_name(tid) for tid in engine.state.podium],
        "top4": [engine.team_name(tid) for tid in engine.state.top4],
        "games_per_team": completed_games,
        "max_games_diff_observed": max_games_diff,
        "rematch_count": len(rematches),
        "total_swiss_matches": engine.swiss_total_matches(),
        "completed_swiss_matches": engine.swiss_finished_matches(),
        "wave_count": engine.state.wave_index,
        "autosave_roundtrip_ok": False,
    }

    # Autosave round-trip.
    tmp_path = ROOT / "data" / ".simulation_tmp"
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    try:
        bm = BackupManager(autosave_file=tmp_path / "autosave.json", snapshot_dir=tmp_path / "snapshots")
        bm.save_state(engine.state, label="simulation", snapshot=False)
        loaded = bm.load_state()
    finally:
        if tmp_path.exists():
            shutil.rmtree(tmp_path)
    report["autosave_roundtrip_ok"] = loaded.phase == engine.state.phase and len(loaded.teams) == len(engine.state.teams)
    return report


if __name__ == "__main__":
    report = simulate_turnier()
    out = Path(__file__).with_name("simulation_report.txt")
    lines = [
        f"Phase: {report['phase']}",
        f"Podium: {', '.join(report['podium'])}",
        f"Top4: {', '.join(report['top4'])}",
        f"Swiss matches: {report['completed_swiss_matches']}/{report['total_swiss_matches']}",
        f"Max games diff observed: {report['max_games_diff_observed']}",
        f"Rematch pairs tracked: {report['rematch_count']}",
        f"Waves completed: {report['wave_count']}",
        f"Autosave roundtrip ok: {report['autosave_roundtrip_ok']}",
        f"Games per team: {report['games_per_team']}",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
