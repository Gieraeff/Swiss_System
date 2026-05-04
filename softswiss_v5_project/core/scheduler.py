from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Dict, List, Tuple

from core.config import DEFAULT_GAME_CAP, DEFAULT_POINT_CAP, RELAXED_GAME_CAPS, RELAXED_POINT_CAPS
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
        if now_ts is None:
            now_ts = self.now()
        return max(0.0, (now_ts - team.last_finish_ts) / 60.0)

    def buchholz_map(self, teams: List[Team]) -> Dict[int, int]:
        by_id = {team.id: team for team in teams}
        result: Dict[int, int] = {}
        for team in teams:
            result[team.id] = sum(by_id[opp_id].swiss_points for opp_id in team.opponents if opp_id in by_id)
        return result

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
        teams = [team for team in state.teams.values() if team.swiss_games_played < 999]
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
