from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from core.config import (
    DEFAULT_GAME_CAP,
    DEFAULT_POINT_CAP,
    LOG_LIMIT,
    PHASE_LABELS,
    PREVIEW_MATCHES,
    SWISS_GAMES_PER_TEAM,
    SWISS_MATCHES_PER_WAVE,
    SWISS_WAVES_TOTAL,
    TABLE_COUNT,
    TEAM_COUNT,
    TOP_CUT,
)
from core.models import KOSlot, Match, Team, TournamentState, WavePlan
from core.scheduler import PairSuggestion, SoftSwissScheduler


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
        return self.state.teams[team_id].name

    def get_team(self, team_id: int) -> Team:
        return self.state.teams[team_id]

    def free_tables(self) -> List[int]:
        used = {match.table for match in self.active_matches() if match.table is not None}
        return [table for table in range(1, TABLE_COUNT + 1) if table not in used]

    def active_matches(self) -> List[Match]:
        return sorted(
            [match for match in self.state.matches.values() if match.status == "active" and match.table is not None],
            key=lambda item: item.table or 999,
        )

    def current_wave_matches(self) -> List[Match]:
        if not self.state.current_wave:
            return []
        return [self.state.matches[mid] for mid in self.state.current_wave.match_ids if mid in self.state.matches]

    def pending_current_wave(self) -> List[Match]:
        return [match for match in self.current_wave_matches() if match.status == "pending"]

    def current_wave_complete(self) -> bool:
        wave_matches = self.current_wave_matches()
        return bool(wave_matches) and all(match.status == "finished" for match in wave_matches)

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

    def swiss_complete(self) -> bool:
        return bool(self.state.teams) and all(team.swiss_games_played >= SWISS_GAMES_PER_TEAM for team in self.state.teams.values())

    # ------------------------------------------------------------------
    # Tournament setup
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.state = TournamentState()

    def new_tournament(self, team_names: List[str]) -> None:
        clean_names = [name.strip() for name in team_names if name.strip()]
        if len(clean_names) != TEAM_COUNT:
            raise ValueError(f"Es müssen genau {TEAM_COUNT} Teamnamen eingegeben werden.")

        self.reset()
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

    def _build_wave_plan(self, wave_index: int, relaxed: bool = False) -> None:
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

        wave = WavePlan(wave_index=wave_index, created_ts=self.now(), status="active")
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
            )
            self.state.matches[match_id] = match
            wave.match_ids.append(match_id)

        self.state.current_wave = wave
        self.state.wave_index = wave_index
        self.log(f"Welle {wave_index} vorbereitet ({len(wave.match_ids)} Matches).")

    def _activate_match(self, match: Match, table: int) -> None:
        match.status = "active"
        match.table = table
        match.started_ts = self.now()
        self.state.active_tables[table] = match.match_id
        self.state.teams[match.team_a].active_match_id = match.match_id
        self.state.teams[match.team_b].active_match_id = match.match_id
        self.log(f"Tisch {table}: {self.team_name(match.team_a)} vs {self.team_name(match.team_b)} gestartet.")

    def fill_free_tables(self, relaxed: bool = False) -> int:
        if self.state.phase != "SWISS":
            return 0

        if not self.state.current_wave:
            if self.state.wave_index >= SWISS_WAVES_TOTAL:
                return 0
            self._build_wave_plan(self.state.wave_index + 1 if self.state.wave_index else 1, relaxed=relaxed)

        if self.current_wave_complete():
            if self.state.wave_index >= SWISS_WAVES_TOTAL:
                return 0
            self._build_wave_plan(self.state.wave_index + 1, relaxed=relaxed)

        free_tables = self.free_tables()
        pending = self.pending_current_wave()
        count = 0
        for table, match in zip(free_tables, pending):
            self._activate_match(match, table)
            count += 1
        if count:
            self._notify_change()
        return count

    def refresh_next_up_plan(self) -> None:
        # deliberately derived from the fixed current wave; no reshuffling on refresh.
        return

    def preview_matches(self) -> List[dict]:
        if self.state.phase != "SWISS" or not self.state.current_wave:
            return []
        rows: List[dict] = []
        for match in self.pending_current_wave()[:PREVIEW_MATCHES]:
            rows.append(
                {
                    "slot": match.slot_label,
                    "team_a": self.team_name(match.team_a),
                    "team_b": self.team_name(match.team_b),
                    "status": "wartet auf freien Tisch",
                    "wave_order": match.wave_order,
                }
            )
        # If fewer than PREVIEW_MATCHES pending remain, also show currently active matches in order,
        # so the screen always displays two usable next-match entries.
        if len(rows) < PREVIEW_MATCHES:
            for match in self.active_matches():
                if len(rows) >= PREVIEW_MATCHES:
                    break
                rows.append(
                    {
                        "slot": match.slot_label,
                        "team_a": self.team_name(match.team_a),
                        "team_b": self.team_name(match.team_b),
                        "status": f"läuft an Tisch {match.table}",
                        "wave_order": match.wave_order,
                    }
                )
        return rows[:PREVIEW_MATCHES]

    def wave_rows(self) -> List[dict]:
        rows: List[dict] = []
        if not self.state.current_wave:
            return rows
        for match in self.current_wave_matches():
            if match.status == "active":
                note = f"läuft an Tisch {match.table}"
            elif match.status == "finished":
                note = "fertig"
            else:
                note = "wartet auf freien Tisch"
            rows.append(
                {
                    "order": match.wave_order,
                    "slot": match.slot_label,
                    "team_a": self.team_name(match.team_a),
                    "team_b": self.team_name(match.team_b),
                    "status": match.status,
                    "note": note,
                }
            )
        return rows

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
        if is_overtime:
            if loser_cups_hit < 10 or loser_cups_hit > 12:
                raise ValueError("In OT muss der Verlierer zwischen 10 und 12 Becher getroffen haben.")
            winner_points, loser_points = 2, 1
            winner_cup_change = 13 - loser_cups_hit
            loser_cup_change = 0
        else:
            if loser_cups_hit < 0 or loser_cups_hit > 9:
                raise ValueError("Ohne OT muss der Verlierer zwischen 0 und 9 Becher getroffen haben.")
            winner_points, loser_points = 3, 0
            winner_cup_change = 10 - loser_cups_hit
            loser_cup_change = -winner_cup_change

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
        if self.current_wave_complete():
            if self.state.wave_index < SWISS_WAVES_TOTAL:
                self._build_wave_plan(self.state.wave_index + 1, relaxed=False)
                self.fill_free_tables(relaxed=False)
            else:
                if not self.state.active_tables:
                    self.start_knockout()
            return

        if freed_table is not None and self.state.current_wave:
            pending = self.pending_current_wave()
            if pending:
                self._activate_match(pending[0], freed_table)

    # ------------------------------------------------------------------
    # Knockout
    # ------------------------------------------------------------------

    def start_knockout(self) -> None:
        self.state.phase = "KO"
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
            if loser_cups_hit < 10 or loser_cups_hit > 12:
                raise ValueError("In OT muss der Verlierer zwischen 10 und 12 Becher getroffen haben.")
        else:
            if loser_cups_hit < 0 or loser_cups_hit > 9:
                raise ValueError("Ohne OT muss der Verlierer zwischen 0 und 9 Becher getroffen haben.")

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

    def active_matches_rows(self) -> List[dict]:
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
        return rows

    def progress_text(self) -> str:
        if self.state.phase == "SWISS":
            current_wave = self.state.current_wave.wave_index if self.state.current_wave else 0
            return (
                f"Swiss Welle {current_wave}/{SWISS_WAVES_TOTAL} | "
                f"{self.swiss_finished_matches()}/{self.swiss_total_matches()} Spiele beendet | "
                f"aktive Tische: {len(self.active_matches())}"
            )
        if self.state.phase == "KO":
            finished = sum(1 for slot in self.state.ko_slots.values() if slot.status == "finished")
            total = len(self.state.ko_slots)
            return f"KO {finished}/{total} Slots beendet | aktive Tische: {len(self.active_matches())}"
        if self.state.phase == "FINISHED":
            return "Turnier beendet"
        return "Noch kein Turnier gestartet"
