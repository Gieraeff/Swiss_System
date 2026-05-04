from __future__ import annotations

import time
from typing import Dict, List, Optional

from core.config import (
    DEFAULT_SWISS_GAME_CAP,
    DEFAULT_SWISS_POINT_CAP,
    LOG_LIMIT,
    PHASE_LABELS,
    PREVIEW_MATCHES,
    SWISS_GAMES_PER_TEAM,
    TABLE_COUNT,
    TEAM_COUNT,
    TOP_CUT,
)
from core.models import KOSlot, Match, Team, TournamentState
from core.scheduler import PairSuggestion, SoftSwissScheduler


class SwissTournamentEngine:
    def __init__(self, scheduler: Optional[SoftSwissScheduler] = None) -> None:
        self.scheduler = scheduler or SoftSwissScheduler()
        self.state = TournamentState()

    def reset(self) -> None:
        self.state = TournamentState()

    def refresh_next_up_plan(self, relaxed: bool = False) -> None:
        if self.state.phase != "SWISS":
            self.state.next_up_plan = []
            self.state.next_up_updated_ts = self.now()
            return

        limit = PREVIEW_MATCHES
        suggestions = self.scheduler.preview_bundle(self.state, limit, reference_ts=self.now())
        self.state.next_up_plan = [
            {
                "team_a_id": s.team_a_id,
                "team_b_id": s.team_b_id,
                "point_cap_used": s.point_cap_used,
                "game_cap_used": s.game_cap_used,
                "penalty": s.penalty,
            }
            for s in suggestions[:PREVIEW_MATCHES]
        ]
        self.state.next_up_updated_ts = self.now()

    def now(self) -> float:
        return time.time()

    def phase_label(self) -> str:
        return PHASE_LABELS.get(self.state.phase, self.state.phase)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.state.logs.append(f"[{stamp}] {message}")
        self.state.logs = self.state.logs[-LOG_LIMIT:]

    def next_match_id(self, prefix: str) -> str:
        match_id = f"{prefix}-{self.state.match_counter:03d}"
        self.state.match_counter += 1
        return match_id

    def team_name(self, team_id: int) -> str:
        return self.state.teams[team_id].name

    def get_team(self, team_id: int) -> Team:
        return self.state.teams[team_id]

    def swiss_total_matches(self) -> int:
        if not self.state.teams:
            return 0
        return (len(self.state.teams) * SWISS_GAMES_PER_TEAM) // 2

    def swiss_finished_matches(self) -> int:
        return sum(1 for match in self.state.completed_matches if match.phase == "SWISS")

    def active_team_ids(self) -> set[int]:
        active_ids: set[int] = set()
        for match in self.state.active_matches.values():
            active_ids.add(match.team_a)
            active_ids.add(match.team_b)
        return active_ids

    def free_tables(self) -> List[int]:
        used = set(self.state.active_matches.keys())
        return [table for table in range(1, TABLE_COUNT + 1) if table not in used]

    def get_buchholz_map(self) -> Dict[int, int]:
        result: Dict[int, int] = {}
        for team in self.state.teams.values():
            result[team.id] = sum(self.state.teams[opp_id].swiss_points for opp_id in team.opponents)
        return result

    def ranking(self) -> List[Team]:
        buchholz = self.get_buchholz_map()
        return sorted(
            self.state.teams.values(),
            key=lambda team: (
                -team.swiss_points,
                -team.cups_metric,
                -buchholz[team.id],
                team.seed,
            ),
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
        return bool(self.state.teams) and all(
            team.swiss_games_played >= SWISS_GAMES_PER_TEAM for team in self.state.teams.values()
        )

    def new_tournament(self, team_names: List[str]) -> None:
        clean_names = [name.strip() for name in team_names if name.strip()]
        if len(clean_names) != TEAM_COUNT:
            raise ValueError(f"Es werden genau {TEAM_COUNT} Teamnamen benötigt.")
        if len(set(clean_names)) != len(clean_names):
            raise ValueError("Die Teamnamen müssen eindeutig sein.")

        self.reset()
        self.state.phase = "SWISS"
        self.state.started_at = self.now()

        for idx, name in enumerate(clean_names, start=1):
            self.state.teams[idx] = Team(
                id=idx,
                name=name,
                seed=idx,
                last_finish_ts=self.state.started_at,
            )

        self.log("Neues Turnier gestartet.")
        self.fill_free_tables(relaxed=False)
        self.refresh_next_up_plan(relaxed=False)

    def fill_free_tables(self, relaxed: bool = False) -> int:
        if self.state.phase != "SWISS":
            return 0

        free_tables = self.free_tables()
        if not free_tables:
            return 0

        suggestions, point_cap_used, game_cap_used = self.scheduler.best_bundle(
            self.state,
            len(free_tables),
            relaxed=relaxed,
        )

        if not suggestions:
            if relaxed:
                self.log("Erweiterte Suche fand aktuell keine zusätzliche Paarung.")
            else:
                self.log(
                    "Kein reguläres Swiss-Match für freie Tische gefunden. Warten oder manuell 'Erweiterte Suche' nutzen."
                )
            self.refresh_next_up_plan(relaxed=relaxed)
            return 0

        for table, suggestion in zip(free_tables, suggestions):
            self.activate_swiss_match(
                table,
                suggestion.team_a_id,
                suggestion.team_b_id,
                suggestion.point_cap_used,
                suggestion.game_cap_used,
            )

        if point_cap_used > DEFAULT_SWISS_POINT_CAP or game_cap_used > DEFAULT_SWISS_GAME_CAP:
            self.log(
                f"Paarung leicht gelockert: Punkte-Cap {point_cap_used}, Spiele-Cap {game_cap_used}."
            )

        self.refresh_next_up_plan(relaxed=relaxed)
        return len(suggestions)

    def activate_swiss_match(
        self,
        table: int,
        team_a_id: int,
        team_b_id: int,
        point_cap_used: int,
        game_cap_used: int,
    ) -> Match:
        match = Match(
            match_id=self.next_match_id("SW"),
            phase="SWISS",
            slot_label="Swiss",
            table=table,
            team_a=team_a_id,
            team_b=team_b_id,
            status="active",
            started_ts=self.now(),
            point_cap_used=point_cap_used,
            game_cap_used=game_cap_used,
        )
        self.state.active_matches[table] = match
        self.state.teams[team_a_id].active_match_id = match.match_id
        self.state.teams[team_b_id].active_match_id = match.match_id
        self.log(
            f"Tisch {table}: {self.team_name(team_a_id)} vs {self.team_name(team_b_id)} gestartet."
        )
        return match

    def preview_matches(self, limit: int = PREVIEW_MATCHES) -> List[dict]:
        if self.state.phase == "SWISS":
            if not self.state.next_up_plan:
                self.refresh_next_up_plan(relaxed=False)
            rows: List[dict] = []
            for idx, item in enumerate(self.state.next_up_plan[:limit], start=1):
                team_a = self.get_team(int(item["team_a_id"]))
                team_b = self.get_team(int(item["team_b_id"]))
                rows.append(
                    {
                        "index": idx,
                        "phase": "SWISS",
                        "label": f"Next {idx}",
                        "team_a": team_a.name,
                        "team_b": team_b.name,
                        "points": f"{team_a.swiss_points}:{team_b.swiss_points}",
                        "games": f"{team_a.swiss_games_played}:{team_b.swiss_games_played}",
                        "caps": f"P{item["point_cap_used"]}/G{item["game_cap_used"]}",
                    }
                )
            return rows

        if self.state.phase in {"KO", "FINISHED"}:
            order = {"QF": 1, "SF": 2, "FINAL": 3, "3RD": 4}
            pending = [
                slot
                for slot in self.state.ko_slots.values()
                if slot.status == "pending" and slot.team_a is not None and slot.team_b is not None
            ]
            pending.sort(key=lambda slot: (order.get(slot.stage, 99), slot.label))
            rows = []
            for idx, slot in enumerate(pending[:limit], start=1):
                rows.append(
                    {
                        "index": idx,
                        "phase": slot.stage,
                        "label": slot.label,
                        "team_a": self.team_name(slot.team_a),
                        "team_b": self.team_name(slot.team_b),
                        "points": "-",
                        "games": "-",
                        "caps": "-",
                    }
                )
            return rows

        return []

    def submit_result(self, table: int, winner_team_id: int, is_overtime: bool, loser_cups_hit: int) -> None:
        if table not in self.state.active_matches:
            raise ValueError("Auf diesem Tisch gibt es aktuell kein aktives Match.")

        match = self.state.active_matches[table]
        if winner_team_id not in {match.team_a, match.team_b}:
            raise ValueError("Das Sieger-Team gehört nicht zu diesem Tisch.")

        loser_team_id = match.team_b if winner_team_id == match.team_a else match.team_a
        if match.phase == "SWISS":
            self._finish_swiss_match(match, winner_team_id, loser_team_id, is_overtime, loser_cups_hit)
        else:
            self._finish_ko_match(match, winner_team_id, loser_team_id, is_overtime, loser_cups_hit)

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

        del self.state.active_matches[match.table]
        self.state.completed_matches.append(match)

        if is_overtime:
            self.log(
                f"Tisch {match.table}: {winner.name} gewinnt OT gegen {loser.name} ({winner_points}:{loser_points} Punkte, Cups +{winner_cup_change}/0)."
            )
        else:
            self.log(
                f"Tisch {match.table}: {winner.name} gewinnt gegen {loser.name} ({winner_points}:{loser_points} Punkte, Cups +{winner_cup_change}/{loser_cup_change})."
            )

        if self.swiss_complete() and not self.state.active_matches:
            self.start_knockout()
            self.state.next_up_plan = []
        else:
            self.fill_free_tables(relaxed=False)
            self.refresh_next_up_plan(relaxed=False)

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

        del self.state.active_matches[match.table]
        self.state.completed_matches.append(match)

        slot = self.state.ko_slots[match.slot_label]
        slot.status = "finished"
        slot.winner = winner_team_id
        slot.loser = loser_team_id
        slot.match_id = match.match_id
        slot.table = match.table

        winner = self.get_team(winner_team_id)
        loser = self.get_team(loser_team_id)
        self.log(f"{slot.label}: {winner.name} schlägt {loser.name}.")
        self.progress_ko_bracket()

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

    def schedule_ko_stage(self, stage: str) -> int:
        pending_slots = [
            slot
            for slot in self.state.ko_slots.values()
            if slot.stage == stage and slot.status == "pending" and slot.team_a and slot.team_b
        ]
        if not pending_slots:
            return 0

        free_tables = self.free_tables()
        count = 0
        for table, slot in zip(free_tables, pending_slots):
            match = Match(
                match_id=self.next_match_id(stage),
                phase=stage,
                slot_label=slot.slot_id,
                table=table,
                team_a=slot.team_a,
                team_b=slot.team_b,
                status="active",
                started_ts=self.now(),
            )
            self.state.active_matches[table] = match
            self.state.teams[slot.team_a].active_match_id = match.match_id
            self.state.teams[slot.team_b].active_match_id = match.match_id
            slot.status = "active"
            slot.match_id = match.match_id
            slot.table = table
            self.log(
                f"Tisch {table}: {slot.label} - {self.team_name(slot.team_a)} vs {self.team_name(slot.team_b)}"
            )
            count += 1
        return count

    def progress_ko_bracket(self) -> None:
        if all(
            slot_id in self.state.ko_slots and self.state.ko_slots[slot_id].status == "finished"
            for slot_id in ["QF1", "QF2", "QF3", "QF4"]
        ):
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

        if all(
            slot_id in self.state.ko_slots and self.state.ko_slots[slot_id].status == "finished"
            for slot_id in ["SF1", "SF2"]
        ):
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

        if all(
            slot_id in self.state.ko_slots and self.state.ko_slots[slot_id].status == "finished"
            for slot_id in ["FINAL", "3RD"]
        ):
            final = self.state.ko_slots["FINAL"]
            third = self.state.ko_slots["3RD"]
            self.state.podium = [final.winner, final.loser, third.winner]
            self.state.top4 = [final.winner, final.loser, third.winner, third.loser]
            self.state.phase = "FINISHED"
            self.state.next_up_plan = []
            self.log("Turnier beendet. Podest steht fest.")

    def active_matches_rows(self) -> List[dict]:
        rows = []
        for table, match in sorted(self.state.active_matches.items()):
            rows.append(
                {
                    "table": table,
                    "phase": match.phase,
                    "slot": match.slot_label,
                    "team_a_id": match.team_a,
                    "team_b_id": match.team_b,
                    "team_a": self.team_name(match.team_a),
                    "team_b": self.team_name(match.team_b),
                }
            )
        return rows

    def knockout_rows(self) -> List[dict]:
        order = {"QF": 1, "SF": 2, "FINAL": 3, "3RD": 4}
        rows = []
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

    def progress_text(self) -> str:
        if self.state.phase == "SWISS":
            return (
                f"Swiss {self.swiss_finished_matches()}/{self.swiss_total_matches()} Spiele | "
                f"aktive Tische: {len(self.state.active_matches)}"
            )
        if self.state.phase == "KO":
            finished = sum(1 for slot in self.state.ko_slots.values() if slot.status == "finished")
            total = len(self.state.ko_slots)
            return f"KO {finished}/{total} Slots beendet | aktive Tische: {len(self.state.active_matches)}"
        if self.state.phase == "FINISHED":
            return "Turnier beendet"
        return "Noch kein Turnier gestartet"
