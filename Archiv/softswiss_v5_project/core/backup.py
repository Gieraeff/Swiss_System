from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import AUTOSAVE_FILE, MAX_SNAPSHOTS, SNAPSHOT_DIR
from core.models import TournamentState


class BackupManager:
    def __init__(self, autosave_file: Path = AUTOSAVE_FILE, snapshot_dir: Path = SNAPSHOT_DIR) -> None:
        self.autosave_file = autosave_file
        self.snapshot_dir = snapshot_dir
        self.autosave_file.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def _write_state_file(self, path: Path, state: TournamentState, label: str) -> None:
        payload = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "state": state.to_dict(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_state(self, state: TournamentState, label: str = "autosave", snapshot: bool = False) -> Path:
        now = datetime.now().timestamp()
        state.last_save_ts = now
        state.last_save_label = label
        state.autosave_count += 1
        self._write_state_file(self.autosave_file, state, label)

        if snapshot:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot_name = f"{stamp}_{label}.json"
            snapshot_path = self.snapshot_dir / snapshot_name
            self._write_state_file(snapshot_path, state, label)
            self._cleanup_snapshots()
            return snapshot_path

        return self.autosave_file

    def load_state(self, path: Optional[Path] = None) -> TournamentState:
        path = path or self.autosave_file
        data = json.loads(path.read_text(encoding="utf-8"))
        return TournamentState.from_dict(data["state"])

    def autosave_exists(self) -> bool:
        return self.autosave_file.exists()

    def delete_autosave(self) -> None:
        if self.autosave_file.exists():
            self.autosave_file.unlink()

    def latest_snapshot(self) -> Optional[Path]:
        snapshots = sorted(self.snapshot_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return snapshots[0] if snapshots else None

    def _cleanup_snapshots(self) -> None:
        snapshots = sorted(self.snapshot_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in snapshots[MAX_SNAPSHOTS:]:
            try:
                old.unlink()
            except OSError:
                pass
