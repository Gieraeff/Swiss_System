from __future__ import annotations

import time
from math import ceil
from typing import Callable, Dict, List, Optional

from core.config import (
    B_GROUP_TABLE_NUMBER,
    DEFAULT_GAME_CAP,
    DEFAULT_POINT_CAP,
    LOG_LIMIT,
    B_GROUP_TABLE_LABEL,
    B_GROUP_TEAM_COUNT,
    PHASE_LABELS,
    PREVIEW_MATCHES,
    SWISS_GAMES_PER_TEAM,
    SWISS_MATCHES_PER_WAVE,
    SWISS_PRIMARY_TABLE_COUNT,
    SWISS_WAVE_PREPARE_RATIO,
    SWISS_WAVES_TOTAL,
    TABLE_COUNT,
    TEAM_COUNT,
    TOP_CUT,
)
from core.models import BGroupMatch, BGroupState, BGroupTeam, KOSlot, Match, Team, TournamentState, WavePlan
from core.scheduler import SlotParticipant, SoftSwissScheduler, WaveSlotSuggestion


class SwissTournamentEngine:
    def __init__(self, scheduler: Optional[SoftSwissScheduler] = None, on_change: Optional[Callable[[], None]] = None) -> None:
        self.scheduler = scheduler or SoftSwissScheduler()
        self.state = TournamentState()
        self.on_change = on_change

    def _notify_change(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def now(self) -> float:
        return time.time()

    def phase_label(self) -> str:
        return PHASE_LABELS.get(self.state.phase, self.state.phase)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.state.logs.append(f"[{stamp}] {message}")
        self.state.logs = self.state.logs[-LOG_LIMIT:]

    def next_match_id(self, prefix: str) -> str:
        match_id = f"{prefix}-{self.state.match_counter:04d}"
        self.state.match_counter += 1
        return match_id

    def team_name(self, team_id: int) -> str:
        team = self.state.teams.get(team_id)
        return team.name if team else "-"

    def get_team(self, team_id: int) -> Team:
        return self.state.teams[team_id]

    def _used_table_numbers(self) -> set[int]:
        used = {match.table for match in self.active_matches() if match.table is not None}
        if self.b_group_active_match() is not None:
            used.add(B_GROUP_TABLE_NUMBER)
        return used

    def _current_wave_finished_count(self) -> int:
        return sum(1 for match in self.current_wave_matches() if match.status == "finished")

    def _current_wave_prepare_threshold(self) -> int:
        total = len(self.current_wave_matches()) or SWISS_MATCHES_PER_WAVE
        return max(1, ceil(total * SWISS_WAVE_PREPARE_RATIO))

    def _current_wave_ready_for_next_plan(self) -> bool:
        if not self.state.current_wave or self.current_wave_complete():
            return False
        if self.pending_current_wave():
            return False
        return self._current_wave_finished_count() >= self._current_wave_prepare_threshold()

    def _b_group_blocks_table_5_for_swiss(self) -> bool:
        return self.state.b_group.phase not in {"SETUP", "FINISHED"} and any(
            match.status in {"pending", "active"} for match in self.state.b_group.matches.values()
        )

    def _swiss_can_use_table_5(self) -> bool:
        if self._b_group_blocks_table_5_for_swiss():
            return False
        if self.state.phase != "SWISS":
            return True
        if not self.state.current_wave or self.current_wave_complete():
            return True
        if self.pending_current_wave():
            return True
        if not self.state.prepared_wave:
            return False
        return self._current_wave_ready_for_next_plan() and self._pending_wave_is_plan_safe(self.state.prepared_wave)

    def swiss_table_numbers(self) -> List[int]:
        tables = list(range(1, SWISS_PRIMARY_TABLE_COUNT + 1))
        if TABLE_COUNT >= B_GROUP_TABLE_NUMBER and self._swiss_can_use_table_5():
            tables.append(B_GROUP_TABLE_NUMBER)
        return tables

    def free_tables(self) -> List[int]:
        used = self._used_table_numbers()
        return [table for table in self.swiss_table_numbers() if table not in used]

    def active_matches(self) -> List[Match]:
        return sorted(
            [match for match in self.state.matches.values() if match.status == "active" and match.table is not None],
            key=lambda item: item.table or 999,
        )

    def current_wave_matches(self) -> List[Match]:
        if not self.state.current_wave:
            return []
        return [self.state.matches[mid] for mid in self.state.current_wave.match_ids if mid in self.state.matches]

    def prepared_wave_matches(self) -> List[Match]:
        if not self.state.prepared_wave:
            return []
        return [self.state.matches[mid] for mid in self.state.prepared_wave.match_ids if mid in self.state.matches]

    def pending_current_wave(self) -> List[Match]:
        return [match for match in self.current_wave_matches() if match.status == "pending"]

    def pending_prepared_wave(self) -> List[Match]:
        return [match for match in self.prepared_wave_matches() if match.status == "pending"]

    def current_wave_complete(self) -> bool:
        wave_matches = self.current_wave_matches()
        return bool(wave_matches) and all(match.status == "finished" for match in wave_matches)

    def current_wave_remaining_active(self) -> int:
        return sum(1 for match in self.current_wave_matches() if match.status == "active")

    def swiss_total_matches(self) -> int:
        return (len(self.state.teams) * SWISS_GAMES_PER_TEAM) // 2 if self.state.teams else 0

    def swiss_finished_matches(self) -> int:
        return sum(1 for match in self.state.matches.values() if match.phase == "SWISS" and match.status == "finished")

    def get_buchholz_map(self) -> Dict[int, int]:
        result: Dict[int, int] = {}
        for team in self.state.teams.values():
            result[team.id] = sum(self.state.teams[opp_id].swiss_points for opp_id in team.opponents if opp_id in self.state.teams)
        return result

    def ranking(self) -> List[Team]:
        buchholz = self.get_buchholz_map()
        return sorted(
            self.state.teams.values(),
            key=lambda team: (-team.swiss_points, -team.cups_metric, -buchholz[team.id], team.seed),
        )

    def ranking_rows(self) -> List[dict]:
        buchholz = self.get_buchholz_map()
        rows: List[dict] = []
        for idx, team in enumerate(self.ranking(), start=1):
            rows.append(
                {
                    "rank": idx,
                    "team_id": team.id,
                    "name": team.name,
                    "seed": team.seed,
                    "points": team.swiss_points,
                    "cups": team.cups_metric,
                    "buchholz": buchholz[team.id],
                    "games": team.swiss_games_played,
                    "ko_seed": team.ko_seed,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # B group
    # ------------------------------------------------------------------

    def _b_group_next_match_id(self) -> str:
        match_id = f"B-{self.state.b_group.match_counter:04d}"
        self.state.b_group.match_counter += 1
        return match_id

    def b_group_team_name(self, team_id: int) -> str:
        team = self.state.b_group.teams.get(team_id)
        return team.name if team else "-"

    def b_group_team_names_text(self) -> str:
        teams = sorted(self.state.b_group.teams.values(), key=lambda team: team.seed)
        return "\n".join(team.name for team in teams)

    def b_group_set_teams(self, names: List[str]) -> None:
        clean_names = [name.strip() for name in names if name.strip()]
        if len(clean_names) != B_GROUP_TEAM_COUNT:
            raise ValueError(f"Die B-Gruppe braucht genau {B_GROUP_TEAM_COUNT} Teams.")
        if len({name.lower() for name in clean_names}) != len(clean_names):
            raise ValueError("Teamnamen in der B-Gruppe muessen eindeutig sein.")

        b_group = self.state.b_group
        if b_group.phase != "SETUP" and len(clean_names) != len(b_group.teams):
            raise ValueError("Nach dem Start darf die Anzahl der B-Gruppen-Teams nicht geaendert werden.")

        if b_group.phase == "SETUP":
            b_group.teams = {
                idx: BGroupTeam(id=idx, name=name, seed=idx)
                for idx, name in enumerate(clean_names, start=1)
            }
            b_group.matches = {}
            b_group.match_counter = 1
            b_group.podium = []
        else:
            for idx, name in enumerate(clean_names, start=1):
                if idx in b_group.teams:
                    b_group.teams[idx].name = name
        self._notify_change()

    def b_group_start(self, names: Optional[List[str]] = None, table_label: str = B_GROUP_TABLE_LABEL) -> None:
        if self.state.b_group.phase != "SETUP":
            raise ValueError("Die B-Gruppe laeuft bereits. Zum Neustart bitte zuerst zuruecksetzen.")
        if names is not None:
            self.b_group_set_teams(names)
        b_group = self.state.b_group
        if len(b_group.teams) != B_GROUP_TEAM_COUNT:
            raise ValueError(f"Die B-Gruppe braucht genau {B_GROUP_TEAM_COUNT} Teams.")

        teams = sorted(b_group.teams.values(), key=lambda team: team.seed)
        by_seed = {team.seed: team for team in teams}
        round_robin_order = [
            (1, 2, "Runde 1 - Spiel 1"),
            (3, 4, "Runde 1 - Spiel 2"),
            (1, 3, "Runde 2 - Spiel 1"),
            (2, 4, "Runde 2 - Spiel 2"),
            (1, 4, "Runde 3 - Spiel 1"),
            (2, 3, "Runde 3 - Spiel 2"),
        ]
        b_group.phase = "ROUND_ROBIN"
        b_group.matches = {}
        b_group.match_counter = 1
        b_group.podium = []
        self._b_group_rebuild_derived_state()

        for seed_a, seed_b, label in round_robin_order:
            team_a = by_seed[seed_a]
            team_b = by_seed[seed_b]
            match_id = self._b_group_next_match_id()
            b_group.matches[match_id] = BGroupMatch(
                match_id=match_id,
                stage="ROUND_ROBIN",
                label=label,
                team_a=team_a.id,
                team_b=team_b.id,
                table_label=B_GROUP_TABLE_LABEL,
            )
        self.log(f"B-Gruppe gestartet ({len(b_group.matches)} Gruppenspiele).")
        self._b_group_activate_next_match_if_possible()
        self._notify_change()

    def _b_group_match_sort_key(self, match: BGroupMatch) -> tuple[int, str]:
        stage_order = {"ROUND_ROBIN": 1, "SEMI": 2, "THIRD": 3, "FINAL": 4}
        return stage_order.get(match.stage, 99), match.match_id

    def b_group_active_match(self) -> Optional[BGroupMatch]:
        active = [match for match in self.state.b_group.matches.values() if match.status == "active"]
        if not active:
            return None
        return sorted(active, key=self._b_group_match_sort_key)[0]

    def _b_group_pending_matches(self) -> List[BGroupMatch]:
        return [
            match
            for match in sorted(self.state.b_group.matches.values(), key=self._b_group_match_sort_key)
            if match.status == "pending"
        ]

    def b_group_table_status(self) -> str:
        active = self.b_group_active_match()
        if active:
            return f"{B_GROUP_TABLE_LABEL}: B-Gruppe läuft"
        if B_GROUP_TABLE_NUMBER in self.state.active_tables:
            match = self.state.matches.get(self.state.active_tables[B_GROUP_TABLE_NUMBER])
            if match:
                return f"{B_GROUP_TABLE_LABEL}: Swiss belegt"
        if self._b_group_pending_matches():
            return f"{B_GROUP_TABLE_LABEL}: frei fuer B-Gruppe"
        return f"{B_GROUP_TABLE_LABEL}: frei"

    def _b_group_activate_next_match_if_possible(self) -> bool:
        if self.state.b_group.phase in {"SETUP", "FINISHED"}:
            return False
        if self.b_group_active_match() is not None:
            return False
        if B_GROUP_TABLE_NUMBER in self.state.active_tables:
            return False
        pending = self._b_group_pending_matches()
        if not pending:
            return False
        match = pending[0]
        match.status = "active"
        match.table_label = B_GROUP_TABLE_LABEL
        self.log(f"{B_GROUP_TABLE_LABEL}: B-Gruppe - {self.b_group_team_name(match.team_a)} vs {self.b_group_team_name(match.team_b)} gestartet.")
        return True

    def _b_group_rebuild_derived_state(self) -> None:
        b_group = self.state.b_group
        for team in b_group.teams.values():
            team.points = 0
            team.cups_metric = 0
            team.wins = 0
            team.losses = 0
            team.games_played = 0

        for match in sorted(b_group.matches.values(), key=self._b_group_match_sort_key):
            if match.stage != "ROUND_ROBIN" or match.status != "finished":
                continue
            if match.winner is None or match.loser is None:
                continue
            team_a = b_group.teams.get(match.team_a)
            team_b = b_group.teams.get(match.team_b)
            if not team_a or not team_b:
                continue
            loser_cups = match.loser_cups_hit if match.loser_cups_hit is not None else 0
            '''cup_change = 10 - loser_cups'''
            points_a = 3 if match.winner == match.team_a else 0
            points_b = 3 if match.winner == match.team_b else 0
            team_a.points += points_a
            team_b.points += points_b
            team_a.games_played += 1
            team_b.games_played += 1
            if match.winner == team_a.id:
                team_a.cups_metric += loser_cups
                team_b.cups_metric -= loser_cups
                team_a.wins += 1
                team_b.losses += 1
            else:
                team_b.cups_metric += loser_cups
                team_a.cups_metric -= loser_cups
                team_b.wins += 1
                team_a.losses += 1

    def b_group_ranking_rows(self) -> List[dict]:
        self._b_group_rebuild_derived_state()
        rows: List[dict] = []
        teams = sorted(
            self.state.b_group.teams.values(),
            key=lambda team: (-team.points, -team.cups_metric, -team.wins, team.losses, team.seed),
        )
        for idx, team in enumerate(teams, start=1):
            rows.append(
                {
                    "rank": idx,
                    "team_id": team.id,
                    "name": team.name,
                    "points": team.points,
                    "cups": team.cups_metric,
                    "wins": team.wins,
                    "losses": team.losses,
                    "games": team.games_played,
                }
            )
        return rows

    def _b_group_round_robin_complete(self) -> bool:
        rr_matches = [match for match in self.state.b_group.matches.values() if match.stage == "ROUND_ROBIN"]
        return bool(rr_matches) and all(match.status == "finished" for match in rr_matches)

    def _b_group_schedule_playoffs_if_ready(self) -> None:
        b_group = self.state.b_group
        if b_group.phase == "ROUND_ROBIN" and self._b_group_round_robin_complete():
            if any(match.stage == "SEMI" for match in b_group.matches.values()):
                b_group.phase = "PLAYOFF"
                return

            ranking = self.b_group_ranking_rows()
            if len(ranking) != B_GROUP_TEAM_COUNT:
                b_group.phase = "FINISHED"
                return

            semi_1_id = self._b_group_next_match_id()
            b_group.matches[semi_1_id] = BGroupMatch(
                match_id=semi_1_id,
                stage="SEMI",
                label="Halbfinale 1 (1 vs 4)",
                team_a=ranking[0]["team_id"],
                team_b=ranking[3]["team_id"],
            )
            semi_2_id = self._b_group_next_match_id()
            b_group.matches[semi_2_id] = BGroupMatch(
                match_id=semi_2_id,
                stage="SEMI",
                label="Halbfinale 2 (2 vs 3)",
                team_a=ranking[1]["team_id"],
                team_b=ranking[2]["team_id"],
            )
            b_group.phase = "PLAYOFF"
            self.log("B-Gruppe: Halbfinals vorbereitet.")

        semi_matches = [match for match in b_group.matches.values() if match.stage == "SEMI"]
        if b_group.phase != "PLAYOFF" or not semi_matches:
            return
        if any(match.status != "finished" for match in semi_matches):
            return
        if any(match.stage in {"FINAL", "THIRD"} for match in b_group.matches.values()):
            return

        semi_matches.sort(key=self._b_group_match_sort_key)
        first, second = semi_matches[0], semi_matches[1]
        if first.winner is None or first.loser is None or second.winner is None or second.loser is None:
            return

        third_id = self._b_group_next_match_id()
        b_group.matches[third_id] = BGroupMatch(
            match_id=third_id,
            stage="THIRD",
            label="Spiel um Platz 3",
            team_a=first.loser,
            team_b=second.loser,
        )
        final_id = self._b_group_next_match_id()
        b_group.matches[final_id] = BGroupMatch(
            match_id=final_id,
            stage="FINAL",
            label="Finale",
            team_a=first.winner,
            team_b=second.winner,
        )
        self.log("B-Gruppe: Finale und Spiel um Platz 3 vorbereitet.")

    def _b_group_update_podium_if_finished(self) -> None:
        b_group = self.state.b_group
        if b_group.phase != "PLAYOFF":
            return
        playoff_matches = [match for match in b_group.matches.values() if match.stage in {"FINAL", "THIRD"}]
        if not playoff_matches or any(match.status != "finished" for match in playoff_matches):
            return
        final = next((match for match in playoff_matches if match.stage == "FINAL"), None)
        if not final or final.winner is None or final.loser is None:
            return
        podium = [final.winner, final.loser]
        third = next((match for match in playoff_matches if match.stage == "THIRD"), None)
        if third and third.winner is not None:
            podium.append(third.winner)
        elif len(self.b_group_ranking_rows()) >= 3:
            podium.append(self.b_group_ranking_rows()[2]["team_id"])
        b_group.podium = podium
        b_group.phase = "FINISHED"
        self.log("B-Gruppe beendet.")

    def b_group_submit_result(
        self,
        match_id: str,
        winner_team_id: int,
        loser_cups_hit: int = 0,
        table_label: str = B_GROUP_TABLE_LABEL,
    ) -> None:
        self._b_group_store_result(match_id, winner_team_id, loser_cups_hit)
        self._b_group_schedule_playoffs_if_ready()
        self._b_group_update_podium_if_finished()
        self._b_group_activate_next_match_if_possible()
        match = self.state.b_group.matches[match_id]
        self.log(f"B-Gruppe: {match.label} gespeichert.")
        self._notify_change()

    def _b_group_validate_loser_cups(self, loser_cups_hit: int) -> None:
        if loser_cups_hit < 0 or loser_cups_hit > 10:
            raise ValueError("Es muessen zwischen 0 und 10 noch stoh.")

    def _b_group_store_result(self, match_id: str, winner_team_id: int, loser_cups_hit: int) -> None:
        b_group = self.state.b_group
        match = b_group.matches.get(match_id)
        if not match:
            raise ValueError("Dieses B-Gruppen-Match gibt es nicht.")
        if winner_team_id not in {match.team_a, match.team_b}:
            raise ValueError("Das Sieger-Team gehoert nicht zu diesem B-Gruppen-Match.")
        self._b_group_validate_loser_cups(loser_cups_hit)

        loser_team_id = match.team_b if winner_team_id == match.team_a else match.team_a
        match.status = "finished"
        match.winner = winner_team_id
        match.loser = loser_team_id
        match.table_label = B_GROUP_TABLE_LABEL
        match.loser_cups_hit = loser_cups_hit
        if winner_team_id == match.team_a:
            match.points_a = 3
            match.points_b = 0
        else:
            match.points_a = 0
            match.points_b = 3

        self._b_group_rebuild_derived_state()

    def _b_group_discard_matches_by_stage(self, stages: set[str]) -> None:
        b_group = self.state.b_group
        for match_id, match in list(b_group.matches.items()):
            if match.stage in stages:
                del b_group.matches[match_id]
        b_group.podium = []

    def b_group_edit_result(self, match_id: str, winner_team_id: int, loser_cups_hit: int) -> None:
        match = self.state.b_group.matches.get(match_id)
        if not match:
            raise ValueError("Dieses B-Gruppen-Match gibt es nicht.")
        if match.status != "finished":
            raise ValueError("Nur gespeicherte B-Gruppen-Ergebnisse koennen bearbeitet werden.")

        stage = match.stage
        self._b_group_store_result(match_id, winner_team_id, loser_cups_hit)
        b_group = self.state.b_group

        if stage == "ROUND_ROBIN":
            self._b_group_discard_matches_by_stage({"SEMI", "THIRD", "FINAL"})
            b_group.phase = "ROUND_ROBIN"
        elif stage == "SEMI":
            self._b_group_discard_matches_by_stage({"THIRD", "FINAL"})
            b_group.phase = "PLAYOFF"
        elif stage in {"THIRD", "FINAL"} and b_group.phase == "FINISHED":
            b_group.phase = "PLAYOFF"

        self._b_group_schedule_playoffs_if_ready()
        self._b_group_update_podium_if_finished()
        self._b_group_activate_next_match_if_possible()
        self.log(f"B-Gruppe: {match.label} bearbeitet.")
        self._notify_change()

    def b_group_reset(self) -> None:
        self.state.b_group = BGroupState()
        self._notify_change()

    def b_group_match_rows(self) -> List[dict]:
        rows: List[dict] = []
        for match in sorted(self.state.b_group.matches.values(), key=self._b_group_match_sort_key):
            if match.status == "active":
                status_text = "läuft"
            elif match.status == "finished":
                status_text = "fertig"
            else:
                status_text = "wartet"
            rows.append(
                {
                    "match_id": match.match_id,
                    "label": match.label,
                    "stage": match.stage,
                    "table": match.table_label,
                    "team_a": self.b_group_team_name(match.team_a),
                    "team_b": self.b_group_team_name(match.team_b),
                    "status": match.status,
                    "status_text": status_text,
                    "winner": self.b_group_team_name(match.winner) if match.winner else "-",
                    "points": (
                        "-"
                        if match.points_a is None or match.points_b is None
                        else f"{match.points_a}:{match.points_b}"
                    ),
                    "loser_cups": "-" if match.loser_cups_hit is None else match.loser_cups_hit,
                }
            )
        return rows

    def b_group_next_match(self) -> Optional[dict]:
        for row in self.b_group_match_rows():
            if row["status"] == "pending":
                return row
        return None

    def swiss_complete(self) -> bool:
        return bool(self.state.teams) and all(team.swiss_games_played >= SWISS_GAMES_PER_TEAM for team in self.state.teams.values())

    def editable_match_rows(self) -> List[dict]:
        rows: List[dict] = []
        for match in sorted(self.state.matches.values(), key=lambda item: (item.started_ts or 0.0, item.match_id), reverse=True):
            if match.phase != "SWISS":
                continue
            rows.append(
                {
                    "match_id": match.match_id,
                    "slot": match.slot_label or match.match_id,
                    "team_a": self.team_name(match.team_a),
                    "team_b": self.team_name(match.team_b),
                    "status": match.status,
                    "winner": self.team_name(match.winner) if match.winner else "-",
                    "ot": "ja" if match.is_overtime else "nein",
                    "cups": "-" if match.loser_cups_hit is None else match.loser_cups_hit,
                }
            )
        return rows

    def _swiss_result_values(self, is_overtime: bool, loser_cups_hit: int) -> tuple[int, int, int, int]:
        if is_overtime:
            if loser_cups_hit > 10 :
                raise ValueError("Becher sind immer unter 10 du Öpfel")
            winner_points, loser_points = 2, 1
            winner_cup_change = loser_cups_hit
            loser_cup_change = 0
        else:
            if loser_cups_hit < 0 or loser_cups_hit > 10:
                raise ValueError("Ohne OT muss beim sieger no 1 - 10 Becher stoh")
            winner_points, loser_points = 3, 0
            winner_cup_change = loser_cups_hit
            loser_cup_change = -winner_cup_change
        return winner_points, loser_points, winner_cup_change, loser_cup_change

    def _remove_completed_match_id(self, match_id: str) -> None:
        self.state.completed_match_ids = [value for value in self.state.completed_match_ids if value != match_id]

    def _reset_match_result_fields(self, match: Match, status: str = "pending") -> None:
        match.status = status
        match.winner = None
        match.loser = None
        match.is_overtime = False
        match.loser_cups_hit = None
        match.ended_ts = None

    def _trim_wave_to_existing_matches(self, wave: Optional[WavePlan]) -> Optional[WavePlan]:
        if not wave:
            return None
        wave.match_ids = [match_id for match_id in wave.match_ids if match_id in self.state.matches]
        return wave if wave.match_ids else None

    def _discard_open_swiss_plans(self, preserve_match_ids: Optional[set[str]] = None) -> None:
        preserve_match_ids = preserve_match_ids or set()
        for match_id, match in list(self.state.matches.items()):
            if match.phase == "SWISS" and match.status == "pending" and match_id not in preserve_match_ids:
                del self.state.matches[match_id]
        self.state.current_wave = self._trim_wave_to_existing_matches(self.state.current_wave)
        self.state.prepared_wave = None

    def _rebuild_team_derived_state(self) -> None:
        for team in self.state.teams.values():
            team.swiss_points = 0
            team.cups_metric = 0
            team.swiss_games_played = 0
            team.opponents.clear()
            team.last_finish_ts = 0.0
            team.active_match_id = None
            if self.state.phase == "SWISS":
                team.ko_seed = None

        rebuilt_completed: List[str] = []
        seen_completed: set[str] = set()
        for match_id in self.state.completed_match_ids:
            if match_id in seen_completed:
                continue
            match = self.state.matches.get(match_id)
            if not match or match.status != "finished":
                continue
            seen_completed.add(match_id)
            rebuilt_completed.append(match_id)
            if match.phase != "SWISS" or match.winner is None or match.loser is None or match.loser_cups_hit is None:
                continue

            winner_points, loser_points, winner_cups, loser_cups = self._swiss_result_values(match.is_overtime, match.loser_cups_hit)
            winner = self.get_team(match.winner)
            loser = self.get_team(match.loser)
            winner.swiss_points += winner_points
            loser.swiss_points += loser_points
            winner.cups_metric += winner_cups
            loser.cups_metric += loser_cups
            winner.swiss_games_played += 1
            loser.swiss_games_played += 1
            winner.opponents.add(loser.id)
            loser.opponents.add(winner.id)
            finished_ts = match.ended_ts or 0.0
            winner.last_finish_ts = max(winner.last_finish_ts, finished_ts)
            loser.last_finish_ts = max(loser.last_finish_ts, finished_ts)
        self.state.completed_match_ids = rebuilt_completed

        self.state.active_tables = {}
        for match in self.state.matches.values():
            if match.status != "active" or match.table is None:
                continue
            self.state.active_tables[match.table] = match.match_id
            if match.team_a in self.state.teams:
                self.state.teams[match.team_a].active_match_id = match.match_id
            if match.team_b in self.state.teams:
                self.state.teams[match.team_b].active_match_id = match.match_id

    def _rebuild_after_manual_change(self, rebuild_pairing: bool = True, preserve_match_ids: Optional[set[str]] = None) -> None:
        self._rebuild_team_derived_state()
        self._discard_open_swiss_plans(preserve_match_ids=preserve_match_ids)
        if rebuild_pairing:
            self.recompute_pairing(notify=False, preserve_match_ids=preserve_match_ids)

    def _rebuild_after_result_correction(
        self,
        rebuild_prepared_wave: bool,
        preserve_pending_match_ids: Optional[set[str]] = None,
    ) -> None:
        preserve_pending_match_ids = preserve_pending_match_ids or set()
        self._rebuild_team_derived_state()
        if not rebuild_prepared_wave or self.state.phase != "SWISS":
            return

        prepared_wave = self.state.prepared_wave
        if not prepared_wave:
            return

        prepared_matches = self.prepared_wave_matches()
        has_locked_prepared_match = any(match.status != "pending" for match in prepared_matches)
        preserve_in_prepared = bool(preserve_pending_match_ids.intersection(prepared_wave.match_ids))
        if has_locked_prepared_match or preserve_in_prepared:
            if self._reassign_wave_remaining(prepared_wave):
                return
            if has_locked_prepared_match:
                return

        prepared_wave_index = prepared_wave.wave_index
        self._discard_wave_matches(prepared_wave)
        self.state.prepared_wave = None

        if self.swiss_complete():
            return
        if self.current_wave_complete() or self._current_wave_ready_for_next_plan():
            try:
                self._build_wave_plan(prepared_wave_index, relaxed=False, target="prepared")
            except ValueError:
                self._build_wave_plan(prepared_wave_index, relaxed=True, target="prepared")

    def undo_last_result(self) -> str:
        for match_id in reversed(self.state.completed_match_ids):
            match = self.state.matches.get(match_id)
            if match and match.phase == "SWISS" and match.status == "finished":
                self._remove_completed_match_id(match_id)
                old_table = match.table
                replacement_match_id = self.state.active_tables.get(old_table) if old_table is not None else None
                replacement_match = self.state.matches.get(replacement_match_id or "")
                if replacement_match and replacement_match.match_id != match.match_id:
                    replacement_match.table = None
                    replacement_match.started_ts = 0.0
                    replacement_match.status = "pending"

                self._reset_match_result_fields(match, status="active" if old_table is not None else "pending")
                match.table = old_table
                preserve_pending_match_ids = {match.match_id}
                if replacement_match and replacement_match.match_id != match.match_id:
                    preserve_pending_match_ids.add(replacement_match.match_id)
                self._rebuild_after_result_correction(
                    rebuild_prepared_wave=self.state.prepared_wave is not None,
                    preserve_pending_match_ids=preserve_pending_match_ids,
                )
                self.log(f"Letzte Eingabe zurueckgenommen: {match.slot_label or match.match_id}.")
                self._notify_change()
                return match.match_id
        raise ValueError("Es gibt kein Swiss-Ergebnis, das zurueckgenommen werden kann.")

    def edit_match_result(self, match_id: str, winner_team_id: int, is_overtime: bool, loser_cups_hit: int) -> None:
        match = self.state.matches.get(match_id)
        if not match or match.phase != "SWISS":
            raise ValueError("Dieses Match kann hier nicht bearbeitet werden.")
        if match.status != "finished":
            raise ValueError("Nur bereits gespeicherte Swiss-Ergebnisse koennen bearbeitet werden.")
        if winner_team_id not in {match.team_a, match.team_b}:
            raise ValueError("Das Sieger-Team gehoert nicht zu diesem Match.")
        loser_team_id = match.team_b if winner_team_id == match.team_a else match.team_a
        self._swiss_result_values(is_overtime, loser_cups_hit)
        match.winner = winner_team_id
        match.loser = loser_team_id
        match.is_overtime = is_overtime
        match.loser_cups_hit = loser_cups_hit
        if match.ended_ts is None:
            match.ended_ts = self.now()
        self._rebuild_after_result_correction(rebuild_prepared_wave=self.state.prepared_wave is not None)
        self.log(f"Match bearbeitet: {match.slot_label or match.match_id}.")
        self._notify_change()

    def reset_match(self, match_id: str) -> None:
        match = self.state.matches.get(match_id)
        if not match or match.phase != "SWISS":
            raise ValueError("Dieses Match kann hier nicht zurueckgesetzt werden.")
        self._remove_completed_match_id(match_id)
        if match.table in self.state.active_tables:
            del self.state.active_tables[match.table]
        match.table = None
        match.started_ts = 0.0
        self._reset_match_result_fields(match, status="pending")
        self._rebuild_after_manual_change(rebuild_pairing=True, preserve_match_ids={match.match_id})
        self.log(f"Match zurueckgesetzt: {match.slot_label or match.match_id}.")
        self._notify_change()

    def _recompute_prepared_wave(self) -> bool:
        prepared_wave = self.state.prepared_wave
        if not prepared_wave:
            return False

        prepared_matches = self.prepared_wave_matches()
        if any(match.status != "pending" for match in prepared_matches):
            return self._reassign_wave_remaining(prepared_wave)

        prepared_wave_index = prepared_wave.wave_index
        self._discard_wave_matches(prepared_wave)
        self.state.prepared_wave = None

        if self.current_wave_complete() or self._current_wave_ready_for_next_plan():
            try:
                self._build_wave_plan(prepared_wave_index, relaxed=False, target="prepared")
            except ValueError:
                self._build_wave_plan(prepared_wave_index, relaxed=True, target="prepared")
            return True
        return False

    def recompute_pairing(self, notify: bool = True, preserve_match_ids: Optional[set[str]] = None) -> bool:
        if self.state.phase != "SWISS":
            return False
        if self.swiss_complete():
            if notify:
                self._notify_change()
            return False
        changed = False
        if self.state.current_wave is None or self.current_wave_complete():
            self._discard_open_swiss_plans(preserve_match_ids=preserve_match_ids)
            next_wave_index = self.state.wave_index + 1 if self.state.wave_index else 1
            try:
                self._build_wave_plan(next_wave_index, relaxed=False, target="current")
            except ValueError:
                self._build_wave_plan(next_wave_index, relaxed=True, target="current")
            changed = True
        elif self.pending_current_wave():
            changed = False
        elif self.state.prepared_wave:
            changed = self._recompute_prepared_wave()
        elif self._maybe_prepare_next_wave():
            changed = True
        if changed:
            started = self._activate_available_matches_on_free_tables()
            changed = changed or started > 0
        if changed:
            self.log("Pairing neu berechnet.")
        if notify:
            self._notify_change()
        return changed

    # ------------------------------------------------------------------
    # Tournament setup
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.state = TournamentState()

    def new_tournament(self, team_names: List[str]) -> None:
        clean_names = [name.strip() for name in team_names if name.strip()]
        if len(clean_names) != TEAM_COUNT:
            raise ValueError(f"Es müssen genau {TEAM_COUNT} Teamnamen eingegeben werden.")

        b_group = self.state.b_group
        self.reset()
        self.state.b_group = b_group
        self.state.started_at = self.now()
        self.state.phase = "SWISS"
        self.state.wave_index = 1

        for idx, name in enumerate(clean_names, start=1):
            self.state.teams[idx] = Team(id=idx, name=name, seed=idx)

        self.log("Neues Turnier gestartet.")
        self._build_wave_plan(self.state.wave_index, relaxed=False)
        self.fill_free_tables(relaxed=False)
        self._notify_change()

    # ------------------------------------------------------------------
    # Wave / swiss scheduling
    # ------------------------------------------------------------------

    def _apply_slot_participant(self, match: Match, participant: SlotParticipant, side: str) -> None:
        team_id = participant.team_id or 0
        placeholder = participant.label if participant.is_placeholder else ""
        if side == "a":
            match.team_a = team_id
            match.placeholder_a = placeholder
            match.resolved_a = participant.team_id
            match.source_match_id_a = participant.source_match_id
            match.source_outcome_a = participant.source_outcome
        else:
            match.team_b = team_id
            match.placeholder_b = placeholder
            match.resolved_b = participant.team_id
            match.source_match_id_b = participant.source_match_id
            match.source_outcome_b = participant.source_outcome
        match.is_placeholder = bool(match.source_match_id_a or match.source_match_id_b or not match.team_a or not match.team_b)

    def _apply_slot_suggestion(self, match: Match, suggestion: WaveSlotSuggestion) -> None:
        self._apply_slot_participant(match, suggestion.participant_a, "a")
        self._apply_slot_participant(match, suggestion.participant_b, "b")

    def _build_wave_plan(self, wave_index: int, relaxed: bool = False, target: str = "current") -> None:
        if target == "prepared":
            slot_suggestions, point_cap, game_cap = self.scheduler.build_placeholder_wave_bundle(
                self.state,
                table_target=SWISS_MATCHES_PER_WAVE,
                relaxed=relaxed,
                reference_ts=self.now(),
                require_all_assignments_legal=True,
            )
            if len(slot_suggestions) < SWISS_MATCHES_PER_WAVE:
                if not relaxed:
                    slot_suggestions, point_cap, game_cap = self.scheduler.build_placeholder_wave_bundle(
                        self.state,
                        table_target=SWISS_MATCHES_PER_WAVE,
                        relaxed=True,
                        reference_ts=self.now(),
                        require_all_assignments_legal=True,
                    )
                if len(slot_suggestions) < SWISS_MATCHES_PER_WAVE:
                    raise ValueError("Fuer diese Welle konnte keine vollstaendige Paarung gefunden werden.")

            wave = WavePlan(wave_index=wave_index, created_ts=self.now(), status="prepared")
            for idx, suggestion in enumerate(slot_suggestions[:SWISS_MATCHES_PER_WAVE], start=1):
                match_id = self.next_match_id(f"W{wave_index}")
                match = Match(
                    match_id=match_id,
                    phase="SWISS",
                    table=None,
                    team_a=0,
                    team_b=0,
                    wave_index=wave_index,
                    wave_order=idx,
                    status="pending",
                    point_cap_used=point_cap,
                    game_cap_used=game_cap,
                    slot_label=f"W{wave_index:02d}-{idx:02d}",
                )
                self._apply_slot_suggestion(match, suggestion)
                self.state.matches[match_id] = match
                wave.match_ids.append(match_id)

            self.state.prepared_wave = wave
            self.log(f"Welle {wave_index} vorbereitet ({len(wave.match_ids)} Matches).")
            return

        suggestions, point_cap, game_cap = self.scheduler.build_wave_bundle(
            self.state,
            table_target=SWISS_MATCHES_PER_WAVE,
            relaxed=relaxed,
            reference_ts=self.now(),
        )
        if len(suggestions) < SWISS_MATCHES_PER_WAVE:
            if not relaxed:
                suggestions, point_cap, game_cap = self.scheduler.build_wave_bundle(
                    self.state,
                    table_target=SWISS_MATCHES_PER_WAVE,
                    relaxed=True,
                    reference_ts=self.now(),
                )
            if len(suggestions) < SWISS_MATCHES_PER_WAVE:
                raise ValueError("Für diese Welle konnte keine vollständige Paarung gefunden werden.")

        wave = WavePlan(wave_index=wave_index, created_ts=self.now(), status="active" if target == "current" else "prepared")
        for idx, suggestion in enumerate(suggestions[:SWISS_MATCHES_PER_WAVE], start=1):
            match_id = self.next_match_id(f"W{wave_index}")
            match = Match(
                match_id=match_id,
                phase="SWISS",
                table=None,
                team_a=suggestion.team_a_id,
                team_b=suggestion.team_b_id,
                wave_index=wave_index,
                wave_order=idx,
                status="pending",
                point_cap_used=suggestion.point_cap_used,
                game_cap_used=suggestion.game_cap_used,
                slot_label=f"W{wave_index:02d}-{idx:02d}",
                resolved_a=suggestion.team_a_id,
                resolved_b=suggestion.team_b_id,
            )
            self.state.matches[match_id] = match
            wave.match_ids.append(match_id)

        if target == "prepared":
            self.state.prepared_wave = wave
            self.log(f"Welle {wave_index} vorbereitet ({len(wave.match_ids)} Matches).")
        else:
            self.state.current_wave = wave
            self.state.prepared_wave = None
            self.state.wave_index = wave_index
            self.log(f"Welle {wave_index} gestartet ({len(wave.match_ids)} Matches).")

    def _promote_prepared_wave(self) -> bool:
        prepared_wave = self.state.prepared_wave
        if not prepared_wave:
            return False
        if not self._pending_wave_is_activation_safe(prepared_wave) and not self._reassign_wave_remaining(prepared_wave):
            self._discard_wave_matches(prepared_wave)
            self.state.prepared_wave = None
            self.log("Vorbereitete Welle verworfen, weil sie nicht rematchfrei auflösbar war.")
            return False

        self.state.current_wave = prepared_wave
        self.state.current_wave.status = "active"
        self.state.prepared_wave = None
        self.state.wave_index = self.state.current_wave.wave_index
        self.log(f"Welle {self.state.current_wave.wave_index} wird jetzt aktiv.")
        return True

    def _discard_wave_matches(self, wave: WavePlan) -> None:
        for match_id in wave.match_ids:
            match = self.state.matches.get(match_id)
            if match and match.status == "pending":
                del self.state.matches[match_id]

    def _pending_matches_from_wave(self, wave: Optional[WavePlan]) -> List[Match]:
        if not wave:
            return []
        matches = [self.state.matches[match_id] for match_id in wave.match_ids if match_id in self.state.matches]
        return [match for match in matches if match.status == "pending"]

    def _source_outcome_team_id(self, source_match_id: Optional[str], outcome: Optional[str]) -> Optional[int]:
        if not source_match_id or not outcome:
            return None
        source = self.state.matches.get(source_match_id)
        if not source or source.status != "finished":
            return None
        if outcome == "winner":
            return source.winner
        if outcome == "loser":
            return source.loser
        return None

    def _resolved_match_side(self, match: Match, side: str, mutate: bool = False) -> Optional[int]:
        if side == "a":
            team_id = match.resolved_a or (match.team_a if match.team_a else None)
            source_team_id = self._source_outcome_team_id(match.source_match_id_a, match.source_outcome_a)
            if source_team_id is not None:
                team_id = source_team_id
            if mutate and team_id is not None:
                match.resolved_a = team_id
                match.team_a = team_id
                match.placeholder_a = ""
        else:
            team_id = match.resolved_b or (match.team_b if match.team_b else None)
            source_team_id = self._source_outcome_team_id(match.source_match_id_b, match.source_outcome_b)
            if source_team_id is not None:
                team_id = source_team_id
            if mutate and team_id is not None:
                match.resolved_b = team_id
                match.team_b = team_id
                match.placeholder_b = ""
        if mutate:
            match.is_placeholder = not bool(match.team_a and match.team_b)
        return team_id

    def _resolved_match_pair(self, match: Match, mutate: bool = False) -> tuple[Optional[int], Optional[int]]:
        return self._resolved_match_side(match, "a", mutate=mutate), self._resolved_match_side(match, "b", mutate=mutate)

    def _is_legal_swiss_pair(self, team_a_id: int, team_b_id: int) -> bool:
        if team_a_id == team_b_id:
            return False
        team_a = self.state.teams.get(team_a_id)
        return bool(team_a) and team_b_id not in team_a.opponents

    def _team_ready_for_activation(self, team_id: int) -> bool:
        team = self.state.teams.get(team_id)
        return bool(team and team.active_match_id is None and team.swiss_games_played < SWISS_GAMES_PER_TEAM)

    def _can_activate_match(self, match: Match, mutate: bool = False) -> bool:
        team_a_id, team_b_id = self._resolved_match_pair(match, mutate=mutate)
        if team_a_id is None or team_b_id is None:
            return False
        if not self._team_ready_for_activation(team_a_id) or not self._team_ready_for_activation(team_b_id):
            return False
        if match.phase == "SWISS" and not self._is_legal_swiss_pair(team_a_id, team_b_id):
            return False
        return True

    def _match_is_resolved_rematch(self, match: Match) -> bool:
        team_a_id, team_b_id = self._resolved_match_pair(match, mutate=False)
        return bool(team_a_id and team_b_id and match.phase == "SWISS" and not self._is_legal_swiss_pair(team_a_id, team_b_id))

    def _participant_from_match_side(self, match: Match, side: str) -> Optional[SlotParticipant]:
        team_id = self._resolved_match_side(match, side, mutate=False)
        if team_id is not None and team_id in self.state.teams:
            return self.scheduler.concrete_participant(self.state.teams[team_id])

        if side == "a":
            source_match_id = match.source_match_id_a
            source_outcome = match.source_outcome_a
        else:
            source_match_id = match.source_match_id_b
            source_outcome = match.source_outcome_b
        source = self.state.matches.get(source_match_id or "")
        if source and source_outcome in {"winner", "loser"}:
            return self.scheduler.outcome_participant(source, self.state, source_outcome)
        return None

    def _pending_wave_is_activation_safe(self, wave: Optional[WavePlan], ignored_match_id: Optional[str] = None) -> bool:
        seen_team_ids: set[int] = set()
        for match in self._pending_matches_from_wave(wave):
            if match.match_id == ignored_match_id:
                continue
            if not self._can_activate_match(match, mutate=False):
                return False
            team_a_id, team_b_id = self._resolved_match_pair(match, mutate=False)
            if team_a_id is None or team_b_id is None:
                return False
            if team_a_id in seen_team_ids or team_b_id in seen_team_ids:
                return False
            seen_team_ids.add(team_a_id)
            seen_team_ids.add(team_b_id)
        return True

    def _pending_wave_is_plan_safe(self, wave: Optional[WavePlan], ignored_match_id: Optional[str] = None) -> bool:
        for match in self._pending_matches_from_wave(wave):
            if match.match_id == ignored_match_id:
                continue

            participant_a = self._participant_from_match_side(match, "a")
            participant_b = self._participant_from_match_side(match, "b")
            if participant_a is None or participant_b is None:
                return False

            if participant_a.is_placeholder or participant_b.is_placeholder:
                if not self.scheduler._slot_pair_has_only_legal_assignments(participant_a, participant_b, self.state):
                    return False
                continue

            if participant_a.team_id is None or participant_b.team_id is None:
                return False
            if not self._is_legal_swiss_pair(participant_a.team_id, participant_b.team_id):
                return False
            if self.state.teams[participant_a.team_id].swiss_games_played >= SWISS_GAMES_PER_TEAM:
                return False
            if self.state.teams[participant_b.team_id].swiss_games_played >= SWISS_GAMES_PER_TEAM:
                return False
        return True

    def _reassign_wave_remaining(self, wave: Optional[WavePlan]) -> bool:
        pending = self._pending_matches_from_wave(wave)
        if not pending:
            return False

        participants_by_key: Dict[str, SlotParticipant] = {}
        for match in pending:
            for side in ("a", "b"):
                participant = self._participant_from_match_side(match, side)
                if participant is not None:
                    participants_by_key[participant.key] = participant

        participants = list(participants_by_key.values())
        if len(participants) < len(pending) * 2:
            return False
        require_all_assignments_legal = any(participant.is_placeholder for participant in participants)

        suggestions, point_cap, game_cap = self.scheduler.build_slot_wave_bundle(
            self.state,
            participants,
            table_target=len(pending),
            relaxed=True,
            reference_ts=self.now(),
            require_all_assignments_legal=require_all_assignments_legal,
        )
        if len(suggestions) < len(pending):
            return False

        for match, suggestion in zip(pending, suggestions):
            match.team_a = 0
            match.team_b = 0
            match.placeholder_a = ""
            match.placeholder_b = ""
            match.resolved_a = None
            match.resolved_b = None
            match.source_match_id_a = None
            match.source_match_id_b = None
            match.source_outcome_a = None
            match.source_outcome_b = None
            match.point_cap_used = point_cap
            match.game_cap_used = game_cap
            self._apply_slot_suggestion(match, suggestion)
        return self._pending_wave_is_plan_safe(wave)

    def _next_resolvable_match_from_wave(self, wave: Optional[WavePlan]) -> Optional[Match]:
        for _attempt in range(2):
            pending = self._pending_matches_from_wave(wave)
            if not pending:
                return None
            if not self._pending_wave_is_plan_safe(wave):
                if self._reassign_wave_remaining(wave):
                    continue

            for match in pending:
                if self._can_activate_match(match, mutate=False) and self._pending_wave_is_plan_safe(wave, ignored_match_id=match.match_id):
                    self._can_activate_match(match, mutate=True)
                    return match
            if not self._reassign_wave_remaining(wave):
                break
        return None

    def _next_activation_pool(self) -> List[Match]:
        if self.pending_current_wave():
            return self.pending_current_wave()
        return self.pending_prepared_wave()

    def _maybe_prepare_next_wave(self) -> bool:
        if self.state.phase != "SWISS":
            return False
        if self.state.prepared_wave is not None:
            return False
        if not self.state.current_wave:
            return False
        if self.current_wave_complete():
            return False
        if not self._current_wave_ready_for_next_plan():
            return False
        if self.state.wave_index >= SWISS_WAVES_TOTAL:
            return False
        try:
            self._build_wave_plan(self.state.wave_index + 1, relaxed=True, target="prepared")
            return True
        except ValueError:
            # If even the relaxed search cannot produce a stable preview, keep the current wave intact.
            return False

    def _activate_next_match_on_table(self, table: int) -> bool:
        if table in self._used_table_numbers():
            return False
        if table == B_GROUP_TABLE_NUMBER:
            if self._b_group_activate_next_match_if_possible():
                return True
            if table not in self.swiss_table_numbers():
                return False
        match = self._next_resolvable_match_from_wave(self.state.current_wave)
        if match is None and self.state.prepared_wave is not None:
            match = self._next_resolvable_match_from_wave(self.state.prepared_wave)
        if match is None:
            return False
        self._activate_match(match, table)
        return True

    def _activate_match(self, match: Match, table: int) -> None:
        if not self._can_activate_match(match, mutate=True):
            raise ValueError("Dieses Swiss-Match kann noch nicht konfliktfrei gestartet werden.")
        match.status = "active"
        match.table = table
        match.started_ts = self.now()
        self.state.active_tables[table] = match.match_id
        self.state.teams[match.team_a].active_match_id = match.match_id
        self.state.teams[match.team_b].active_match_id = match.match_id
        self.log(f"Tisch {table}: {self.team_name(match.team_a)} vs {self.team_name(match.team_b)} gestartet.")

    def _activate_available_matches_on_free_tables(self) -> int:
        count = 1 if self._b_group_activate_next_match_if_possible() else 0
        for table in list(self.free_tables()):
            if self._activate_next_match_on_table(table):
                count += 1
        return count

    def fill_free_tables(self, relaxed: bool = False) -> int:
        if self.state.phase != "SWISS":
            count = 1 if self._b_group_activate_next_match_if_possible() else 0
            if count:
                self._notify_change()
            return count

        count = 1 if self._b_group_activate_next_match_if_possible() else 0

        if not self.state.current_wave:
            if self.state.wave_index >= SWISS_WAVES_TOTAL:
                if count:
                    self._notify_change()
                return count
            try:
                self._build_wave_plan(self.state.wave_index + 1 if self.state.wave_index else 1, relaxed=relaxed, target="current")
            except ValueError:
                self._build_wave_plan(self.state.wave_index + 1 if self.state.wave_index else 1, relaxed=True, target="current")

        if self.current_wave_complete():
            if self.state.prepared_wave and self._promote_prepared_wave():
                pass
            elif self.state.wave_index < SWISS_WAVES_TOTAL:
                self._build_wave_plan(self.state.wave_index + 1, relaxed=relaxed, target="current")
            else:
                if count:
                    self._notify_change()
                return count

        prepared_created = self._maybe_prepare_next_wave()

        count += self._activate_available_matches_on_free_tables()
        prepared_created = self._maybe_prepare_next_wave() or prepared_created
        if count or prepared_created:
            self._notify_change()
        return count

    def refresh_next_up_plan(self) -> None:
        # deliberately derived from the fixed state; no reshuffling on refresh.
        return

    def _display_match_side(self, match: Match, side: str) -> str:
        team_id = self._resolved_match_side(match, side, mutate=False)
        if team_id is not None:
            return self.team_name(team_id)
        if side == "a":
            return match.placeholder_a or "-"
        return match.placeholder_b or "-"

    def _wave_rows_from(self, wave: Optional[WavePlan]) -> List[dict]:
        rows: List[dict] = []
        if not wave:
            return rows
        for match_id in wave.match_ids:
            match = self.state.matches.get(match_id)
            if not match:
                continue
            if match.status == "active":
                note = f"läuft an Tisch {match.table}"
            elif match.status == "finished":
                note = "fertig"
            elif match.is_placeholder:
                note = "wartet auf Ergebnis"
            elif self._can_activate_match(match, mutate=False):
                note = "bereit"
            elif self._match_is_resolved_rematch(match):
                note = "blockiert (Rematch)"
            else:
                note = "wartet"
            rows.append(
                {
                    "order": match.wave_order,
                    "slot": match.slot_label,
                    "team_a": self._display_match_side(match, "a"),
                    "team_b": self._display_match_side(match, "b"),
                    "status": match.status,
                    "note": note,
                }
            )
        return rows

    def wave_rows(self) -> List[dict]:
        return self._wave_rows_from(self.state.current_wave)

    def prepared_wave_rows(self) -> List[dict]:
        return self._wave_rows_from(self.state.prepared_wave)

    def preview_matches(self) -> List[dict]:
        if self.state.phase != "SWISS":
            return []
        rows: List[dict] = []
        for wave in (self.state.current_wave, self.state.prepared_wave):
            for match in self._pending_matches_from_wave(wave):
                team_a_id, team_b_id = self._resolved_match_pair(match, mutate=False)
                if team_a_id is None or team_b_id is None:
                    continue
                if not self._can_activate_match(match, mutate=False):
                    continue
                rows.append(
                    {
                        "slot": match.slot_label,
                        "team_a": self.team_name(team_a_id),
                        "team_b": self.team_name(team_b_id),
                        "status": "Wir bitten Sie, sich Spielbereit und in Abrufnähe zu halten. Da Verein Dankt.",
                        "wave_order": match.wave_order,
                    }
                )
                if len(rows) >= PREVIEW_MATCHES:
                    return rows[:PREVIEW_MATCHES]
        return rows[:PREVIEW_MATCHES]

    # ------------------------------------------------------------------
    # Results / progression
    # ------------------------------------------------------------------

    def submit_result(self, table: int, winner_team_id: int, is_overtime: bool, loser_cups_hit: int) -> None:
        if table not in self.state.active_tables:
            raise ValueError("Auf diesem Tisch gibt es aktuell kein aktives Match.")

        match_id = self.state.active_tables[table]
        match = self.state.matches[match_id]
        if winner_team_id not in {match.team_a, match.team_b}:
            raise ValueError("Das Sieger-Team gehört nicht zu diesem Tisch.")

        loser_team_id = match.team_b if winner_team_id == match.team_a else match.team_a
        if match.phase == "SWISS":
            self._finish_swiss_match(match, winner_team_id, loser_team_id, is_overtime, loser_cups_hit)
        else:
            self._finish_ko_match(match, winner_team_id, loser_team_id, is_overtime, loser_cups_hit)
        self._notify_change()

    def _finish_swiss_match(
        self,
        match: Match,
        winner_team_id: int,
        loser_team_id: int,
        is_overtime: bool,
        loser_cups_hit: int,
    ) -> None:
        winner_points, loser_points, winner_cup_change, loser_cup_change = self._swiss_result_values(is_overtime, loser_cups_hit)

        winner = self.get_team(winner_team_id)
        loser = self.get_team(loser_team_id)
        finished_ts = self.now()

        winner.swiss_points += winner_points
        loser.swiss_points += loser_points
        winner.cups_metric += winner_cup_change
        loser.cups_metric += loser_cup_change
        winner.swiss_games_played += 1
        loser.swiss_games_played += 1
        winner.last_finish_ts = finished_ts
        loser.last_finish_ts = finished_ts
        winner.active_match_id = None
        loser.active_match_id = None
        winner.opponents.add(loser.id)
        loser.opponents.add(winner.id)

        match.status = "finished"
        match.winner = winner_team_id
        match.loser = loser_team_id
        match.is_overtime = is_overtime
        match.loser_cups_hit = loser_cups_hit
        match.ended_ts = finished_ts

        self.state.completed_match_ids.append(match.match_id)
        if match.table in self.state.active_tables:
            del self.state.active_tables[match.table]

        if is_overtime:
            self.log(
                f"Tisch {match.table}: {winner.name} gewinnt OT gegen {loser.name} ({winner_points}:{loser_points} Punkte, Cups +{winner_cup_change}/0)."
            )
        else:
            self.log(
                f"Tisch {match.table}: {winner.name} gewinnt gegen {loser.name} ({winner_points}:{loser_points} Punkte, Cups +{winner_cup_change}/{loser_cup_change})."
            )

        self._continue_after_finished_swiss_match(freed_table=match.table)

    def _continue_after_finished_swiss_match(self, freed_table: Optional[int]) -> None:
        # Prepare the next wave as preview while the current wave is still running.
        self._maybe_prepare_next_wave()

        if self.current_wave_complete():
            if self.state.prepared_wave and self._promote_prepared_wave():
                pass
            elif self.state.wave_index < SWISS_WAVES_TOTAL:
                try:
                    self._build_wave_plan(self.state.wave_index + 1, relaxed=False, target="current")
                except ValueError:
                    self._build_wave_plan(self.state.wave_index + 1, relaxed=True, target="current")
            else:
                if not self.state.active_tables:
                    self.log("Swiss-Phase abgeschlossen. KO kann jetzt manuell gestartet werden.")

        if freed_table is not None:
            self._activate_available_matches_on_free_tables()
            self._maybe_prepare_next_wave()

    # ------------------------------------------------------------------
    # Knockout
    # ------------------------------------------------------------------

    def start_knockout(self) -> None:
        if self.state.phase != "SWISS":
            raise ValueError("KO kann nur aus der Swiss-Phase gestartet werden.")
        if self.state.active_tables:
            raise ValueError("Bitte erst alle laufenden Swiss-Spiele beenden.")
        if not self.swiss_complete():
            raise ValueError("KO kann erst nach allen Swiss-Spielen gestartet werden.")
        self.state.phase = "KO"
        self.state.prepared_wave = None
        ranking = self.ranking_rows()
        top8 = ranking[:TOP_CUT]
        for idx, row in enumerate(top8, start=1):
            self.state.teams[row["team_id"]].ko_seed = idx

        seed_to_team_id = {idx: row["team_id"] for idx, row in enumerate(top8, start=1)}
        self.state.ko_slots = {
            "QF1": KOSlot("QF1", "QF", "Viertelfinale 1", seed_to_team_id[1], seed_to_team_id[8]),
            "QF2": KOSlot("QF2", "QF", "Viertelfinale 2", seed_to_team_id[2], seed_to_team_id[7]),
            "QF3": KOSlot("QF3", "QF", "Viertelfinale 3", seed_to_team_id[3], seed_to_team_id[6]),
            "QF4": KOSlot("QF4", "QF", "Viertelfinale 4", seed_to_team_id[4], seed_to_team_id[5]),
        }
        self.log("Swiss-Phase abgeschlossen. KO-Phase gestartet.")
        self.schedule_ko_stage("QF")
        self._notify_change()

    def schedule_ko_stage(self, stage: str) -> int:
        pending_slots = [slot for slot in self.state.ko_slots.values() if slot.stage == stage and slot.status == "pending"]
        free_tables = self.free_tables()
        count = 0
        for table, slot in zip(free_tables, pending_slots):
            match_id = self.next_match_id(stage)
            match = Match(
                match_id=match_id,
                phase=stage,
                table=table,
                team_a=slot.team_a or 0,
                team_b=slot.team_b or 0,
                wave_index=None,
                wave_order=None,
                status="active",
                started_ts=self.now(),
                slot_label=slot.slot_id,
            )
            self.state.matches[match_id] = match
            self.state.active_tables[table] = match_id
            self.state.teams[match.team_a].active_match_id = match_id
            self.state.teams[match.team_b].active_match_id = match_id
            slot.status = "active"
            slot.match_id = match_id
            slot.table = table
            self.log(f"Tisch {table}: {slot.label} - {self.team_name(match.team_a)} vs {self.team_name(match.team_b)}")
            count += 1
        if count:
            self._notify_change()
        return count

    def _finish_ko_match(
        self,
        match: Match,
        winner_team_id: int,
        loser_team_id: int,
        is_overtime: bool,
        loser_cups_hit: int,
    ) -> None:
        if is_overtime:
            if loser_cups_hit > 10:
                raise ValueError("es müan immer noch irgendwelche bechers stoh, oh im OT")
        else:
            if loser_cups_hit < 0 or loser_cups_hit > 10:
                raise ValueError("Ohne OT müan noch zwischen 0 und 10 Becher stoh")

        finished_ts = self.now()
        team_a = self.get_team(match.team_a)
        team_b = self.get_team(match.team_b)
        team_a.active_match_id = None
        team_b.active_match_id = None
        team_a.last_finish_ts = finished_ts
        team_b.last_finish_ts = finished_ts

        match.status = "finished"
        match.winner = winner_team_id
        match.loser = loser_team_id
        match.is_overtime = is_overtime
        match.loser_cups_hit = loser_cups_hit
        match.ended_ts = finished_ts
        self.state.completed_match_ids.append(match.match_id)
        if match.table in self.state.active_tables:
            del self.state.active_tables[match.table]

        slot = self.state.ko_slots.get(match.slot_label)
        if slot:
            slot.status = "finished"
            slot.winner = winner_team_id
            slot.loser = loser_team_id
            slot.match_id = match.match_id
            slot.table = match.table

        winner = self.get_team(winner_team_id)
        loser = self.get_team(loser_team_id)
        self.log(f"{slot.label if slot else match.slot_label}: {winner.name} schlägt {loser.name}.")
        self.progress_ko_bracket()

    def progress_ko_bracket(self) -> None:
        if all(self.state.ko_slots.get(slot_id) and self.state.ko_slots[slot_id].status == "finished" for slot_id in ["QF1", "QF2", "QF3", "QF4"]):
            if "SF1" not in self.state.ko_slots:
                self.state.ko_slots["SF1"] = KOSlot(
                    "SF1",
                    "SF",
                    "Halbfinale 1",
                    self.state.ko_slots["QF1"].winner,
                    self.state.ko_slots["QF4"].winner,
                )
                self.state.ko_slots["SF2"] = KOSlot(
                    "SF2",
                    "SF",
                    "Halbfinale 2",
                    self.state.ko_slots["QF2"].winner,
                    self.state.ko_slots["QF3"].winner,
                )
                self.schedule_ko_stage("SF")
                return

        if all(self.state.ko_slots.get(slot_id) and self.state.ko_slots[slot_id].status == "finished" for slot_id in ["SF1", "SF2"]):
            if "FINAL" not in self.state.ko_slots:
                self.state.ko_slots["FINAL"] = KOSlot(
                    "FINAL",
                    "FINAL",
                    "Finale",
                    self.state.ko_slots["SF1"].winner,
                    self.state.ko_slots["SF2"].winner,
                )
                self.state.ko_slots["3RD"] = KOSlot(
                    "3RD",
                    "3RD",
                    "Spiel um Platz 3",
                    self.state.ko_slots["SF1"].loser,
                    self.state.ko_slots["SF2"].loser,
                )
                self.schedule_ko_stage("FINAL")
                self.schedule_ko_stage("3RD")
                return

        if all(self.state.ko_slots.get(slot_id) and self.state.ko_slots[slot_id].status == "finished" for slot_id in ["FINAL", "3RD"]):
            final = self.state.ko_slots["FINAL"]
            third = self.state.ko_slots["3RD"]
            self.state.podium = [final.winner, final.loser, third.winner]
            self.state.top4 = [final.winner, final.loser, third.winner, third.loser]
            self.state.phase = "FINISHED"
            self.log("Turnier beendet. Podest steht fest.")
            self._notify_change()

    def knockout_rows(self) -> List[dict]:
        order = {"QF": 1, "SF": 2, "FINAL": 3, "3RD": 4}
        rows: List[dict] = []
        for slot in sorted(self.state.ko_slots.values(), key=lambda item: (order.get(item.stage, 99), item.label)):
            rows.append(
                {
                    "stage": slot.stage,
                    "label": slot.label,
                    "team_a": self.team_name(slot.team_a) if slot.team_a else "-",
                    "team_b": self.team_name(slot.team_b) if slot.team_b else "-",
                    "status": slot.status,
                    "winner": self.team_name(slot.winner) if slot.winner else "-",
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _waiting_table_numbers(self) -> List[int]:
        if self.state.phase != "SWISS" or not self.state.current_wave:
            return []
        if self.pending_current_wave() or self.current_wave_complete():
            return []
        waiting_tables = []
        used = self._used_table_numbers()
        for table in range(1, TABLE_COUNT + 1):
            if table in used:
                continue
            if table == B_GROUP_TABLE_NUMBER and table not in self.swiss_table_numbers():
                continue
            waiting_tables.append(table)
        return waiting_tables

    def active_matches_rows(self, include_b_group: bool = False, include_waiting: bool = False) -> List[dict]:
        rows: List[dict] = []
        for match in self.active_matches():
            rows.append(
                {
                    "table": match.table,
                    "phase": match.phase,
                    "slot": match.slot_label,
                    "team_a_id": match.team_a,
                    "team_b_id": match.team_b,
                    "team_a": self.team_name(match.team_a),
                    "team_b": self.team_name(match.team_b),
                }
            )
        if include_b_group:
            b_match = self.b_group_active_match()
            if b_match:
                rows.append(
                    {
                        "table": B_GROUP_TABLE_NUMBER,
                        "phase": "B-GRUPPE",
                        "slot": b_match.label,
                        "team_a_id": b_match.team_a,
                        "team_b_id": b_match.team_b,
                        "team_a": self.b_group_team_name(b_match.team_a),
                        "team_b": self.b_group_team_name(b_match.team_b),
                    }
                )
        if include_waiting:
            for table in self._waiting_table_numbers():
                rows.append(
                    {
                        "table": table,
                        "phase": "WAITING",
                        "slot": "",
                        "team_a_id": None,
                        "team_b_id": None,
                        "team_a": "Wartet auf weitere Daten",
                        "team_b": "",
                    }
                )
        rows.sort(key=lambda row: row["table"] or 999)
        return rows

    def progress_text(self) -> str:
        if self.state.phase == "SWISS":
            current_wave = self.state.current_wave.wave_index if self.state.current_wave else 0
            next_wave = self.state.prepared_wave.wave_index if self.state.prepared_wave else None
            suffix = f" | Next vorbereitet: W{next_wave}" if next_wave is not None else ""
            if self.state.current_wave and not self.current_wave_complete() and next_wave is None:
                suffix += f" | Next ab {self._current_wave_prepare_threshold()} fertigen Spielen"
            suffix += f" | {self.b_group_table_status()}"
            return (
                f"Swiss Welle {current_wave}/{SWISS_WAVES_TOTAL} | "
                f"{self.swiss_finished_matches()}/{self.swiss_total_matches()} Spiele beendet | "
                f"aktive Tische: {len(self.active_matches_rows(include_b_group=True))}{suffix}"
            )
        if self.state.phase == "KO":
            finished = sum(1 for slot in self.state.ko_slots.values() if slot.status == "finished")
            total = len(self.state.ko_slots)
            return f"KO {finished}/{total} Slots beendet | aktive Tische: {len(self.active_matches())}"
        if self.state.phase == "FINISHED":
            return "Turnier beendet"
        return "Noch kein Turnier gestartet"
