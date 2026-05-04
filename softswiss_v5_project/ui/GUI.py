from __future__ import annotations

import hashlib
import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from core.backup import BackupManager
from core.config import APP_TITLE, PUBLIC_THEME, SWISS_GAMES_PER_TEAM, TEAM_COUNT, TOP_CUT
from core.models import TournamentState
from core.Swiss import SwissTournamentEngine


class TournamentGUI:
    def __init__(self, root: tk.Tk, engine: SwissTournamentEngine, backup_manager: BackupManager) -> None:
        self.root = root
        self.engine = engine
        self.backup_manager = backup_manager
        self.root.title(APP_TITLE)
        self.root.geometry("1680x980")
        self.root.minsize(1400, 820)

        self.player_window: Optional[tk.Toplevel] = None
        self.player_body: Optional[tk.Frame] = None
        self.player_fullscreen = False
        self._refresh_pending = False
        self._last_render_hash: Optional[str] = None

        self.phase_var = tk.StringVar(value="Setup")
        self.progress_var = tk.StringVar(value="Noch kein Turnier gestartet")
        self.status_var = tk.StringVar(value="Bereit.")
        self.save_var = tk.StringVar(value="Autosave: -")
        self.top_hint_var = tk.StringVar(value="Top 8 werden im Ranking grün markiert.")

        self.selected_table_var = tk.StringVar()
        self.selected_winner_var = tk.StringVar()
        self.loser_cups_var = tk.StringVar(value="0")
        self.ot_var = tk.BooleanVar(value=False)

        self.setup_widgets()
        self.refresh_all(force=True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def setup_widgets(self) -> None:
        self._configure_styles()
        self._build_admin_window()
        self._build_player_window()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = PUBLIC_THEME["bg"]
        panel = PUBLIC_THEME["panel"]
        accent = PUBLIC_THEME["accent"]
        accent_dark = PUBLIC_THEME["accent_dark"]
        text = PUBLIC_THEME["text"]

        self.root.configure(bg=bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(12, 6))
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), foreground=accent_dark, background=bg)
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), foreground=text, background=bg)
        style.configure("Muted.TLabel", foreground=PUBLIC_THEME["muted"], background=bg)
        style.configure("BigStatus.TLabel", font=("Segoe UI", 13), foreground=accent_dark, background=bg)
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 6))
        style.configure("Treeview", background="white", fieldbackground="white", foreground=text, rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "white")])
        style.configure("Card.TLabelframe", background=bg, padding=10)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"), background=bg)
        style.configure("Accent.TFrame", background=panel)

    def _build_admin_window(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(side="left")
        right = ttk.Frame(header)
        right.pack(side="right")
        ttk.Label(right, textvariable=self.phase_var, style="BigStatus.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(right, text="Spieleranzeige öffnen", command=self.show_player_display, style="Action.TButton").pack(side="left")

        ttk.Label(outer, textvariable=self.progress_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

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
        ttk.Label(self.setup_tab, text=f"{TEAM_COUNT} Teamnamen eingeben - je Zeile ein Team.", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.setup_tab,
            text="Seed = Eingabereihenfolge. Swiss-Wellen werden fix gespeichert und nicht bei jedem Refresh neu gewürfelt.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 10))

        self.team_text = tk.Text(self.setup_tab, height=24, width=48, font=("Consolas", 11), relief="solid", borderwidth=1)
        self.team_text.pack(fill="both", expand=True)

        buttons = ttk.Frame(self.setup_tab)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Demo-Teams", command=self.load_demo_teams).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Neues Turnier starten", command=self.start_new_tournament).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

    def _build_control_tab(self) -> None:
        pane = ttk.Panedwindow(self.control_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=4)

        current_frame = ttk.LabelFrame(left, text="Laufende Welle", style="Card.TLabelframe")
        current_frame.pack(fill="both", expand=False, pady=(0, 10))
        self.current_wave_tree = ttk.Treeview(
            current_frame,
            columns=("order", "slot", "team_a", "team_b", "status", "note"),
            show="headings",
            height=7,
        )
        for col, title, width in [
            ("order", "#", 36),
            ("slot", "Slot", 80),
            ("team_a", "Team A", 180),
            ("team_b", "Team B", 180),
            ("status", "Status", 86),
            ("note", "Info", 180),
        ]:
            self.current_wave_tree.heading(col, text=title)
            self.current_wave_tree.column(col, width=width, anchor="center")
        self.current_wave_tree.pack(fill="x", expand=False)

        next_frame = ttk.LabelFrame(left, text="Vorbereitete Welle", style="Card.TLabelframe")
        next_frame.pack(fill="both", expand=False, pady=(0, 10))
        self.prepared_wave_tree = ttk.Treeview(
            next_frame,
            columns=("order", "slot", "team_a", "team_b", "status", "note"),
            show="headings",
            height=7,
        )
        for col, title, width in [
            ("order", "#", 36),
            ("slot", "Slot", 80),
            ("team_a", "Team A", 180),
            ("team_b", "Team B", 180),
            ("status", "Status", 86),
            ("note", "Info", 180),
        ]:
            self.prepared_wave_tree.heading(col, text=title)
            self.prepared_wave_tree.column(col, width=width, anchor="center")
        self.prepared_wave_tree.pack(fill="x", expand=False)

        entry_frame = ttk.LabelFrame(left, text="Ergebnis eintragen", style="Card.TLabelframe")
        entry_frame.pack(fill="x", pady=(0, 10))
        for idx in range(4):
            entry_frame.columnconfigure(idx, weight=1)

        ttk.Label(entry_frame, text="Tisch").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.table_combo = ttk.Combobox(entry_frame, textvariable=self.selected_table_var, state="readonly", width=12)
        self.table_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.table_combo.bind("<<ComboboxSelected>>", self.on_table_selected)

        ttk.Label(entry_frame, text="Sieger").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=(0, 8))
        self.winner_combo = ttk.Combobox(entry_frame, textvariable=self.selected_winner_var, state="readonly", width=28)
        self.winner_combo.grid(row=0, column=3, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(entry_frame, text="OT", variable=self.ot_var, command=self.on_ot_toggle).grid(row=1, column=0, sticky="w")
        ttk.Label(entry_frame, text="Verlierer-Becher").grid(row=1, column=2, sticky="w", padx=(12, 8))
        self.loser_spin = ttk.Spinbox(entry_frame, textvariable=self.loser_cups_var, from_=0, to=12, width=10)
        self.loser_spin.grid(row=1, column=3, sticky="w")

        self.ot_hint = ttk.Label(
            entry_frame,
            text="Normal: 0-9 | OT: 10-12 | OT: Sieger bekommt Differenz, Verlierer 0",
            style="Muted.TLabel",
        )
        self.ot_hint.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Button(entry_frame, text="Ergebnis speichern", command=self.submit_result_from_gui, style="Action.TButton").grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )

        actions = ttk.LabelFrame(left, text="Aktionen", style="Card.TLabelframe")
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Freie Tische auffüllen", command=self.fill_tables_normal).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Erweiterte Suche manuell", command=self.fill_tables_relaxed).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Next-Up neu anzeigen", command=self.rebuild_preview).pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Autosave jetzt", command=self.save_autosave).pack(fill="x")

        backup_status = ttk.LabelFrame(left, text="Autosave", style="Card.TLabelframe")
        backup_status.pack(fill="x")
        ttk.Label(backup_status, textvariable=self.save_var, style="Muted.TLabel").pack(anchor="w")

        ranking_frame = ttk.LabelFrame(right, text="Live-Ranking", style="Card.TLabelframe")
        ranking_frame.pack(fill="both", expand=True, pady=(0, 10))
        self.ranking_tree = ttk.Treeview(
            ranking_frame,
            columns=("rank", "seed", "team", "pts", "cups", "bh", "rounds"),
            show="headings",
            height=18,
        )
        for col, title, width in [
            ("rank", "#", 38),
            ("seed", "Seed", 55),
            ("team", "Team", 200),
            ("pts", "Pkt", 55),
            ("cups", "Cups", 70),
            ("bh", "Buchholz", 86),
            ("rounds", "Runden", 68),
        ]:
            self.ranking_tree.heading(col, text=title)
            self.ranking_tree.column(col, width=width, anchor="center")
        self.ranking_tree.pack(fill="both", expand=True)
        self.ranking_tree.tag_configure("top8", background=PUBLIC_THEME["top8"])
        self.ranking_tree.tag_configure("top8strong", background=PUBLIC_THEME["top8_strong"])

        preview_frame = ttk.LabelFrame(right, text="Als Nächstes", style="Card.TLabelframe")
        preview_frame.pack(fill="x", pady=(0, 10))
        self.preview_tree = ttk.Treeview(
            preview_frame,
            columns=("idx", "team_a", "team_b", "status"),
            show="headings",
            height=3,
        )
        for col, title, width in [
            ("idx", "#", 35),
            ("team_a", "Team A", 180),
            ("team_b", "Team B", 180),
            ("status", "Status", 180),
        ]:
            self.preview_tree.heading(col, text=title)
            self.preview_tree.column(col, width=width, anchor="center")
        self.preview_tree.pack(fill="x")

    def _build_backup_tab(self) -> None:
        ttk.Label(self.backup_tab, text="Autosave und Snapshots werden lokal als JSON gespeichert.", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.backup_tab, textvariable=self.save_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 10))

        actions = ttk.Frame(self.backup_tab)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Autosave speichern", command=self.save_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Snapshot erstellen", command=self.save_snapshot).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

        self.backup_info = tk.Text(self.backup_tab, height=18, font=("Consolas", 10), state="disabled", wrap="word")
        self.backup_info.pack(fill="both", expand=True)

    def _build_player_window(self) -> None:
        self.player_window = tk.Toplevel(self.root)
        self.player_window.title("Spieleranzeige")
        self.player_window.geometry("1680x980")
        self.player_window.configure(bg=PUBLIC_THEME["bg"])
        self.player_window.protocol("WM_DELETE_WINDOW", self.player_window.withdraw)

        self.player_header = tk.Frame(self.player_window, bg=PUBLIC_THEME["bg"], padx=18, pady=14)
        self.player_header.pack(fill="x")
        self.player_title = tk.Label(
            self.player_header,
            text="Beerpong Turnier",
            fg=PUBLIC_THEME["text"],
            bg=PUBLIC_THEME["bg"],
            font=("Segoe UI", 28, "bold"),
        )
        self.player_title.pack(side="left")
        self.player_status = tk.Label(
            self.player_header,
            text="",
            fg=PUBLIC_THEME["accent_dark"],
            bg=PUBLIC_THEME["bg"],
            font=("Segoe UI", 18),
        )
        self.player_status.pack(side="left", padx=(18, 0))
        tk.Button(
            self.player_header,
            text="Vollbild",
            command=self.toggle_player_fullscreen,
            bg=PUBLIC_THEME["card"],
            fg=PUBLIC_THEME["text"],
            activebackground=PUBLIC_THEME["panel"],
            activeforeground=PUBLIC_THEME["text"],
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right")

        self.player_body = tk.Frame(self.player_window, bg=PUBLIC_THEME["bg"], padx=18, pady=6)
        self.player_body.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _state_hash(self) -> str:
        payload = self.engine.state.to_dict()
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def request_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def _do_refresh() -> None:
            self._refresh_pending = False
            self.refresh_all()

        self.root.after_idle(_do_refresh)

    def refresh_all(self, force: bool = False) -> None:
        state_hash = self._state_hash()
        if not force and state_hash == self._last_render_hash:
            return
        self._last_render_hash = state_hash
        self.phase_var.set(self.engine.phase_label())
        self.progress_var.set(self.engine.progress_text())
        self.save_var.set(self._format_save_info())
        self._refresh_table_combo()
        self._refresh_winner_combo()
        self._refresh_wave_tree()
        self._refresh_prepared_wave_tree()
        self._refresh_ranking_tree()
        self._refresh_preview_tree()
        self._refresh_backup_info()
        self._refresh_player_view()


    def _format_save_info(self) -> str:
        if self.engine.state.last_save_ts <= 0:
            return "Autosave: -"
        from datetime import datetime

        ts = datetime.fromtimestamp(self.engine.state.last_save_ts).strftime("%d.%m.%Y %H:%M:%S")
        return f"Autosave: {ts} | Label: {self.engine.state.last_save_label} | Count: {self.engine.state.autosave_count}"

    def _refresh_table_combo(self) -> None:
        rows = self.engine.active_matches_rows()
        values = [f"T{row['table']} - {row['team_a']} vs {row['team_b']}" for row in rows]
        self.table_combo["values"] = values
        if values and self.selected_table_var.get() not in values:
            self.selected_table_var.set(values[0])
        if not values:
            self.selected_table_var.set("")

    def _selected_table_number(self) -> Optional[int]:
        value = self.selected_table_var.get()
        if not value.startswith("T"):
            return None
        try:
            return int(value.split("-")[0].strip()[1:])
        except Exception:
            return None

    def _refresh_winner_combo(self) -> None:
        table = self._selected_table_number()
        winners: List[str] = []
        if table is not None:
            match = self.engine.state.matches.get(self.engine.state.active_tables.get(table, ""))
            if match:
                winners = [self.engine.team_name(match.team_a), self.engine.team_name(match.team_b)]
        self.winner_combo["values"] = winners
        if winners and self.selected_winner_var.get() not in winners:
            self.selected_winner_var.set(winners[0])
        if not winners:
            self.selected_winner_var.set("")

    def _refresh_wave_tree(self) -> None:
        for item in self.current_wave_tree.get_children():
            self.current_wave_tree.delete(item)
        for row in self.engine.wave_rows():
            tag = ""
            if row["status"] == "active":
                tag = "selected"
            elif row["status"] == "finished":
                tag = "top8"
            self.current_wave_tree.insert(
                "",
                "end",
                values=(row["order"], row["slot"], row["team_a"], row["team_b"], row["status"], row["note"]),
                tags=(tag,) if tag else (),
            )

    def _refresh_ranking_tree(self) -> None:
        for item in self.ranking_tree.get_children():
            self.ranking_tree.delete(item)
        rows = self.engine.ranking_rows()
        for row in rows:
            tags = ()
            if row["rank"] <= TOP_CUT:
                tags = ("top8strong" if row["rank"] == TOP_CUT else "top8",)
            self.ranking_tree.insert(
                "",
                "end",
                values=(row["rank"], row["seed"], row["name"], row["points"], row["cups"], row["buchholz"], row["games"]),
                tags=tags,
            )

    def _refresh_preview_tree(self) -> None:
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        rows = self.engine.preview_matches()
        for idx, row in enumerate(rows, start=1):
            self.preview_tree.insert("", "end", values=(idx, row["team_a"], row["team_b"], row["status"]))

    def _refresh_prepared_wave_tree(self) -> None:
        for item in self.prepared_wave_tree.get_children():
            self.prepared_wave_tree.delete(item)
        for row in self.engine.prepared_wave_rows():
            tag = ""
            if row["status"] == "active":
                tag = "selected"
            self.prepared_wave_tree.insert(
                "",
                "end",
                values=(row["order"], row["slot"], row["team_a"], row["team_b"], row["status"], row["note"]),
                tags=(tag,) if tag else (),
            )

    def _refresh_backup_info(self) -> None:
        content = [
            f"Phase: {self.engine.phase_label()}",
            f"Letzter Save: {self.save_var.get()}",
            f"Autosave vorhanden: {'ja' if self.backup_manager.autosave_exists() else 'nein'}",
        ]
        latest_snapshot = self.backup_manager.latest_snapshot()
        content.append(f"Letzter Snapshot: {latest_snapshot.name if latest_snapshot else '-'}")
        self.backup_info.configure(state="normal")
        self.backup_info.delete("1.0", "end")
        self.backup_info.insert("1.0", "\n".join(content))
        self.backup_info.configure(state="disabled")

    def _refresh_player_view(self) -> None:
        if self.player_window is None or self.player_body is None:
            return

        for child in self.player_body.winfo_children():
            child.destroy()

        self.player_title.config(text="Beerpong Turnier")
        self.player_status.config(text=self.engine.phase_label())

        if self.engine.state.phase == "KO" or self.engine.state.phase == "FINISHED":
            self._render_player_knockout_view()
        else:
            self._render_player_swiss_view()

    def _public_card(self, parent: tk.Widget, title: str) -> tk.Frame:
        frame = tk.Frame(parent, bg=PUBLIC_THEME["card"], padx=14, pady=12, highlightthickness=1, highlightbackground=PUBLIC_THEME["line"])
        tk.Label(frame, text=title, fg=PUBLIC_THEME["accent_dark"], bg=PUBLIC_THEME["card"], font=("Segoe UI", 16, "bold"), anchor="w").pack(anchor="w", pady=(0, 8))
        return frame

    def _render_player_swiss_view(self) -> None:
        content = self.player_body
        if content is None:
            return
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=2)

        live_card = self._public_card(content, "Laufende Matches")
        live_card.grid(row=0, column=0, sticky="nsew", padx=(0, 9), pady=(0, 10))
        next_card = self._public_card(content, "Als Nächstes")
        next_card.grid(row=0, column=1, sticky="nsew", padx=(9, 0), pady=(0, 10))
        ranking_card = self._public_card(content, "Live-Ranking")
        ranking_card.grid(row=1, column=0, columnspan=2, sticky="nsew")

        active = self.engine.active_matches_rows()
        if not active:
            tk.Label(live_card, text="Noch keine aktiven Spiele.", bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 16)).pack(anchor="w")
        else:
            for row in active:
                txt = f"T{row['table']}: {row['team_a']} vs {row['team_b']}"
                tk.Label(live_card, text=txt, bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 15), anchor="w").pack(anchor="w", pady=2)

        preview = self.engine.preview_matches()
        if not preview:
            tk.Label(next_card, text="Es gibt keine Matches mehr", bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 16)).pack(anchor="w")
        else:
            for row in preview:
                txt = f"{row['team_a']} vs {row['team_b']}"
                tk.Label(next_card, text=txt, bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 15), anchor="w").pack(anchor="w", pady=2)
                tk.Label(next_card, text=row.get("status", ""), bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["muted"], font=("Segoe UI", 11), anchor="w").pack(anchor="w", pady=(0, 6))

        ranking_tree = ttk.Treeview(ranking_card, columns=("rank", "seed", "team", "pts", "cups", "bh", "rounds"), show="headings", height=18)
        for col, title, width in [
            ("rank", "#", 40),
            ("seed", "Seed", 60),
            ("team", "Team", 220),
            ("pts", "Pkt", 55),
            ("cups", "Cups", 70),
            ("bh", "Buchholz", 86),
            ("rounds", "Runden", 68),
        ]:
            ranking_tree.heading(col, text=title)
            ranking_tree.column(col, width=width, anchor="center")
        ranking_tree.pack(fill="both", expand=True)
        ranking_tree.tag_configure("top8", background=PUBLIC_THEME["top8"])
        ranking_tree.tag_configure("top8strong", background=PUBLIC_THEME["top8_strong"])
        for row in self.engine.ranking_rows():
            tags = ()
            if row["rank"] <= TOP_CUT:
                tags = ("top8strong" if row["rank"] == TOP_CUT else "top8",)
            ranking_tree.insert("", "end", values=(row["rank"], row["seed"], row["name"], row["points"], row["cups"], row["buchholz"], row["games"]), tags=tags)

    def _render_player_knockout_view(self) -> None:
        content = self.player_body
        if content is None:
            return
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=0)

        bracket_card = self._public_card(content, "KO-Turnierbaum")
        bracket_card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        canvas = tk.Canvas(bracket_card, bg=PUBLIC_THEME["card"], highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._draw_bracket_canvas(canvas)

        podium_card = self._public_card(content, "Podest")
        podium_card.grid(row=1, column=0, sticky="ew")
        podium_text = self._format_podium_text()
        tk.Label(podium_card, text=podium_text, bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 16, "bold"), justify="left", anchor="w").pack(anchor="w")

    def _format_podium_text(self) -> str:
        if len(self.engine.state.podium) >= 3:
            return "1. %s\n2. %s\n3. %s" % (
                self.engine.team_name(self.engine.state.podium[0]),
                self.engine.team_name(self.engine.state.podium[1]),
                self.engine.team_name(self.engine.state.podium[2]),
            )
        return "Podest folgt nach den letzten KO-Spielen."

    def _draw_bracket_canvas(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        rows = self.engine.knockout_rows()
        width = max(canvas.winfo_width(), 1200)
        height = max(canvas.winfo_height(), 560)
        if width <= 1 or height <= 1:
            width, height = 1200, 560

        positions = {
            "QF": [(70, 70), (70, 180), (70, 290), (70, 400)],
            "SF": [(420, 125), (420, 345)],
            "FINAL": [(780, 180)],
            "3RD": [(780, 320)],
        }

        def draw_box(x: int, y: int, w: int, h: int, title: str, team_a: str, team_b: str, status: str, winner: str) -> None:
            fill = PUBLIC_THEME["highlight"] if status == "active" else PUBLIC_THEME["card"]
            outline = PUBLIC_THEME["accent_dark"] if status == "finished" else PUBLIC_THEME["line"]
            canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=2)
            canvas.create_text(x + 12, y + 10, anchor="nw", text=title, fill=PUBLIC_THEME["accent_dark"], font=("Segoe UI", 13, "bold"))
            canvas.create_text(x + 12, y + 36, anchor="nw", text=team_a, fill=PUBLIC_THEME["text"], font=("Segoe UI", 12, "bold"))
            canvas.create_text(x + 12, y + 58, anchor="nw", text=team_b, fill=PUBLIC_THEME["text"], font=("Segoe UI", 12, "bold"))
            if status == "finished":
                canvas.create_text(x + w - 12, y + h - 12, anchor="se", text=f"Sieger: {winner}", fill=PUBLIC_THEME["ok"], font=("Segoe UI", 10, "bold"))
            elif status == "active":
                canvas.create_text(x + w - 12, y + h - 12, anchor="se", text="läuft", fill=PUBLIC_THEME["warn"], font=("Segoe UI", 10, "bold"))
            else:
                canvas.create_text(x + w - 12, y + h - 12, anchor="se", text="wartet", fill=PUBLIC_THEME["muted"], font=("Segoe UI", 10, "bold"))

        def row_for(stage: str, label: str):
            for row in rows:
                if row["stage"] == stage and row["label"] == label:
                    return row
            return None

        for idx, (x, y) in enumerate(positions["QF"], start=1):
            label = f"Viertelfinale {idx}"
            row = row_for("QF", label)
            if row:
                draw_box(x, y, 260, 70, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        for idx, (x, y) in enumerate(positions["SF"], start=1):
            label = f"Halbfinale {idx}"
            row = row_for("SF", label)
            if row:
                draw_box(x, y, 280, 70, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        for stage, label, (x, y) in [("FINAL", "Finale", positions["FINAL"][0]), ("3RD", "Spiel um Platz 3", positions["3RD"][0])]:
            row = row_for(stage, label)
            if row:
                draw_box(x, y, 320, 70, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        # Connectors.
        def line(x1: int, y1: int, x2: int, y2: int) -> None:
            canvas.create_line(x1, y1, x2, y2, fill=PUBLIC_THEME["accent_dark"], width=3)

        # QF -> SF
        line(330, 105, 390, 160)
        line(330, 215, 390, 160)
        line(330, 325, 390, 380)
        line(330, 435, 390, 380)
        # SF -> Final / 3rd
        line(700, 160, 760, 215)
        line(700, 380, 760, 215)
        line(700, 160, 760, 355)
        line(700, 380, 760, 355)

        canvas.create_text(70, 25, anchor="nw", text="KO-Phase wird erst nach Abschluss der Swiss-Wellen angezeigt.", fill=PUBLIC_THEME["muted"], font=("Segoe UI", 12))

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
        if self.player_window is not None:
            self.player_window.deiconify()
            self.player_window.lift()

    def toggle_player_fullscreen(self) -> None:
        if self.player_window is None:
            return
        self.player_fullscreen = not self.player_fullscreen
        self.player_window.attributes("-fullscreen", self.player_fullscreen)

    def persist_state(self, label: str = "autosave", snapshot: bool = False) -> None:
        self.backup_manager.save_state(self.engine.state, label=label, snapshot=snapshot)
        self.request_refresh()

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
        self.refresh_all()

    def save_autosave(self) -> None:
        self.persist_state(label="autosave", snapshot=False)
        self.status_var.set("Autosave gespeichert.")

    def save_snapshot(self) -> None:
        self.persist_state(label=f"phase_{self.engine.state.phase.lower()}", snapshot=True)
        self.status_var.set("Snapshot erstellt.")

    def rebuild_preview(self) -> None:
        # The preview is derived from the current state and is only redrawn on demand.
        self.refresh_all(force=True)
        self.status_var.set("Vorschau aktualisiert.")

    def fill_tables_normal(self) -> None:
        count = self.engine.fill_free_tables(relaxed=False)
        self.persist_state(label="fill_normal", snapshot=False)
        self.status_var.set(f"{count} Match(es) gestartet.")
        self.refresh_all()

    def fill_tables_relaxed(self) -> None:
        count = self.engine.fill_free_tables(relaxed=True)
        self.persist_state(label="fill_relaxed", snapshot=True)
        self.status_var.set(f"Erweiterte Suche: {count} Match(es) gestartet.")
        self.refresh_all()

    def on_table_selected(self, _event: object = None) -> None:
        self._refresh_winner_combo()

    def on_ot_toggle(self) -> None:
        self.loser_cups_var.set("10" if self.ot_var.get() else "0")

    def submit_result_from_gui(self) -> None:
        table = self._selected_table_number()
        if table is None:
            messagebox.showerror("Fehler", "Bitte zuerst einen Tisch auswählen.")
            return
        winner_name = self.selected_winner_var.get()
        if not winner_name:
            messagebox.showerror("Fehler", "Bitte einen Sieger auswählen.")
            return
        try:
            loser_cups = int(self.loser_cups_var.get())
        except ValueError:
            messagebox.showerror("Fehler", "Bitte einen gültigen Cups-Wert eingeben.")
            return

        match_id = self.engine.state.active_tables.get(table)
        if not match_id:
            messagebox.showerror("Fehler", "Auf diesem Tisch läuft kein Match.")
            return
        match = self.engine.state.matches[match_id]
        winner_id = match.team_a if self.engine.team_name(match.team_a) == winner_name else match.team_b

        try:
            self.engine.submit_result(table, winner_id, self.ot_var.get(), loser_cups)
            self.persist_state(label="result_entry", snapshot=False)
            self.status_var.set("Ergebnis gespeichert.")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _refresh_backup_info(self) -> None:
        content = [
            f"Phase: {self.engine.phase_label()}",
            f"Letzter Save: {self.save_var.get()}",
            f"Autosave vorhanden: {'ja' if self.backup_manager.autosave_exists() else 'nein'}",
        ]
        latest_snapshot = self.backup_manager.latest_snapshot()
        content.append(f"Letzter Snapshot: {latest_snapshot.name if latest_snapshot else '-'}")
        self.backup_info.configure(state="normal")
        self.backup_info.delete("1.0", "end")
        self.backup_info.insert("1.0", "\n".join(content))
        self.backup_info.configure(state="disabled")
