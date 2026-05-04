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
        if self.pending_current_wave():
            return False
        if self.current_wave_remaining_active() > TABLE_COUNT:
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

    def fill_free_tables(self, relaxed: bool = False) -> int:
        if self.state.phase != "SWISS":
            return 0

        if not self.state.current_wave:
            if self.state.wave_index >= SWISS_WAVES_TOTAL:
                return 0
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
                return 0

        prepared_created = self._maybe_prepare_next_wave()

        free_tables = self.free_tables()
        count = 0
        for table in free_tables:
            if self._activate_next_match_on_table(table):
                count += 1
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
                        "status": "Macht euch bereit",
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
                    self.start_knockout()

        if freed_table is not None:
            self._activate_next_match_on_table(freed_table)
            self._maybe_prepare_next_wave()

    # ------------------------------------------------------------------
    # Knockout
    # ------------------------------------------------------------------

    def start_knockout(self) -> None:
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
            next_wave = self.state.prepared_wave.wave_index if self.state.prepared_wave else None
            suffix = f" | Next vorbereitet: W{next_wave}" if next_wave is not None else ""
            return (
                f"Swiss Welle {current_wave}/{SWISS_WAVES_TOTAL} | "
                f"{self.swiss_finished_matches()}/{self.swiss_total_matches()} Spiele beendet | "
                f"aktive Tische: {len(self.active_matches())}{suffix}"
            )
        if self.state.phase == "KO":
            finished = sum(1 for slot in self.state.ko_slots.values() if slot.status == "finished")
            total = len(self.state.ko_slots)
            return f"KO {finished}/{total} Slots beendet | aktive Tische: {len(self.active_matches())}"
        if self.state.phase == "FINISHED":
            return "Turnier beendet"
        return "Noch kein Turnier gestartet"
