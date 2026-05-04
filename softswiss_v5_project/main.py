from __future__ import annotations

import tkinter as tk

from core.backup import BackupManager
from core.scheduler import SoftSwissScheduler
from core.Swiss import SwissTournamentEngine
from ui.GUI import TournamentGUI


class MainApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.backup_manager = BackupManager()
        self.scheduler = SoftSwissScheduler()
        self.engine = SwissTournamentEngine(scheduler=self.scheduler)
        self.gui = TournamentGUI(root=self.root, engine=self.engine, backup_manager=self.backup_manager)
        self.engine.on_change = self.gui.request_refresh

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    MainApp().run()


if __name__ == "__main__":
    main()
