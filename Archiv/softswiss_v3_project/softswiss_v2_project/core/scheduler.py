from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Dict, Iterable, List, Tuple

from core.config import (
    DEFAULT_SWISS_GAME_CAP,
    DEFAULT_SWISS_POINT_CAP,
    RELAXED_GAME_CAPS,
    RELAXED_POINT_CAPS,
    SWISS_GAMES_PER_TEAM,
)
from core.models import Team, TournamentState


@dataclass(frozen=True)
class PairSuggestion:
    team_a_id: int
    team_b_id: int
    point_cap_used: int
    game_cap_used: int
    penalty: float


class SoftSwissScheduler:
    def now(self) -> float:
        return time.time()

    def team_wait_minutes(self, team: Team, now_ts: float | None = None) -> float:
        if team.active_match_id:
            return 0.0
        if now_ts is None:
            now_ts = self.now()
        return max(0.0, (now_ts - team.last_finish_ts) / 60.0)

    def available_swiss_teams(self, state: TournamentState) -> List[Team]:
        active_team_ids = set()
        for match in state.active_matches.values():
            active_team_ids.add(match.team_a)
            active_team_ids.add(match.team_b)
        return [
            team
            for team in state.teams.values()
            if team.id not in active_team_ids and team.swiss_games_played < SWISS_GAMES_PER_TEAM
        ]

    def candidate_pool(
        self, state: TournamentState, table_target: int, game_cap: int, now_ts: float | None = None
    ) -> List[Team]:
        available = self.available_swiss_teams(state)
        if not available:
            return []

        if now_ts is None:
            now_ts = self.now()

        available.sort(
            key=lambda team: (team.swiss_games_played, -self.team_wait_minutes(team, now_ts), team.seed)
        )
        min_games = min(team.swiss_games_played for team in available)
        threshold = min_games + game_cap
        pool = [team for team in available if team.swiss_games_played <= threshold]

        minimum_teams = min(len(available), max(2, table_target * 2 + 2))
        if len(pool) < minimum_teams:
            for team in available:
                if team not in pool:
                    pool.append(team)
                if len(pool) >= minimum_teams:
                    break

        return pool

    def is_valid_pair(self, a: Team, b: Team, point_cap: int, game_cap: int) -> bool:
        if a.id == b.id:
            return False
        if b.id in a.opponents:
            return False
        if abs(a.swiss_games_played - b.swiss_games_played) > game_cap:
            return False
        if abs(a.swiss_points - b.swiss_points) > point_cap:
            return False
        return True

    def pair_penalty(self, a: Team, b: Team, now_ts: float | None = None) -> float:
        if now_ts is None:
            now_ts = self.now()

        point_diff = abs(a.swiss_points - b.swiss_points)
        games_diff = abs(a.swiss_games_played - b.swiss_games_played)
        wait_bonus = (self.team_wait_minutes(a, now_ts) + self.team_wait_minutes(b, now_ts)) / 5.0
        seed_tiebreak = (a.seed + b.seed) / 1000.0
        return (10 * point_diff) + (6 * games_diff) - (3 * wait_bonus) + seed_tiebreak

    def _edge_key(self, a_id: int, b_id: int) -> Tuple[int, int]:
        return (a_id, b_id) if a_id < b_id else (b_id, a_id)

    def _future_need_state(
        self,
        state: TournamentState,
        extra_pairs: Tuple[Tuple[int, int], ...] = tuple(),
    ) -> Tuple[Dict[int, int], set[Tuple[int, int]]]:
        needs = {
            team.id: SWISS_GAMES_PER_TEAM - team.swiss_games_played
            for team in state.teams.values()
        }
        banned_edges: set[Tuple[int, int]] = set()

        for team in state.teams.values():
            for opponent_id in team.opponents:
                banned_edges.add(self._edge_key(team.id, opponent_id))

        for match in state.active_matches.values():
            if match.phase != "SWISS":
                continue
            needs[match.team_a] -= 1
            needs[match.team_b] -= 1
            banned_edges.add(self._edge_key(match.team_a, match.team_b))

        for a_id, b_id in extra_pairs:
            needs[a_id] -= 1
            needs[b_id] -= 1
            banned_edges.add(self._edge_key(a_id, b_id))

        return needs, banned_edges

    def _remaining_pairs_after(
        self,
        state: TournamentState,
        extra_pairs: Tuple[Tuple[int, int], ...] = tuple(),
    ) -> int:
        needs, _ = self._future_need_state(state, extra_pairs)
        total_need = sum(max(0, value) for value in needs.values())
        return total_need // 2

    def _completion_feasible(
        self,
        state: TournamentState,
        extra_pairs: Tuple[Tuple[int, int], ...] = tuple(),
    ) -> bool:
        needs, banned_edges = self._future_need_state(state, extra_pairs)

        if any(value < 0 for value in needs.values()):
            return False

        total_need = sum(needs.values())
        if total_need % 2 != 0:
            return False

        positive_ids = tuple(sorted(team_id for team_id, need in needs.items() if need > 0))
        if not positive_ids:
            return True

        allowed_neighbors: Dict[int, set[int]] = {}
        for team_id in state.teams.keys():
            allowed_neighbors[team_id] = set()
            for other_id in state.teams.keys():
                if team_id == other_id:
                    continue
                if self._edge_key(team_id, other_id) in banned_edges:
                    continue
                allowed_neighbors[team_id].add(other_id)

        @lru_cache(maxsize=None)
        def solve(remaining: Tuple[Tuple[int, int], ...]) -> bool:
            if not remaining:
                return True

            state_dict = {team_id: need for team_id, need in remaining}

            for team_id, need in remaining:
                available = [
                    other_id
                    for other_id in state_dict.keys()
                    if other_id != team_id
                    and other_id in allowed_neighbors[team_id]
                    and state_dict[other_id] > 0
                ]
                if len(available) < need:
                    return False

            pivot = min(
                remaining,
                key=lambda item: (
                    len(
                        [
                            other_id
                            for other_id in state_dict.keys()
                            if other_id != item[0]
                            and other_id in allowed_neighbors[item[0]]
                            and state_dict[other_id] > 0
                        ]
                    )
                    - item[1],
                    -item[1],
                ),
            )
            team_id, degree = pivot

            candidates = [
                other_id
                for other_id in state_dict.keys()
                if other_id != team_id
                and other_id in allowed_neighbors[team_id]
                and state_dict[other_id] > 0
            ]
            candidates.sort(key=lambda other_id: (-state_dict[other_id], other_id))

            for combo in combinations(candidates, degree):
                next_state = dict(state_dict)
                next_state[team_id] = 0
                valid = True
                for other_id in combo:
                    next_state[other_id] -= 1
                    if next_state[other_id] < 0:
                        valid = False
                        break
                if not valid:
                    continue
                reduced = tuple(sorted((tid, val) for tid, val in next_state.items() if val > 0))
                if solve(reduced):
                    return True

            return False

        initial_state = tuple(sorted((team_id, need) for team_id, need in needs.items() if need > 0))
        return solve(initial_state)

    def _solve_best_bundle(
        self,
        state: TournamentState,
        teams: List[Team],
        table_target: int,
        point_cap: int,
        game_cap: int,
        now_ts: float | None = None,
    ) -> Tuple[int, float, Tuple[Tuple[int, int], ...]]:
        team_map = {team.id: team for team in teams}
        ids = tuple(sorted(team_map.keys()))
        if now_ts is None:
            now_ts = self.now()
        pair_penalties: Dict[Tuple[int, int], float] = {}

        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                team_a = team_map[ids[i]]
                team_b = team_map[ids[j]]
                if self.is_valid_pair(team_a, team_b, point_cap, game_cap):
                    pair_penalties[(team_a.id, team_b.id)] = self.pair_penalty(team_a, team_b, now_ts)

        def is_better(
            left: Tuple[int, float, Tuple[Tuple[int, int], ...]],
            right: Tuple[int, float, Tuple[Tuple[int, int], ...]],
        ) -> bool:
            if left[0] != right[0]:
                return left[0] > right[0]
            return left[1] < right[1]

        @lru_cache(maxsize=None)
        def solve(remaining_ids: Tuple[int, ...], slots_left: int) -> Tuple[int, float, Tuple[Tuple[int, int], ...]]:
            if slots_left == 0 or len(remaining_ids) < 2:
                return (0, 0.0, tuple())

            first_id = remaining_ids[0]
            best = solve(remaining_ids[1:], slots_left)

            for idx in range(1, len(remaining_ids)):
                second_id = remaining_ids[idx]
                key = (first_id, second_id)
                if key not in pair_penalties:
                    continue

                next_remaining = remaining_ids[1:idx] + remaining_ids[idx + 1 :]
                sub_count, sub_score, sub_pairs = solve(next_remaining, slots_left - 1)
                candidate = (
                    sub_count + 1,
                    sub_score + pair_penalties[key],
                    ((first_id, second_id),) + sub_pairs,
                )
                remaining_pairs = self._remaining_pairs_after(state, candidate[2])
                if remaining_pairs <= 12 and not self._completion_feasible(state, candidate[2]):
                    continue
                if is_better(candidate, best):
                    best = candidate

            return best

        return solve(ids, table_target)

    def best_bundle(
        self,
        state: TournamentState,
        table_target: int,
        relaxed: bool = False,
        point_caps: List[int] | None = None,
        game_caps: List[int] | None = None,
        reference_ts: float | None = None,
    ) -> Tuple[List[PairSuggestion], int, int]:
        if table_target <= 0:
            return [], DEFAULT_SWISS_POINT_CAP, DEFAULT_SWISS_GAME_CAP

        point_caps = point_caps or (RELAXED_POINT_CAPS if relaxed else [DEFAULT_SWISS_POINT_CAP])
        game_caps = game_caps or (RELAXED_GAME_CAPS if relaxed else [DEFAULT_SWISS_GAME_CAP])
        now_ts = self.now()

        best_pairs: Tuple[Tuple[int, int], ...] = tuple()
        best_count = -1
        best_score = float("inf")
        best_point_cap = point_caps[0]
        best_game_cap = game_caps[0]
        best_penalties: Dict[Tuple[int, int], float] = {}
        if reference_ts is None:
            reference_ts = self.now()

        for game_cap in game_caps:
            teams = self.candidate_pool(state, table_target, game_cap, now_ts=reference_ts)
            if len(teams) < 2:
                continue
            team_map = {team.id: team for team in teams}
            for point_cap in point_caps:
                count, score, pairs = self._solve_best_bundle(
                    state, teams, table_target, point_cap, game_cap, now_ts=reference_ts
                )
                if count > best_count:
                    best_pairs = pairs
                    best_count = count
                    best_score = score
                    best_point_cap = point_cap
                    best_game_cap = game_cap
                    best_penalties = {
                        pair: self.pair_penalty(team_map[pair[0]], team_map[pair[1]], self.now())
                        for pair in pairs
                    }
                elif count == best_count:
                    if game_cap < best_game_cap or (
                        game_cap == best_game_cap and point_cap < best_point_cap
                    ) or (
                        game_cap == best_game_cap and point_cap == best_point_cap and score < best_score
                    ):
                        best_pairs = pairs
                        best_score = score
                        best_point_cap = point_cap
                        best_game_cap = game_cap
                        best_penalties = {
                            pair: self.pair_penalty(team_map[pair[0]], team_map[pair[1]], now_ts)
                            for pair in pairs
                        }

        suggestions = [
            PairSuggestion(
                team_a_id=pair[0],
                team_b_id=pair[1],
                point_cap_used=best_point_cap,
                game_cap_used=best_game_cap,
                penalty=best_penalties.get(pair, 0.0),
            )
            for pair in best_pairs
        ]
        return suggestions, best_point_cap, best_game_cap

    def preview_bundle(
        self, state: TournamentState, limit: int, reference_ts: float | None = None
    ) -> List[PairSuggestion]:
        suggestions, _, _ = self.best_bundle(state, limit, relaxed=False, reference_ts=reference_ts)
        return suggestions
