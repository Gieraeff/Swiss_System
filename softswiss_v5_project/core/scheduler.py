from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from core.config import DEFAULT_GAME_CAP, DEFAULT_POINT_CAP, RELAXED_GAME_CAPS, RELAXED_POINT_CAPS, SWISS_GAMES_PER_TEAM
from core.models import Match, Team, TournamentState


@dataclass(frozen=True)
class PairSuggestion:
    team_a_id: int
    team_b_id: int
    point_cap_used: int
    game_cap_used: int
    penalty: float


@dataclass(frozen=True)
class SlotParticipant:
    key: str
    label: str
    team_id: Optional[int]
    possible_team_ids: Tuple[int, ...]
    swiss_points: int
    cups_metric: int
    swiss_games_played: int
    seed: int
    source_match_id: Optional[str] = None
    source_outcome: Optional[str] = None

    @property
    def is_placeholder(self) -> bool:
        return self.team_id is None


@dataclass(frozen=True)
class WaveSlotSuggestion:
    participant_a: SlotParticipant
    participant_b: SlotParticipant
    point_cap_used: int
    game_cap_used: int
    penalty: float


class SoftSwissScheduler:
    def now(self) -> float:
        return time.time()

    def team_wait_minutes(self, team: Team, now_ts: float | None = None) -> float:
        if now_ts is None:
            now_ts = self.now()
        return max(0.0, (now_ts - team.last_finish_ts) / 60.0)

    def buchholz_map(self, teams: List[Team]) -> Dict[int, int]:
        by_id = {team.id: team for team in teams}
        result: Dict[int, int] = {}
        for team in teams:
            result[team.id] = sum(by_id[opp_id].swiss_points for opp_id in team.opponents if opp_id in by_id)
        return result

    def concrete_participant(self, team: Team) -> SlotParticipant:
        return SlotParticipant(
            key=f"T{team.id}",
            label=team.name,
            team_id=team.id,
            possible_team_ids=(team.id,),
            swiss_points=team.swiss_points,
            cups_metric=team.cups_metric,
            swiss_games_played=team.swiss_games_played,
            seed=team.seed,
        )

    def outcome_participant(self, match: Match, state: TournamentState, outcome: str) -> SlotParticipant:
        team_a = state.teams[match.team_a]
        team_b = state.teams[match.team_b]
        table_label = f"Tisch {match.table}" if match.table is not None else match.slot_label
        if outcome == "winner":
            label = f"Sieger von {table_label}"
            projected_points = max(team_a.swiss_points, team_b.swiss_points) + 3
            projected_cups = max(team_a.cups_metric, team_b.cups_metric) + 6
            projected_seed = min(team_a.seed, team_b.seed)
        else:
            label = f"Verlierer von {table_label}"
            projected_points = min(team_a.swiss_points, team_b.swiss_points)
            projected_cups = min(team_a.cups_metric, team_b.cups_metric) - 6
            projected_seed = max(team_a.seed, team_b.seed)

        return SlotParticipant(
            key=f"{match.match_id}:{outcome}",
            label=label,
            team_id=None,
            possible_team_ids=(match.team_a, match.team_b),
            swiss_points=projected_points,
            cups_metric=projected_cups,
            swiss_games_played=max(team_a.swiss_games_played, team_b.swiss_games_played) + 1,
            seed=projected_seed,
            source_match_id=match.match_id,
            source_outcome=outcome,
        )

    def _slot_pair_key(self, a_key: str, b_key: str) -> Tuple[str, str]:
        return (a_key, b_key) if a_key < b_key else (b_key, a_key)

    def _slot_pair_has_legal_assignment(self, a: SlotParticipant, b: SlotParticipant, state: TournamentState) -> bool:
        if a.source_match_id and a.source_match_id == b.source_match_id:
            return False
        for a_id in a.possible_team_ids:
            team_a = state.teams.get(a_id)
            if not team_a:
                continue
            for b_id in b.possible_team_ids:
                if a_id == b_id:
                    continue
                if b_id not in team_a.opponents:
                    return True
        return False

    def _valid_slot_pair(
        self,
        a: SlotParticipant,
        b: SlotParticipant,
        state: TournamentState,
        point_cap: int,
        game_cap: int,
    ) -> bool:
        if not self._slot_pair_has_legal_assignment(a, b, state):
            return False
        if abs(a.swiss_points - b.swiss_points) > point_cap:
            return False
        if abs(a.swiss_games_played - b.swiss_games_played) > game_cap:
            return False
        return True

    def slot_pair_penalty(
        self,
        a: SlotParticipant,
        b: SlotParticipant,
        now_ts: float,
        point_cap: int,
        game_cap: int,
    ) -> float:
        point_diff = abs(a.swiss_points - b.swiss_points)
        cup_diff = abs(a.cups_metric - b.cups_metric)
        game_diff = abs(a.swiss_games_played - b.swiss_games_played)
        seed_diff = abs(a.seed - b.seed)
        wait_sum = 0.0

        score = (
            point_diff * 1000.0
            + cup_diff * 100.0
            + game_diff * 80.0
            + seed_diff * 1.0
            - wait_sum * 6.0
        )
        if a.is_placeholder or b.is_placeholder:
            score += 35.0
        score += point_cap * 0.5 + game_cap * 0.5
        return score

    def _valid_pair(self, a: Team, b: Team, point_cap: int, game_cap: int) -> bool:
        if b.id in a.opponents:
            return False
        if abs(a.swiss_points - b.swiss_points) > point_cap:
            return False
        if abs(a.swiss_games_played - b.swiss_games_played) > game_cap:
            return False
        return True

    def pair_penalty(
        self,
        a: Team,
        b: Team,
        buchholz: Dict[int, int],
        now_ts: float,
        point_cap: int,
        game_cap: int,
    ) -> float:
        point_diff = abs(a.swiss_points - b.swiss_points)
        cup_diff = abs(a.cups_metric - b.cups_metric)
        game_diff = abs(a.swiss_games_played - b.swiss_games_played)
        bh_diff = abs(buchholz.get(a.id, 0) - buchholz.get(b.id, 0))
        seed_diff = abs(a.seed - b.seed)
        wait_sum = self.team_wait_minutes(a, now_ts) + self.team_wait_minutes(b, now_ts)

        # Punkte > Cups > Wartezeit > Buchholz > Seed
        score = (
            point_diff * 1000.0
            + cup_diff * 100.0
            + game_diff * 80.0
            + bh_diff * 10.0
            + seed_diff * 1.0
            - wait_sum * 6.0
        )
        # Slight preference for tighter caps if multiple solutions exist.
        score += point_cap * 0.5 + game_cap * 0.5
        return score

    def _solve_best_bundle(
        self,
        teams: List[Team],
        table_target: int,
        point_cap: int,
        game_cap: int,
        now_ts: float,
    ) -> Tuple[int, float, Tuple[Tuple[int, int], ...]]:
        if len(teams) < 2:
            return 0, 0.0, tuple()

        buchholz = self.buchholz_map(teams)
        team_map = {team.id: team for team in teams}

        pair_penalties: Dict[Tuple[int, int], float] = {}
        for a, b in combinations(teams, 2):
            if not self._valid_pair(a, b, point_cap, game_cap):
                continue
            pair_penalties[(a.id, b.id)] = self.pair_penalty(a, b, buchholz, now_ts, point_cap, game_cap)

        if not pair_penalties:
            return 0, float("inf"), tuple()

        ids = tuple(sorted(team_map.keys(), key=lambda tid: (team_map[tid].swiss_points, team_map[tid].cups_metric, -self.team_wait_minutes(team_map[tid], now_ts), -buchholz[tid], team_map[tid].seed), reverse=True))

        def pair_score(a_id: int, b_id: int) -> float:
            key = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            return pair_penalties[key]

        @lru_cache(maxsize=None)
        def solve(remaining: Tuple[int, ...], slots_left: int) -> Tuple[int, float, Tuple[Tuple[int, int], ...]]:
            if slots_left == 0:
                return 0, 0.0, tuple()
            if len(remaining) < slots_left * 2:
                return -10**9, float("inf"), tuple()
            if len(remaining) < 2:
                return -10**9, float("inf"), tuple()

            first = remaining[0]
            best = solve(remaining[1:], slots_left) if len(remaining) - 1 >= (slots_left - 1) * 2 else (-10**9, float("inf"), tuple())

            candidates: List[Tuple[float, int]] = []
            for idx in range(1, len(remaining)):
                second = remaining[idx]
                key = (first, second) if first < second else (second, first)
                if key not in pair_penalties:
                    continue
                candidates.append((pair_penalties[key], idx))

            candidates.sort(key=lambda item: item[0])
            # Try the best candidates first; enough for 24 teams and improves stability.
            for penalty, idx in candidates[:12]:
                second = remaining[idx]
                next_remaining = remaining[1:idx] + remaining[idx + 1 :]
                sub_count, sub_score, sub_pairs = solve(next_remaining, slots_left - 1)
                if sub_count < 0:
                    continue
                candidate = (sub_count + 1, sub_score + penalty, ((min(first, second), max(first, second)),) + sub_pairs)
                if candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                    best = candidate

            return best

        return solve(ids, table_target)

    def _greedy_bundle(
        self,
        teams: List[Team],
        table_target: int,
        point_cap: int,
        game_cap: int,
        now_ts: float,
    ) -> Tuple[List[Tuple[int, int]], Dict[Tuple[int, int], float]]:
        buchholz = self.buchholz_map(teams)
        team_map = {team.id: team for team in teams}
        available = tuple(sorted(
            team_map.keys(),
            key=lambda tid: (
                team_map[tid].swiss_points,
                team_map[tid].cups_metric,
                -self.team_wait_minutes(team_map[tid], now_ts),
                -buchholz[tid],
                team_map[tid].seed,
            ),
            reverse=True,
        ))
        remaining = list(available)
        pairs: List[Tuple[int, int]] = []
        penalties: Dict[Tuple[int, int], float] = {}

        while len(remaining) >= 2 and len(pairs) < table_target:
            first = remaining[0]
            candidates: List[Tuple[float, int, int]] = []
            for idx in range(1, len(remaining)):
                second = remaining[idx]
                a = team_map[first]
                b = team_map[second]
                if b.id in a.opponents:
                    continue
                penalty = self.pair_penalty(a, b, buchholz, now_ts, point_cap, game_cap)
                # In the fallback we soften the hard caps and only punish larger gaps.
                penalty += max(0, abs(a.swiss_points - b.swiss_points) - point_cap) * 1200.0
                penalty += max(0, abs(a.swiss_games_played - b.swiss_games_played) - game_cap) * 900.0
                candidates.append((penalty, idx, second))

            if not candidates:
                # No legal partner in the fallback pool. This should be rare.
                break

            candidates.sort(key=lambda item: item[0])
            penalty, idx, second = candidates[0]
            a_id, b_id = (first, second) if first < second else (second, first)
            pairs.append((a_id, b_id))
            penalties[(a_id, b_id)] = penalty
            del remaining[idx]
            del remaining[0]

        return pairs, penalties

    def _solve_best_slot_bundle(
        self,
        state: TournamentState,
        participants: List[SlotParticipant],
        table_target: int,
        point_cap: int,
        game_cap: int,
        now_ts: float,
    ) -> Tuple[int, float, Tuple[Tuple[str, str], ...]]:
        if len(participants) < 2:
            return 0, 0.0, tuple()

        participant_map = {participant.key: participant for participant in participants}
        pair_penalties: Dict[Tuple[str, str], float] = {}
        for idx, participant_a in enumerate(participants):
            for participant_b in participants[idx + 1 :]:
                if not self._valid_slot_pair(participant_a, participant_b, state, point_cap, game_cap):
                    continue
                pair_key = self._slot_pair_key(participant_a.key, participant_b.key)
                pair_penalties[pair_key] = self.slot_pair_penalty(participant_a, participant_b, now_ts, point_cap, game_cap)

        if not pair_penalties:
            return 0, float("inf"), tuple()

        ids = tuple(
            sorted(
                participant_map.keys(),
                key=lambda key: (
                    participant_map[key].swiss_points,
                    participant_map[key].cups_metric,
                    -participant_map[key].swiss_games_played,
                    -participant_map[key].seed,
                ),
                reverse=True,
            )
        )

        def pair_score(a_key: str, b_key: str) -> float:
            return pair_penalties[self._slot_pair_key(a_key, b_key)]

        @lru_cache(maxsize=None)
        def solve(remaining: Tuple[str, ...], slots_left: int) -> Tuple[int, float, Tuple[Tuple[str, str], ...]]:
            if slots_left == 0:
                return 0, 0.0, tuple()
            if len(remaining) < slots_left * 2:
                return -10**9, float("inf"), tuple()
            if len(remaining) < 2:
                return -10**9, float("inf"), tuple()

            first = remaining[0]
            best = solve(remaining[1:], slots_left) if len(remaining) - 1 >= (slots_left - 1) * 2 else (-10**9, float("inf"), tuple())

            candidates: List[Tuple[float, int]] = []
            for idx in range(1, len(remaining)):
                second = remaining[idx]
                pair_key = self._slot_pair_key(first, second)
                if pair_key not in pair_penalties:
                    continue
                candidates.append((pair_penalties[pair_key], idx))

            candidates.sort(key=lambda item: item[0])
            for penalty, idx in candidates[:14]:
                second = remaining[idx]
                next_remaining = remaining[1:idx] + remaining[idx + 1 :]
                sub_count, sub_score, sub_pairs = solve(next_remaining, slots_left - 1)
                if sub_count < 0:
                    continue
                pair = self._slot_pair_key(first, second)
                candidate = (sub_count + 1, sub_score + penalty, (pair,) + sub_pairs)
                if candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                    best = candidate

            return best

        return solve(ids, table_target)

    def _greedy_slot_bundle(
        self,
        state: TournamentState,
        participants: List[SlotParticipant],
        table_target: int,
        point_cap: int,
        game_cap: int,
        now_ts: float,
    ) -> Tuple[List[Tuple[str, str]], Dict[Tuple[str, str], float]]:
        remaining = list(
            sorted(
                participants,
                key=lambda participant: (
                    participant.swiss_points,
                    participant.cups_metric,
                    -participant.swiss_games_played,
                    participant.seed,
                ),
                reverse=True,
            )
        )
        pairs: List[Tuple[str, str]] = []
        penalties: Dict[Tuple[str, str], float] = {}

        while len(remaining) >= 2 and len(pairs) < table_target:
            first = remaining[0]
            candidates: List[Tuple[float, int, SlotParticipant]] = []
            for idx in range(1, len(remaining)):
                second = remaining[idx]
                if not self._slot_pair_has_legal_assignment(first, second, state):
                    continue
                penalty = self.slot_pair_penalty(first, second, now_ts, point_cap, game_cap)
                penalty += max(0, abs(first.swiss_points - second.swiss_points) - point_cap) * 1200.0
                penalty += max(0, abs(first.swiss_games_played - second.swiss_games_played) - game_cap) * 900.0
                candidates.append((penalty, idx, second))

            if not candidates:
                break

            candidates.sort(key=lambda item: item[0])
            penalty, idx, second = candidates[0]
            pair_key = self._slot_pair_key(first.key, second.key)
            pairs.append(pair_key)
            penalties[pair_key] = penalty
            del remaining[idx]
            del remaining[0]

        return pairs, penalties

    def build_slot_wave_bundle(
        self,
        state: TournamentState,
        participants: List[SlotParticipant],
        table_target: int,
        relaxed: bool = False,
        reference_ts: float | None = None,
    ) -> Tuple[List[WaveSlotSuggestion], int, int]:
        if table_target <= 0:
            return [], DEFAULT_POINT_CAP, DEFAULT_GAME_CAP

        reference_ts = reference_ts or self.now()
        participants = list(participants)
        point_caps = RELAXED_POINT_CAPS if relaxed else [DEFAULT_POINT_CAP]
        game_caps = RELAXED_GAME_CAPS if relaxed else [DEFAULT_GAME_CAP]

        best_pairs: Tuple[Tuple[str, str], ...] = tuple()
        best_count = -1
        best_score = float("inf")
        best_point_cap = point_caps[0]
        best_game_cap = game_caps[0]
        best_penalty_map: Dict[Tuple[str, str], float] = {}

        for game_cap in game_caps:
            for point_cap in point_caps:
                count, score, pairs = self._solve_best_slot_bundle(state, participants, table_target, point_cap, game_cap, reference_ts)
                if count > best_count or (count == best_count and score < best_score):
                    best_count = count
                    best_score = score
                    best_pairs = pairs
                    best_point_cap = point_cap
                    best_game_cap = game_cap
                    best_penalty_map = {}
                    participant_map = {participant.key: participant for participant in participants}
                    for a_key, b_key in pairs:
                        best_penalty_map[(a_key, b_key)] = self.slot_pair_penalty(
                            participant_map[a_key], participant_map[b_key], reference_ts, point_cap, game_cap
                        )

        if best_count < table_target:
            fallback_pairs, fallback_penalties = self._greedy_slot_bundle(state, participants, table_target, best_point_cap, best_game_cap, reference_ts)
            if len(fallback_pairs) > len(best_pairs):
                best_pairs = tuple(fallback_pairs)
                best_penalty_map = fallback_penalties

        participant_map = {participant.key: participant for participant in participants}
        suggestions = [
            WaveSlotSuggestion(
                participant_a=participant_map[a_key],
                participant_b=participant_map[b_key],
                point_cap_used=best_point_cap,
                game_cap_used=best_game_cap,
                penalty=best_penalty_map.get((a_key, b_key), 0.0),
            )
            for a_key, b_key in best_pairs
        ]
        return suggestions, best_point_cap, best_game_cap

    def build_placeholder_wave_bundle(
        self,
        state: TournamentState,
        table_target: int,
        relaxed: bool = False,
        reference_ts: float | None = None,
    ) -> Tuple[List[WaveSlotSuggestion], int, int]:
        active_matches: List[Match] = []
        if state.current_wave:
            for match_id in state.current_wave.match_ids:
                match = state.matches.get(match_id)
                if match and match.phase == "SWISS" and match.status == "active":
                    active_matches.append(match)

        active_team_ids = {match.team_a for match in active_matches} | {match.team_b for match in active_matches}
        participants: List[SlotParticipant] = []
        for team in state.teams.values():
            if team.swiss_games_played >= SWISS_GAMES_PER_TEAM:
                continue
            if team.id in active_team_ids:
                continue
            participants.append(self.concrete_participant(team))

        active_matches.sort(key=lambda match: (match.wave_order or 999, match.table or 999, match.match_id))
        for match in active_matches:
            participants.append(self.outcome_participant(match, state, "winner"))
            participants.append(self.outcome_participant(match, state, "loser"))

        return self.build_slot_wave_bundle(state, participants, table_target, relaxed=relaxed, reference_ts=reference_ts)

    def build_wave_bundle(
        self,
        state: TournamentState,
        table_target: int,
        relaxed: bool = False,
        reference_ts: float | None = None,
    ) -> Tuple[List[PairSuggestion], int, int]:
        if table_target <= 0:
            return [], DEFAULT_POINT_CAP, DEFAULT_GAME_CAP

        reference_ts = reference_ts or self.now()
        teams = [team for team in state.teams.values() if team.swiss_games_played < SWISS_GAMES_PER_TEAM]
        # In Swiss we expect all teams to be available when a new wave is generated.
        teams.sort(key=lambda t: (t.swiss_points, t.cups_metric, -self.team_wait_minutes(t, reference_ts), -t.seed), reverse=True)

        point_caps = RELAXED_POINT_CAPS if relaxed else [DEFAULT_POINT_CAP]
        game_caps = RELAXED_GAME_CAPS if relaxed else [DEFAULT_GAME_CAP]

        best_pairs: Tuple[Tuple[int, int], ...] = tuple()
        best_count = -1
        best_score = float("inf")
        best_point_cap = point_caps[0]
        best_game_cap = game_caps[0]
        best_penalty_map: Dict[Tuple[int, int], float] = {}

        for game_cap in game_caps:
            for point_cap in point_caps:
                count, score, pairs = self._solve_best_bundle(teams, table_target, point_cap, game_cap, reference_ts)
                if count > best_count or (count == best_count and score < best_score):
                    best_count = count
                    best_score = score
                    best_pairs = pairs
                    best_point_cap = point_cap
                    best_game_cap = game_cap
                    best_penalty_map = {}
                    buchholz = self.buchholz_map(teams)
                    team_map = {team.id: team for team in teams}
                    for a_id, b_id in pairs:
                        best_penalty_map[(a_id, b_id)] = self.pair_penalty(
                            team_map[a_id], team_map[b_id], buchholz, reference_ts, point_cap, game_cap
                        )

        if best_count < table_target:
            fallback_pairs, fallback_penalties = self._greedy_bundle(teams, table_target, best_point_cap, best_game_cap, reference_ts)
            if len(fallback_pairs) > len(best_pairs):
                best_pairs = tuple(fallback_pairs)
                best_penalty_map = fallback_penalties

        suggestions = [
            PairSuggestion(
                team_a_id=a_id,
                team_b_id=b_id,
                point_cap_used=best_point_cap,
                game_cap_used=best_game_cap,
                penalty=best_penalty_map.get((a_id, b_id), 0.0),
            )
            for a_id, b_id in best_pairs
        ]
        return suggestions, best_point_cap, best_game_cap

    def preview_next_matches(self, state: TournamentState, limit: int = 2) -> List[PairSuggestion]:
        if not state.current_wave:
            return []
        match_ids = state.current_wave.match_ids
        suggestions: List[PairSuggestion] = []
        for match_id in match_ids:
            match = state.matches[match_id]
            if match.status == "pending":
                suggestions.append(
                    PairSuggestion(
                        team_a_id=match.team_a,
                        team_b_id=match.team_b,
                        point_cap_used=match.point_cap_used,
                        game_cap_used=match.game_cap_used,
                        penalty=match.wave_order or 0,
                    )
                )
            if len(suggestions) >= limit:
                break
        return suggestions
