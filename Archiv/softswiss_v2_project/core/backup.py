from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from core.config import AUTOSAVE_FILE, DATA_DIR, MAX_SNAPSHOTS, SNAPSHOT_DIR
from core.models import TournamentState


class BackupManager:
    def __init__(
        self,
        autosave_file: Path = AUTOSAVE_FILE,
        snapshot_dir: Path = SNAPSHOT_DIR,
        keep_snapshots: int = MAX_SNAPSHOTS,
    ) -> None:
        self.autosave_file = autosave_file
        self.snapshot_dir = snapshot_dir
        self.keep_snapshots = keep_snapshots
        self.last_save_ts: float | None = None
        self.last_save_label: str = "-"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, payload: dict) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def save_state(self, state: TournamentState, label: str = "autosave", snapshot: bool = False) -> Path:
        payload = state.to_dict()
        self._atomic_write(self.autosave_file, payload)
        self.last_save_ts = time.time()
        self.last_save_label = label

        if snapshot:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label).strip("_") or "snapshot"
            snapshot_path = self.snapshot_dir / f"{stamp}_{safe_label}.json"
            self._atomic_write(snapshot_path, payload)
            self._cleanup_old_snapshots()

        return self.autosave_file

    def load_state(self, path: Optional[Path] = None) -> TournamentState:
        source = path or self.autosave_file
        data = json.loads(source.read_text(encoding="utf-8"))
        try:
            self.last_save_ts = source.stat().st_mtime
            self.last_save_label = "loaded"
        except OSError:
            self.last_save_ts = None
            self.last_save_label = "loaded"
        return TournamentState.from_dict(data)

    def autosave_exists(self) -> bool:
        return self.autosave_file.exists()

    def delete_autosave(self) -> None:
        if self.autosave_file.exists():
            self.autosave_file.unlink()

    def latest_snapshot(self) -> Optional[Path]:
        snapshots = sorted(self.snapshot_dir.glob("*.json"))
        return snapshots[-1] if snapshots else None

    def _cleanup_old_snapshots(self) -> None:
        snapshots = sorted(self.snapshot_dir.glob("*.json"))
        excess = len(snapshots) - self.keep_snapshots
        for path in snapshots[:max(0, excess)]:
            path.unlink(missing_ok=True)
