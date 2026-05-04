from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from core.backup import BackupManager
from core.config import APP_TITLE, AUTO_REFRESH_MS, PLAYER_REFRESH_MS, PUBLIC_THEME, TEAM_COUNT
from core.Swiss import SwissTournamentEngine


class TournamentGUI:
    def __init__(
        self,
        root: tk.Tk,
        engine: SwissTournamentEngine,
        backup_manager: BackupManager,
    ) -> None:
        self.root = root
        self.engine = engine
        self.backup_manager = backup_manager
        self.root.title(APP_TITLE)
        self.root.geometry("1560x980")
        self.root.minsize(1360, 860)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.phase_var = tk.StringVar(value=self.engine.phase_label())
        self.progress_var = tk.StringVar(value=self.engine.progress_text())
        self.status_var = tk.StringVar(value="Bereit")
        self.selected_table_var = tk.StringVar(value="")
        self.selected_winner_var = tk.StringVar(value="")
        self.ot_var = tk.BooleanVar(value=False)
        self.loser_cups_var = tk.StringVar(value="0")

        self._configure_styles()
        self._build_admin_window()
        self._build_player_display()
        self.refresh_all()
        self.root.after(AUTO_REFRESH_MS, self._periodic_refresh_admin)
        self.root.after(PLAYER_REFRESH_MS, self._periodic_refresh_public)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Header.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground="#5b6470")
        style.configure("BigStatus.TLabel", font=("Segoe UI", 13))
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))

    # ------------------------------------------------------------------
    # Build admin UI
    # ------------------------------------------------------------------

    def _build_admin_window(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, textvariable=self.phase_var, style="Header.TLabel").pack(side="left")
        ttk.Label(header, text=" | ", style="Header.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.progress_var, style="BigStatus.TLabel").pack(side="left")
        ttk.Button(
            header,
            text="Spieleranzeige zeigen",
            command=self.show_player_display,
            style="Action.TButton",
        ).pack(side="right")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        self.setup_tab = ttk.Frame(notebook, padding=12)
        self.control_tab = ttk.Frame(notebook, padding=12)
        self.backup_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.setup_tab, text="Setup")
        notebook.add(self.control_tab, text="Turnierleitung")
        notebook.add(self.backup_tab, text="Backup")

        self._build_setup_tab()
        self._build_control_tab()
        self._build_backup_tab()

    def _build_setup_tab(self) -> None:
        ttk.Label(
            self.setup_tab,
            text=f"{TEAM_COUNT} Teamnamen eingeben - je Zeile ein Team. Seed = Eingabereihenfolge.",
            style="Section.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self.setup_tab,
            text="SoftSwiss ohne Rematches in der Swiss-Phase, KO-Phase mit fixem 1v8-Bracket.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 10))

        self.team_text = tk.Text(
            self.setup_tab,
            height=24,
            width=48,
            font=("Consolas", 11),
            relief="solid",
            borderwidth=1,
        )
        self.team_text.pack(fill="both", expand=True)

        buttons = ttk.Frame(self.setup_tab)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Demo-Teams", command=self.load_demo_teams).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Neues Turnier starten", command=self.start_new_tournament).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

    def _build_control_tab(self) -> None:
        pane = ttk.Panedwindow(self.control_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        live_frame = ttk.LabelFrame(left, text="Live-Tische", style="Card.TLabelframe")
        live_frame.pack(fill="x", pady=(0, 10))
        self.active_tree = ttk.Treeview(
            live_frame,
            columns=("table", "phase", "slot", "team_a", "team_b"),
            show="headings",
            height=8,
        )
        for col, text, width in [
            ("table", "Tisch", 65),
            ("phase", "Phase", 85),
            ("slot", "Slot", 140),
            ("team_a", "Team A", 170),
            ("team_b", "Team B", 170),
        ]:
            self.active_tree.heading(col, text=text)
            self.active_tree.column(col, width=width, anchor="center")
        self.active_tree.pack(fill="x")

        pairing_frame = ttk.LabelFrame(left, text="Aktuelles Pairing", style="Card.TLabelframe")
        pairing_frame.pack(fill="x", pady=(0, 10))
        self.pairing_text = tk.Text(pairing_frame, height=7, font=("Consolas", 10), state="disabled", wrap="word")
        self.pairing_text.pack(fill="x")

        entry_frame = ttk.LabelFrame(left, text="Ergebnis eintragen", style="Card.TLabelframe")
        entry_frame.pack(fill="x", pady=(0, 10))
        entry_frame.columnconfigure(1, weight=1)
        entry_frame.columnconfigure(3, weight=1)

        ttk.Label(entry_frame, text="Tisch").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.table_combo = ttk.Combobox(
            entry_frame,
            textvariable=self.selected_table_var,
            state="readonly",
            width=12,
        )
        self.table_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.table_combo.bind("<<ComboboxSelected>>", self.on_table_selected)

        ttk.Label(entry_frame, text="Sieger").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=(0, 8))
        self.winner_combo = ttk.Combobox(
            entry_frame,
            textvariable=self.selected_winner_var,
            state="readonly",
            width=32,
        )
        self.winner_combo.grid(row=0, column=3, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(entry_frame, text="OT", variable=self.ot_var, command=self.on_ot_toggle).grid(
            row=1, column=0, sticky="w"
        )
        ttk.Label(entry_frame, text="Verlierer-Becher").grid(row=1, column=2, sticky="w", padx=(12, 8))
        self.loser_spin = ttk.Spinbox(
            entry_frame,
            textvariable=self.loser_cups_var,
            from_=0,
            to=12,
            width=12,
        )
        self.loser_spin.grid(row=1, column=3, sticky="w")

        self.ot_hint_label = ttk.Label(
            entry_frame,
            text="Normal: 0-9 | OT: 10-12 | OT-Cups: Sieger bekommt nur die Differenz, Verlierer 0",
            style="Muted.TLabel",
        )
        self.ot_hint_label.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Button(entry_frame, text="Ergebnis speichern", command=self.submit_result_from_gui, style="Action.TButton").grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )

        actions = ttk.LabelFrame(left, text="Aktionen", style="Card.TLabelframe")
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Freie Tische auffüllen", command=self.fill_tables_normal).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Erweiterte Suche manuell", command=self.fill_tables_relaxed).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Next-Up neu berechnen", command=self.rebuild_preview).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Autosave jetzt", command=self.save_autosave).pack(fill="x")

        status_box = ttk.LabelFrame(left, text="Status", style="Card.TLabelframe")
        status_box.pack(fill="both", expand=True)
        ttk.Label(status_box, textvariable=self.status_var, wraplength=420, justify="left").pack(anchor="w")

        preview_frame = ttk.LabelFrame(right, text="Next-Up Vorschau", style="Card.TLabelframe")
        preview_frame.pack(fill="x", pady=(0, 10))
        self.preview_tree = ttk.Treeview(
            preview_frame,
            columns=("idx", "label", "team_a", "team_b", "points", "games", "caps"),
            show="headings",
            height=4,
        )
        for col, text, width in [
            ("idx", "#", 40),
            ("label", "Slot", 100),
            ("team_a", "Team A", 160),
            ("team_b", "Team B", 160),
            ("points", "Punkte", 80),
            ("games", "Spiele", 80),
            ("caps", "Caps", 85),
        ]:
            self.preview_tree.heading(col, text=text)
            self.preview_tree.column(col, width=width, anchor="center")
        self.preview_tree.pack(fill="x")

        ranking_frame = ttk.LabelFrame(right, text="Live-Ranking", style="Card.TLabelframe")
        ranking_frame.pack(fill="both", expand=True, pady=(0, 10))
        self.ranking_tree = ttk.Treeview(
            ranking_frame,
            columns=("rank", "seed", "team", "pts", "cups", "bh", "games", "ko_seed"),
            show="headings",
            height=16,
        )
        for col, text, width in [
            ("rank", "#", 40),
            ("seed", "Seed", 55),
            ("team", "Team", 200),
            ("pts", "Pkt", 55),
            ("cups", "Cups", 70),
            ("bh", "Buchholz", 80),
            ("games", "Spiele", 70),
            ("ko_seed", "KO", 55),
        ]:
            self.ranking_tree.heading(col, text=text)
            self.ranking_tree.column(col, width=width, anchor="center")
        self.ranking_tree.tag_configure("top8", background="#dff8ef")
        self.ranking_tree.pack(fill="both", expand=True)

        log_frame = ttk.LabelFrame(right, text="Log", style="Card.TLabelframe")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=10, font=("Consolas", 10), state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _build_backup_tab(self) -> None:
        ttk.Label(self.backup_tab, text="Autosave & Snapshots", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.backup_tab,
            text="Autosave wird nach jeder relevanten Aktion aktualisiert. Zusätzliche Snapshots können manuell erzeugt werden.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        actions = ttk.Frame(self.backup_tab)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Autosave speichern", command=self.save_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Snapshot erstellen", command=self.save_snapshot).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

        self.backup_info = tk.Text(self.backup_tab, height=18, font=("Consolas", 10), state="disabled")
        self.backup_info.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Build public display
    # ------------------------------------------------------------------

    def _build_player_display(self) -> None:
        colors = PUBLIC_THEME
        self.player_window = tk.Toplevel(self.root)
        self.player_window.title("Spieleranzeige")
        self.player_window.geometry("1680x980")
        self.player_window.configure(bg=colors["bg"])
        self.player_window.protocol("WM_DELETE_WINDOW", self.player_window.withdraw)

        topbar = tk.Frame(self.player_window, bg=colors["bg"], padx=18, pady=14)
        topbar.pack(fill="x")
        self.player_title = tk.Label(
            topbar,
            text="Beerpong Turnier",
            fg=colors["text"],
            bg=colors["bg"],
            font=("Segoe UI", 28, "bold"),
        )
        self.player_title.pack(side="left")
        self.player_status = tk.Label(
            topbar,
            text="",
            fg=colors["accent"],
            bg=colors["bg"],
            font=("Segoe UI", 18),
        )
        self.player_status.pack(side="left", padx=(18, 0))
        tk.Button(
            topbar,
            text="Vollbild",
            command=self.toggle_player_fullscreen,
            bg=colors["card"],
            fg=colors["text"],
            activebackground=colors["panel"],
            activeforeground=colors["text"],
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right")

        content = tk.Frame(self.player_window, bg=colors["bg"], padx=18, pady=6)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=2)
        content.grid_rowconfigure(2, weight=1)

        current_card = self._public_card(content, "Gerade am Tisch")
        current_card.grid(row=0, column=0, sticky="nsew", padx=(0, 9), pady=(0, 10))
        next_card = self._public_card(content, "Als Nächstes")
        next_card.grid(row=0, column=1, sticky="nsew", padx=(9, 0), pady=(0, 10))
        ranking_card = self._public_card(content, "Live-Ranking")
        ranking_card.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 10))
        ko_card = self._public_card(content, "KO / Podest")
        ko_card.grid(row=2, column=0, columnspan=2, sticky="nsew")

        self.player_current_label = self._public_multiline_label(current_card, size=18)
        self.player_next_label = self._public_multiline_label(next_card, size=18)
        self.player_ranking_text = self._public_text_box(ranking_card, size=11, mono=True)
        self.player_ko_label = self._public_multiline_label(ko_card, size=16)

    def _public_card(self, parent: tk.Widget, title: str) -> tk.Frame:
        colors = PUBLIC_THEME
        frame = tk.Frame(parent, bg=colors["card"], padx=14, pady=12, highlightthickness=1, highlightbackground="#334155")
        tk.Label(
            frame,
            text=title,
            fg=colors["accent"],
            bg=colors["card"],
            font=("Segoe UI", 16, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        return frame

    def _public_multiline_label(self, parent: tk.Widget, size: int, mono: bool = False) -> tk.Label:
        colors = PUBLIC_THEME
        family = "Consolas" if mono else "Segoe UI"
        label = tk.Label(
            parent,
            text="-",
            justify="left",
            anchor="nw",
            bg=colors["card"],
            fg=colors["text"],
            font=(family, size),
            wraplength=760,
        )
        label.pack(fill="both", expand=True)
        return label

    def _public_text_box(self, parent: tk.Widget, size: int, mono: bool = False) -> tk.Text:
        colors = PUBLIC_THEME
        family = "Consolas" if mono else "Segoe UI"
        text_widget = tk.Text(
            parent,
            bg=colors["card"],
            fg=colors["text"],
            font=(family, size),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            wrap="none",
            height=10,
        )
        text_widget.pack(fill="both", expand=True)
        return text_widget

    def _format_two_column_ranking(self, rows: List[dict]) -> str:
        left = rows[: (len(rows) + 1) // 2]
        right = rows[(len(rows) + 1) // 2 :]
        header = f"{'#':>2} {'Team':<16} {'Pkt':>3} {'Cup':>4} {'BH':>3}    |    {'#':>2} {'Team':<16} {'Pkt':>3} {'Cup':>4} {'BH':>3}"
        lines = [header, "-" * len(header)]
        for idx in range(max(len(left), len(right))):
            l = left[idx] if idx < len(left) else None
            r = right[idx] if idx < len(right) else None
            left_text = (
                f"{l['rank']:>2} {l['name'][:16]:<16} {l['points']:>3} {l['cups']:>4} {l['buchholz']:>3}"
                if l
                else " " * 34
            )
            right_text = (
                f"{r['rank']:>2} {r['name'][:16]:<16} {r['points']:>3} {r['cups']:>4} {r['buchholz']:>3}"
                if r
                else ""
            )
            lines.append(f"{left_text}    |    {right_text}")
        return "\n".join(lines)

    def _format_pairing_overview(self) -> str:
        lines: List[str] = []
        active_rows = self.engine.active_matches_rows()
        if active_rows:
            lines.append("Aktiv:")
            for row in active_rows:
                lines.append(f"T{row['table']}: {row['team_a']} vs {row['team_b']} ({row['phase']})")
        else:
            lines.append("Aktiv: keine laufenden Spiele")

        preview_rows = self.engine.preview_matches()
        if preview_rows:
            lines.append("")
            lines.append("Als Nächstes:")
            for row in preview_rows:
                lines.append(f"{row['label']}: {row['team_a']} vs {row['team_b']}")
        else:
            lines.append("")
            lines.append("Als Nächstes: keine Vorschau")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_close(self) -> None:
        try:
            if self.engine.state.phase != "SETUP":
                self.backup_manager.save_state(self.engine.state, label="shutdown", snapshot=False)
        except Exception:
            pass
        self.root.destroy()

    def show_player_display(self) -> None:
        self.player_window.deiconify()
        self.player_window.lift()

    def toggle_player_fullscreen(self) -> None:
        fullscreen = bool(self.player_window.attributes("-fullscreen"))
        self.player_window.attributes("-fullscreen", not fullscreen)

    def persist_state(self, label: str = "autosave", snapshot: bool = False) -> None:
        self.backup_manager.save_state(self.engine.state, label=label, snapshot=snapshot)
        self.refresh_backup_info()

    def load_demo_teams(self) -> None:
        self.team_text.delete("1.0", "end")
        self.team_text.insert("1.0", "\n".join(f"Team {i:02d}" for i in range(1, TEAM_COUNT + 1)))

    def start_new_tournament(self) -> None:
        raw = self.team_text.get("1.0", "end")
        names = [line.strip() for line in raw.splitlines() if line.strip()]
        try:
            self.engine.new_tournament(names)
            self.persist_state(label="new_tournament", snapshot=True)
            self.status_var.set("Neues Turnier gestartet.")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

    def load_autosave(self) -> None:
        if not self.backup_manager.autosave_exists():
            messagebox.showinfo("Autosave", "Kein Autosave gefunden.")
            return
        try:
            self.engine.state = self.backup_manager.load_state()
            self.status_var.set("Autosave geladen.")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Ladefehler", str(exc))

    def delete_autosave(self) -> None:
        self.backup_manager.delete_autosave()
        self.status_var.set("Autosave gelöscht.")
        self.refresh_backup_info()

    def save_autosave(self) -> None:
        self.persist_state(label="manual_save", snapshot=False)
        self.status_var.set("Autosave gespeichert.")

    def save_snapshot(self) -> None:
        self.persist_state(label=f"phase_{self.engine.state.phase.lower()}", snapshot=True)
        self.status_var.set("Snapshot erstellt.")

    def rebuild_preview(self) -> None:
        self.engine.refresh_next_up_plan(relaxed=False)
        self.persist_state(label="preview_rebuild", snapshot=False)
        self.status_var.set("Next-Up Vorschau neu berechnet.")
        self.refresh_all()

    def fill_tables_normal(self) -> None:
        count = self.engine.fill_free_tables(relaxed=False)
        self.persist_state(label="fill_normal", snapshot=False)
        self.status_var.set(f"{count} Match(es) neu gestartet.")
        self.refresh_all()

    def fill_tables_relaxed(self) -> None:
        count = self.engine.fill_free_tables(relaxed=True)
        self.persist_state(label="fill_relaxed", snapshot=True)
        self.status_var.set(f"Erweiterte Suche: {count} Match(es) neu gestartet.")
        self.refresh_all()

    def on_table_selected(self, _event: object | None = None) -> None:
        table_value = self.selected_table_var.get().strip()
        if not table_value:
            self.winner_combo["values"] = []
            self.selected_winner_var.set("")
            return
        table = int(table_value)
        match = self.engine.state.active_matches.get(table)
        if not match:
            self.winner_combo["values"] = []
            self.selected_winner_var.set("")
            return
        values = [
            f"{match.team_a} - {self.engine.team_name(match.team_a)}",
            f"{match.team_b} - {self.engine.team_name(match.team_b)}",
        ]
        self.winner_combo["values"] = values
        if self.selected_winner_var.get() not in values:
            self.selected_winner_var.set(values[0])

    def on_ot_toggle(self) -> None:
        if self.ot_var.get():
            self.loser_spin.config(from_=10, to=12)
            if self.loser_cups_var.get() not in {"10", "11", "12"}:
                self.loser_cups_var.set("10")
        else:
            self.loser_spin.config(from_=0, to=9)
            try:
                value = int(self.loser_cups_var.get())
            except ValueError:
                value = 0
            if not 0 <= value <= 9:
                self.loser_cups_var.set("0")

    def submit_result_from_gui(self) -> None:
        try:
            if not self.selected_table_var.get():
                raise ValueError("Bitte zuerst einen Tisch wählen.")
            if not self.selected_winner_var.get():
                raise ValueError("Bitte Sieger-Team wählen.")

            table = int(self.selected_table_var.get())
            winner_team_id = int(self.selected_winner_var.get().split(" - ", 1)[0])
            loser_cups_hit = int(self.loser_cups_var.get())
            previous_phase = self.engine.state.phase

            self.engine.submit_result(
                table=table,
                winner_team_id=winner_team_id,
                is_overtime=self.ot_var.get(),
                loser_cups_hit=loser_cups_hit,
            )
            snapshot = previous_phase != self.engine.state.phase or self.engine.state.phase == "FINISHED"
            self.persist_state(label="result_saved", snapshot=snapshot)
            self.status_var.set(f"Ergebnis für Tisch {table} gespeichert.")
            self.selected_table_var.set("")
            self.selected_winner_var.set("")
            self.ot_var.set(False)
            self.loser_cups_var.set("0")
            self.on_ot_toggle()
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.phase_var.set(self.engine.phase_label())
        self.progress_var.set(self.engine.progress_text())
        self.refresh_active_matches()
        self.refresh_preview()
        self.refresh_ranking()
        self.refresh_logs()
        self.refresh_pairing_overview()
        self.refresh_backup_info()
        self.refresh_result_selectors()
        self.refresh_public_display()

    def refresh_active_matches(self) -> None:
        self.active_tree.delete(*self.active_tree.get_children())
        for row in self.engine.active_matches_rows():
            self.active_tree.insert(
                "",
                "end",
                values=(row["table"], row["phase"], row["slot"], row["team_a"], row["team_b"]),
            )

    def refresh_preview(self) -> None:
        self.preview_tree.delete(*self.preview_tree.get_children())
        for row in self.engine.preview_matches():
            self.preview_tree.insert(
                "",
                "end",
                values=(row["index"], row["label"], row["team_a"], row["team_b"], row["points"], row["games"], row["caps"]),
            )

    def refresh_ranking(self) -> None:
        self.ranking_tree.delete(*self.ranking_tree.get_children())
        for row in self.engine.ranking_rows():
            tags = ("top8",) if row["rank"] <= 8 else ()
            self.ranking_tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    row["rank"],
                    row["seed"],
                    row["name"],
                    row["points"],
                    row["cups"],
                    row["buchholz"],
                    row["games"],
                    row["ko_seed"] if row["ko_seed"] is not None else "-",
                ),
            )

    def refresh_logs(self) -> None:
        content = "\n".join(self.engine.state.logs[-80:]) if self.engine.state.logs else "Noch keine Einträge."
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", content)
        self.log_text.configure(state="disabled")

    def refresh_backup_info(self) -> None:
        lines: List[str] = []
        autosave_path = self.backup_manager.autosave_file
        lines.append(f"Autosave: {autosave_path}")
        lines.append(f"Vorhanden: {'Ja' if autosave_path.exists() else 'Nein'}")
        if self.backup_manager.last_save_ts is not None:
            stamp = datetime.fromtimestamp(self.backup_manager.last_save_ts).strftime('%d.%m.%Y %H:%M:%S')
            lines.append(f"Letzte Speicherung: {stamp} ({self.backup_manager.last_save_label})")
        else:
            lines.append("Letzte Speicherung: -")
        latest = self.backup_manager.latest_snapshot()
        lines.append(f"Letzter Snapshot: {latest.name if latest else '-'}")
        lines.append("")
        lines.append("Aktueller Zustand:")
        lines.append(f"  Phase: {self.engine.state.phase}")
        lines.append(f"  Teams: {len(self.engine.state.teams)}")
        lines.append(f"  Aktive Matches: {len(self.engine.state.active_matches)}")
        lines.append(f"  Beendete Matches: {len(self.engine.state.completed_matches)}")
        if self.engine.state.podium:
            podium = [self.engine.team_name(team_id) for team_id in self.engine.state.podium]
            lines.append(f"  Podest: 1. {podium[0]} | 2. {podium[1]} | 3. {podium[2]}")

        self.backup_info.configure(state="normal")
        self.backup_info.delete("1.0", "end")
        self.backup_info.insert("1.0", "\n".join(lines))
        self.backup_info.configure(state="disabled")

    def refresh_result_selectors(self) -> None:
        current_tables = [str(table) for table in sorted(self.engine.state.active_matches.keys())]
        self.table_combo["values"] = current_tables
        if self.selected_table_var.get() not in current_tables:
            self.selected_table_var.set("")
            self.selected_winner_var.set("")
            self.winner_combo["values"] = []
        else:
            self.on_table_selected()

    def refresh_pairing_overview(self) -> None:
        content = self._format_pairing_overview()
        self.pairing_text.configure(state="normal")
        self.pairing_text.delete("1.0", "end")
        self.pairing_text.insert("1.0", content)
        self.pairing_text.configure(state="disabled")

    def refresh_public_display(self) -> None:
        colors = PUBLIC_THEME
        self.player_status.configure(text=f"{self.engine.phase_label()} | {self.engine.progress_text()}")

        active_lines = []
        for row in self.engine.active_matches_rows():
            active_lines.append(f"Tisch {row['table']}: {row['team_a']}  vs  {row['team_b']}")
        if not active_lines:
            active_lines = ["Aktuell keine laufenden Spiele."]
        self.player_current_label.configure(text="\n\n".join(active_lines))

        preview_lines = []
        for row in self.engine.preview_matches():
            preview_lines.append(f"{row['label']}: {row['team_a']}  vs  {row['team_b']}")
        if not preview_lines:
            preview_lines = ["Noch keine Vorschau verfügbar."]
        self.player_next_label.configure(text="\n\n".join(preview_lines))

        ranking_text = self._format_two_column_ranking(self.engine.ranking_rows())
        self.player_ranking_text.configure(state="normal")
        self.player_ranking_text.delete("1.0", "end")
        self.player_ranking_text.insert("1.0", ranking_text)
        self.player_ranking_text.configure(state="disabled")

        ko_lines = []
        if self.engine.state.phase in {"KO", "FINISHED"}:
            for row in self.engine.knockout_rows():
                ko_lines.append(f"{row['label']}: {row['team_a']} vs {row['team_b']} [{row['status']}]")
            if self.engine.state.podium:
                podium_names = [self.engine.team_name(team_id) for team_id in self.engine.state.podium]
                ko_lines.append("")
                ko_lines.append(f"1. Platz: {podium_names[0]}")
                ko_lines.append(f"2. Platz: {podium_names[1]}")
                ko_lines.append(f"3. Platz: {podium_names[2]}")
        else:
            ko_lines = ["KO-Phase wird später eingeblendet."]
        self.player_ko_label.configure(text="\n".join(ko_lines))

    def _periodic_refresh_admin(self) -> None:
        if self.root.winfo_exists():
            self.phase_var.set(self.engine.phase_label())
            self.progress_var.set(self.engine.progress_text())
            self.refresh_result_selectors()
            self.refresh_pairing_overview()
            self.refresh_backup_info()
            self.root.after(AUTO_REFRESH_MS, self._periodic_refresh_admin)

    def _periodic_refresh_public(self) -> None:
        if self.player_window.winfo_exists():
            self.refresh_public_display()
            self.player_window.after(PLAYER_REFRESH_MS, self._periodic_refresh_public)
