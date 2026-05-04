from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class Team:
    id: int
    name: str
    seed: int
    swiss_points: int = 0
    cups_metric: int = 0
    swiss_games_played: int = 0
    opponents: Set[int] = field(default_factory=set)
    last_finish_ts: float = 0.0
    active_match_id: Optional[str] = None
    ko_seed: Optional[int] = None
    next_up_plan: List[dict] = field(default_factory=list)
    next_up_updated_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "seed": self.seed,
            "swiss_points": self.swiss_points,
            "cups_metric": self.cups_metric,
            "swiss_games_played": self.swiss_games_played,
            "opponents": sorted(self.opponents),
            "last_finish_ts": self.last_finish_ts,
            "active_match_id": self.active_match_id,
            "ko_seed": self.ko_seed,
            "next_up_plan": list(self.next_up_plan),
            "next_up_updated_ts": self.next_up_updated_ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Team":
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            seed=int(data["seed"]),
            swiss_points=int(data.get("swiss_points", 0)),
            cups_metric=int(data.get("cups_metric", 0)),
            swiss_games_played=int(data.get("swiss_games_played", 0)),
            opponents={int(x) for x in data.get("opponents", [])},
            last_finish_ts=float(data.get("last_finish_ts", 0.0)),
            active_match_id=data.get("active_match_id"),
            ko_seed=(int(data["ko_seed"]) if data.get("ko_seed") is not None else None),
            next_up_plan=[dict(item) for item in data.get("next_up_plan", [])],
            next_up_updated_ts=float(data.get("next_up_updated_ts", 0.0)),
        )


@dataclass
class Match:
    match_id: str
    phase: str
    slot_label: str
    table: int
    team_a: int
    team_b: int
    status: str = "active"
    winner: Optional[int] = None
    loser: Optional[int] = None
    is_overtime: bool = False
    loser_cups_hit: Optional[int] = None
    started_ts: float = 0.0
    ended_ts: Optional[float] = None
    point_cap_used: int = 0
    game_cap_used: int = 1

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "phase": self.phase,
            "slot_label": self.slot_label,
            "table": self.table,
            "team_a": self.team_a,
            "team_b": self.team_b,
            "status": self.status,
            "winner": self.winner,
            "loser": self.loser,
            "is_overtime": self.is_overtime,
            "loser_cups_hit": self.loser_cups_hit,
            "started_ts": self.started_ts,
            "ended_ts": self.ended_ts,
            "point_cap_used": self.point_cap_used,
            "game_cap_used": self.game_cap_used,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Match":
        return cls(
            match_id=str(data["match_id"]),
            phase=str(data["phase"]),
            slot_label=str(data.get("slot_label", "")),
            table=int(data["table"]),
            team_a=int(data["team_a"]),
            team_b=int(data["team_b"]),
            status=str(data.get("status", "active")),
            winner=(int(data["winner"]) if data.get("winner") is not None else None),
            loser=(int(data["loser"]) if data.get("loser") is not None else None),
            is_overtime=bool(data.get("is_overtime", False)),
            loser_cups_hit=(
                int(data["loser_cups_hit"]) if data.get("loser_cups_hit") is not None else None
            ),
            started_ts=float(data.get("started_ts", 0.0)),
            ended_ts=(float(data["ended_ts"]) if data.get("ended_ts") is not None else None),
            point_cap_used=int(data.get("point_cap_used", 0)),
            game_cap_used=int(data.get("game_cap_used", 1)),
        )


@dataclass
class KOSlot:
    slot_id: str
    stage: str
    label: str
    team_a: Optional[int] = None
    team_b: Optional[int] = None
    status: str = "pending"
    winner: Optional[int] = None
    loser: Optional[int] = None
    match_id: Optional[str] = None
    table: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "stage": self.stage,
            "label": self.label,
            "team_a": self.team_a,
            "team_b": self.team_b,
            "status": self.status,
            "winner": self.winner,
            "loser": self.loser,
            "match_id": self.match_id,
            "table": self.table,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KOSlot":
        return cls(
            slot_id=str(data["slot_id"]),
            stage=str(data["stage"]),
            label=str(data["label"]),
            team_a=(int(data["team_a"]) if data.get("team_a") is not None else None),
            team_b=(int(data["team_b"]) if data.get("team_b") is not None else None),
            status=str(data.get("status", "pending")),
            winner=(int(data["winner"]) if data.get("winner") is not None else None),
            loser=(int(data["loser"]) if data.get("loser") is not None else None),
            match_id=data.get("match_id"),
            table=(int(data["table"]) if data.get("table") is not None else None),
        )


@dataclass
class TournamentState:
    phase: str = "SETUP"
    started_at: float = 0.0
    match_counter: int = 1
    teams: Dict[int, Team] = field(default_factory=dict)
    active_matches: Dict[int, Match] = field(default_factory=dict)
    completed_matches: List[Match] = field(default_factory=list)
    ko_slots: Dict[str, KOSlot] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    podium: List[int] = field(default_factory=list)
    top4: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "started_at": self.started_at,
            "match_counter": self.match_counter,
            "teams": [team.to_dict() for team in self.teams.values()],
            "active_matches": {str(table): match.to_dict() for table, match in self.active_matches.items()},
            "completed_matches": [match.to_dict() for match in self.completed_matches],
            "ko_slots": {slot_id: slot.to_dict() for slot_id, slot in self.ko_slots.items()},
            "logs": list(self.logs),
            "podium": list(self.podium),
            "top4": list(self.top4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TournamentState":
        state = cls()
        state.phase = str(data.get("phase", "SETUP"))
        state.started_at = float(data.get("started_at", 0.0))
        state.match_counter = int(data.get("match_counter", 1))
        state.teams = {
            int(team_data["id"]): Team.from_dict(team_data)
            for team_data in data.get("teams", [])
        }
        state.active_matches = {
            int(table): Match.from_dict(match_data)
            for table, match_data in data.get("active_matches", {}).items()
        }
        state.completed_matches = [Match.from_dict(match_data) for match_data in data.get("completed_matches", [])]
        state.ko_slots = {
            slot_id: KOSlot.from_dict(slot_data)
            for slot_id, slot_data in data.get("ko_slots", {}).items()
        }
        state.logs = [str(x) for x in data.get("logs", [])]
        state.podium = [int(x) for x in data.get("podium", [])]
        state.top4 = [int(x) for x in data.get("top4", [])]
        return state
