from __future__ import annotations

import random
import sys
from pathlib import Path
from statistics import mean
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.scheduler import SoftSwissScheduler
from core.Swiss import SwissTournamentEngine


def _choose_winner(match, engine: SwissTournamentEngine, rng: random.Random) -> int:
    team_a = engine.get_team(match.team_a)
    team_b = engine.get_team(match.team_b)

    strength_a = max(1.0, 10.0 - team_a.seed / 3.0) + team_a.swiss_points * 0.35
    strength_b = max(1.0, 10.0 - team_b.seed / 3.0) + team_b.swiss_points * 0.35

    total = strength_a + strength_b
    return match.team_a if rng.random() < (strength_a / total) else match.team_b


def _cups_value(is_overtime: bool, rng: random.Random) -> int:
    if is_overtime:
        return rng.choice([10, 10, 11, 11, 12])
    return rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])


def simulate_one_tournament(seed: int) -> Dict[str, object]:
    rng = random.Random(seed)
    engine = SwissTournamentEngine(scheduler=SoftSwissScheduler())
    engine.new_tournament([f"Team {idx:02d}" for idx in range(1, 25)])

    relaxed_calls = 0
    safety = 0

    while engine.state.phase != "FINISHED" and safety < 1000:
        safety += 1
        if engine.state.active_matches:
            table = rng.choice(sorted(engine.state.active_matches.keys()))
            match = engine.state.active_matches[table]
            winner = _choose_winner(match, engine, rng)
            is_ot = rng.random() < 0.14
            loser_cups = _cups_value(is_ot, rng)
            engine.submit_result(table, winner, is_ot, loser_cups)
            continue

        if engine.state.phase == "SWISS" and not engine.swiss_complete():
            count = engine.fill_free_tables(relaxed=True)
            relaxed_calls += 1 if count > 0 else 0
            if count == 0:
                raise RuntimeError("Swiss konnte trotz manueller Relaxed-Suche nicht fortgesetzt werden.")
            continue

        if engine.state.phase == "KO":
            pending_stages = [slot.stage for slot in engine.state.ko_slots.values() if slot.status == "pending"]
            if pending_stages:
                stage = sorted(pending_stages, key=lambda value: {"QF": 1, "SF": 2, "FINAL": 3, "3RD": 4}.get(value, 99))[0]
                engine.schedule_ko_stage(stage)
                continue

    if safety >= 1000:
        raise RuntimeError("Simulation lief in den Safety-Cutoff.")

    swiss_matches = [match for match in engine.state.completed_matches if match.phase == "SWISS"]
    swiss_pairs = set()
    for match in swiss_matches:
        key = tuple(sorted((match.team_a, match.team_b)))
        if key in swiss_pairs:
            raise AssertionError(f"Swiss-Rematch gefunden: {key}")
        swiss_pairs.add(key)

    for team in engine.state.teams.values():
        if team.swiss_games_played != 5:
            raise AssertionError(f"{team.name} hat {team.swiss_games_played} statt 5 Swiss-Spiele.")

    if len(swiss_matches) != 60:
        raise AssertionError(f"Swiss-Matchanzahl unerwartet: {len(swiss_matches)}")

    if len(engine.state.podium) != 3:
        raise AssertionError("Podest wurde nicht korrekt ermittelt.")

    top8_names = [row["name"] for row in engine.ranking_rows()[:8]]
    podium_names = [engine.team_name(team_id) for team_id in engine.state.podium]

    return {
        "seed": seed,
        "relaxed_calls": relaxed_calls,
        "swiss_matches": len(swiss_matches),
        "completed_matches": len(engine.state.completed_matches),
        "top8": top8_names,
        "podium": podium_names,
    }


def run_batch(count: int = 12, start_seed: int = 1000) -> Dict[str, object]:
    results: List[Dict[str, object]] = []
    for offset in range(count):
        results.append(simulate_one_tournament(start_seed + offset))

    avg_relaxed = mean(result["relaxed_calls"] for result in results)
    max_relaxed = max(result["relaxed_calls"] for result in results)

    return {
        "runs": count,
        "avg_relaxed_calls": avg_relaxed,
        "max_relaxed_calls": max_relaxed,
        "sample_podium": results[0]["podium"],
        "sample_top8": results[0]["top8"],
        "results": results,
    }


if __name__ == "__main__":
    summary = run_batch()
    print("Simulation erfolgreich")
    print(f"Durchläufe: {summary['runs']}")
    print(f"Ø manuelle Relaxed-Suchen: {summary['avg_relaxed_calls']:.2f}")
    print(f"Max manuelle Relaxed-Suchen: {summary['max_relaxed_calls']}")
    print("Beispiel Top 8:")
    for idx, name in enumerate(summary["sample_top8"], start=1):
        print(f"  {idx}. {name}")
    print("Beispiel Podest:")
    for idx, name in enumerate(summary["sample_podium"], start=1):
        print(f"  {idx}. {name}")
