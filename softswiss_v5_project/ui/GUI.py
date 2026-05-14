from __future__ import annotations

import hashlib
import json
import tkinter as tk
from tkinter import messagebox, ttk
from turtle import right
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
        self._last_player_render_hash: Optional[str] = None

        self.phase_var = tk.StringVar(value="Setup")
        self.progress_var = tk.StringVar(value="Noch kein Turnier gestartet")
        self.status_var = tk.StringVar(value="Bereit.")
        self.save_var = tk.StringVar(value="Autosave: -")
        self.top_hint_var = tk.StringVar(value="Top 8 kommen weiter")

        self.selected_table_var = tk.StringVar()
        self.selected_winner_var = tk.StringVar()
        self.loser_cups_var = tk.StringVar(value="0")
        self.ot_var = tk.BooleanVar(value=False)
        self.ko_button: Optional[ttk.Button] = None
        self.undo_button: Optional[ttk.Button] = None
        self.edit_button: Optional[ttk.Button] = None
        self.reset_match_button: Optional[ttk.Button] = None
        self.fill_button: Optional[ttk.Button] = None
        self.save_now_button: Optional[ttk.Button] = None
        self.recompute_button: Optional[ttk.Button] = None

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
        card = PUBLIC_THEME["card"]
        surface = PUBLIC_THEME["surface_alt"]
        muted = PUBLIC_THEME["muted"]

        self.root.configure(bg=bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("TNotebook", background=bg, borderwidth=0, tabmargins=(0, 6, 0, 0))
        style.configure("TNotebook.Tab", background=surface, foreground=muted, font=("Segoe UI", 10, "bold"), padding=(16, 8), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", card), ("active", PUBLIC_THEME["surface"])], foreground=[("selected", text), ("active", text)])
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), foreground=accent_dark, background=bg)
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), foreground=text, background=bg)
        style.configure("Muted.TLabel", foreground=muted, background=bg)
        style.configure("BigStatus.TLabel", font=("Segoe UI", 13), foreground=accent_dark, background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Card.TLabel", background=card, foreground=text, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=card, foreground=text, font=("Segoe UI", 13, "bold"))
        style.configure("CardMuted.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 9), background=accent, foreground="white")
        style.map(
            "Primary.TButton",
            background=[("disabled", PUBLIC_THEME["line"]), ("active", accent_dark), ("pressed", accent_dark)],
            foreground=[("disabled", muted), ("active", "white"), ("pressed", "white")],
        )
        style.configure(
            "Treeview",
            background=card,
            fieldbackground=card,
            foreground=text,
            rowheight=34,
            font=("Segoe UI", 10),
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            background=surface,
            foreground=PUBLIC_THEME["text_soft"],
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
            relief="flat",
            padding=(8, 6),
        )
        style.map("Treeview", background=[("selected", PUBLIC_THEME["accent_soft"])], foreground=[("selected", text)])
        style.configure("Card.TLabelframe", background=bg, padding=10)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"), background=bg)
        style.configure("Accent.TFrame", background=panel)

    def _admin_card(self, parent: tk.Widget, title: str, subtitle: str = "", padding: tuple[int, int] = (16, 14)) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(parent, bg=PUBLIC_THEME["shadow"], highlightthickness=0)
        body = tk.Frame(
            outer,
            bg=PUBLIC_THEME["card"],
            padx=padding[0],
            pady=padding[1],
            highlightthickness=1,
            highlightbackground=PUBLIC_THEME["line_soft"],
        )
        body.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))

        tk.Label(
            body,
            text=title,
            bg=PUBLIC_THEME["card"],
            fg=PUBLIC_THEME["text"],
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(fill="x", anchor="w")
        if subtitle:
            tk.Label(
                body,
                text=subtitle,
                bg=PUBLIC_THEME["card"],
                fg=PUBLIC_THEME["muted"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x", anchor="w", pady=(2, 10))
        else:
            tk.Frame(body, bg=PUBLIC_THEME["card"], height=8).pack(fill="x")
        content = tk.Frame(body, bg=PUBLIC_THEME["card"])
        content.pack(fill="both", expand=True)
        return outer, content

    def _scrollable_admin_column(self, parent: tk.Widget, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(parent, bg=PUBLIC_THEME["bg"])
        shell = tk.Frame(outer, bg=PUBLIC_THEME["bg"])
        shell.pack(fill="both", expand=True, padx=(padding[0], padding[2]), pady=(padding[1], padding[3]))

        canvas = tk.Canvas(shell, bg=PUBLIC_THEME["bg"], highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=PUBLIC_THEME["bg"])
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _update_scrollregion(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_scroll_up(_event: tk.Event) -> None:
            canvas.yview_scroll(-3, "units")

        def _on_scroll_down(_event: tk.Event) -> None:
            canvas.yview_scroll(3, "units")

        def _bind_mousewheel(_event: tk.Event) -> None:
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_scroll_up)
            canvas.bind_all("<Button-5>", _on_scroll_down)

        def _unbind_mousewheel(_event: tk.Event) -> None:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        content.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _sync_width)
        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)
        content.bind("<Enter>", _bind_mousewheel)
        content.bind("<Leave>", _unbind_mousewheel)
        shell.bind("<Enter>", _bind_mousewheel)
        shell.bind("<Leave>", _unbind_mousewheel)
        return outer, content

    def _style_tree_tags(self, tree: ttk.Treeview) -> None:
        tree.tag_configure("top8", background=PUBLIC_THEME["top8"])
        tree.tag_configure("top8strong", background=PUBLIC_THEME["top8_strong"])
        tree.tag_configure("active", background=PUBLIC_THEME["highlight_soft"])
        tree.tag_configure("finished", background=PUBLIC_THEME["ok_soft"])
        tree.tag_configure("ready", background=PUBLIC_THEME["row_alt"])

    def _set_button_enabled(self, button: ttk.Button, enabled: bool) -> None:
        button.state(["!disabled"] if enabled else ["disabled"])

    def _shorten(self, value: str, max_chars: int = 34) -> str:
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

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
        self.ranking_tab = ttk.Frame(notebook, padding=12)
        self.backup_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.setup_tab, text="Setup")
        notebook.add(self.control_tab, text="Turnierleitung")
        notebook.add(self.ranking_tab, text="Live-Ranking")
        notebook.add(self.backup_tab, text="Backup")

        self._build_setup_tab()
        self._build_control_tab()
        self._build_Live_Ranking_tab()
        self._build_backup_tab()

    def _build_setup_tab(self) -> None:
        setup_shell, setup_card = self._admin_card(
            self.setup_tab,
            f"{TEAM_COUNT} Teamnamen eingeben",
            "Je Zeile ein Team. Seed ist die Eingabereihenfolge; Swiss-Wellen bleiben fix gespeichert.",
            padding=(18, 16),
        )
        setup_shell.pack(fill="both", expand=True)

        self.team_text = tk.Text(
            setup_card,
            height=24,
            width=48,
            font=("Consolas", 11),
            relief="flat",
            borderwidth=0,
            bg=PUBLIC_THEME["surface"],
            fg=PUBLIC_THEME["text"],
            insertbackground=PUBLIC_THEME["accent_dark"],
            selectbackground=PUBLIC_THEME["accent_soft"],
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=PUBLIC_THEME["line_soft"],
        )
        self.team_text.pack(fill="both", expand=True)

        buttons = tk.Frame(setup_card, bg=PUBLIC_THEME["card"])
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Demo-Teams", command=self.load_demo_teams).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Neues Turnier starten", command=self.start_new_tournament).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

    def _build_Live_Ranking_tab(self) -> None:
        ranking_shell, ranking_frame = self._admin_card(
            self.ranking_tab,
            "Live-Ranking",
            "Punkte, Cups, Buchholz und Spiele",
            padding=(18, 16),
        )
        ranking_shell.pack(fill="both", expand=True)
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
        self._style_tree_tags(self.ranking_tree)
        self.ranking_tree.pack(fill="both", expand=True)

    def _build_control_tab(self) -> None:
        pane = ttk.Panedwindow(self.control_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane, padding=(0, 0, 12, 0))
        right_shell, right = self._scrollable_admin_column(pane, padding=(12, 0, 0, 0))
        pane.add(left, weight=3)
        pane.add(right_shell, weight=4)

        preview_shell, preview_frame = self._admin_card(right, "Next Match", "Vorbereitete Paarungen für freie Tische")
        preview_shell.pack(fill="x", pady=(0, 12))
        self.preview_tree = ttk.Treeview(
            preview_frame,
            columns=("idx", "team_a", "team_b", "status"),
            show="headings",
            height=3,
        )

        current_shell, current_frame = self._admin_card(right, "Laufende Matches", "Aktive und gerade freigegebene Slots")
        current_shell.pack(fill="both", expand=False, pady=(0, 12))
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
        self._style_tree_tags(self.current_wave_tree)
        self.current_wave_tree.pack(fill="x", expand=False)

        next_shell, next_frame = self._admin_card(right, "Vorbereitete Matches", "Bereit, sobald ein Tisch frei wird")
        next_shell.pack(fill="both", expand=False, pady=(0, 12))
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
        self._style_tree_tags(self.prepared_wave_tree)
        self.prepared_wave_tree.pack(fill="x", expand=False)

        entry_shell, entry_frame = self._admin_card(left, "Ergebnis eintragen", "Schnelle Eingabe für laufende Tische")
        entry_shell.pack(fill="x", pady=(0, 12))
        for idx in range(4):
            entry_frame.columnconfigure(idx, weight=1)

        ttk.Label(entry_frame, text="Tisch", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.table_combo = ttk.Combobox(entry_frame, textvariable=self.selected_table_var, state="readonly", width=35)
        self.table_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.table_combo.bind("<<ComboboxSelected>>", self.on_table_selected)

        ttk.Label(entry_frame, text="Sieger", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=(0, 8))
        self.winner_combo = ttk.Combobox(entry_frame, textvariable=self.selected_winner_var, state="readonly", width=28)
        self.winner_combo.grid(row=0, column=3, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(entry_frame, text="Verlängerung", variable=self.ot_var, command=self.on_ot_toggle).grid(row=1, column=0, sticky="w")
        ttk.Label(entry_frame, text="Verlierer-Becher", style="Card.TLabel").grid(row=1, column=2, sticky="w", padx=(12, 8))
        self.loser_spin = ttk.Spinbox(entry_frame, textvariable=self.loser_cups_var, from_=0, to=12, width=10)
        self.loser_spin.grid(row=1, column=3, sticky="w")

        self.ot_hint = ttk.Label(
            entry_frame,
            text="Normal: 0-9 | OT: 10-12 | OT: Sieger bekommt Differenz, Verlierer 0",
            style="CardMuted.TLabel",
        )
        self.ot_hint.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Button(entry_frame, text="Ergebnis speichern", command=self.submit_result_from_gui, style="Action.TButton").grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )

        actions_shell, actions = self._admin_card(left, "Steuerung", "Korrekturen, Betrieb und Turnierphasen")
        actions_shell.pack(fill="x", pady=(0, 12))
        ttk.Label(actions, text="Korrektur", style="CardTitle.TLabel").pack(anchor="w")
        self.undo_button = ttk.Button(actions, text="Letzte Eingabe zurück", command=self.undo_last_result_from_gui)
        self.undo_button.pack(fill="x", pady=(6, 6))
        self.edit_button = ttk.Button(actions, text="Match bearbeiten", command=self.open_edit_match_dialog)
        self.edit_button.pack(fill="x", pady=(0, 6))
        self.reset_match_button = ttk.Button(actions, text="Match zurücksetzen", command=self.open_reset_match_dialog)
        self.reset_match_button.pack(fill="x", pady=(0, 12))

        ttk.Label(actions, text="Betrieb", style="CardTitle.TLabel").pack(anchor="w")
        self.fill_button = ttk.Button(actions, text="Freie Plätze auffüllen", command=self.fill_tables_normal)
        self.fill_button.pack(fill="x", pady=(6, 6))
        self.save_now_button = ttk.Button(actions, text="Jetzt speichern", command=self.save_now_from_gui)
        self.save_now_button.pack(fill="x", pady=(0, 6))
        self.recompute_button = ttk.Button(actions, text="Nächste Welle neu berechnen", command=self.recompute_pairing_from_gui)
        self.recompute_button.pack(fill="x", pady=(0, 12))

        ttk.Label(actions, text="Turnier", style="CardTitle.TLabel").pack(anchor="w")
        self.ko_button = ttk.Button(actions, text="KO starten", command=self.start_knockout_from_gui, style="Primary.TButton")
        self.ko_button.pack(fill="x")

        backup_shell, backup_status = self._admin_card(left, "Autosave", "", padding=(16, 12))
        backup_shell.pack(fill="x")
        ttk.Label(backup_status, textvariable=self.save_var, style="CardMuted.TLabel").pack(anchor="w")


        for col, title, width in [
            ("idx", "#", 35),
            ("team_a", "Team A", 180),
            ("team_b", "Team B", 180),
            ("status", "Status", 180),
        ]:
            self.preview_tree.heading(col, text=title)
            self.preview_tree.column(col, width=width, anchor="center")
        self._style_tree_tags(self.preview_tree)
        self.preview_tree.pack(fill="x")

    def _build_backup_tab(self) -> None:
        backup_shell, backup_card = self._admin_card(
            self.backup_tab,
            "Backup",
            "Autosave und Snapshots werden lokal als JSON gespeichert.",
            padding=(18, 16),
        )
        backup_shell.pack(fill="both", expand=True)
        ttk.Label(backup_card, textvariable=self.save_var, style="CardMuted.TLabel").pack(anchor="w", pady=(0, 12))

        actions = tk.Frame(backup_card, bg=PUBLIC_THEME["card"])
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Autosave speichern", command=self.save_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Snapshot erstellen", command=self.save_snapshot).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave laden", command=self.load_autosave).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Autosave löschen", command=self.delete_autosave).pack(side="left")

        self.backup_info = tk.Text(
            backup_card,
            height=18,
            font=("Consolas", 10),
            state="disabled",
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=PUBLIC_THEME["surface"],
            fg=PUBLIC_THEME["text"],
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=PUBLIC_THEME["line_soft"],
        )
        self.backup_info.pack(fill="both", expand=True)

    def _build_player_window(self) -> None:
        self.player_window = tk.Toplevel(self.root)
        self.player_window.title("Spieleranzeige")
        self.player_window.geometry("1680x980")
        self.player_window.configure(bg=PUBLIC_THEME["bg"])
        self.player_window.protocol("WM_DELETE_WINDOW", self.player_window.withdraw)

        self.player_header = tk.Frame(self.player_window, bg=PUBLIC_THEME["bg"], padx=28, pady=18)
        self.player_header.pack(fill="x")
        self.player_title = tk.Label(
            self.player_header,
            text="Beerpong Turnier",
            fg=PUBLIC_THEME["text"],
            bg=PUBLIC_THEME["bg"],
            font=("Segoe UI", 30, "bold"),
        )
        self.player_title.pack(side="left")
        self.player_status = tk.Label(
            self.player_header,
            text="",
            fg=PUBLIC_THEME["accent_dark"],
            bg=PUBLIC_THEME["bg"],
            font=("Segoe UI", 16, "bold"),
        )
        self.player_status.pack(side="left", padx=(18, 0))
        tk.Button(
            self.player_header,
            text="Vollbild",
            command=self.toggle_player_fullscreen,
            bg=PUBLIC_THEME["surface"],
            fg=PUBLIC_THEME["text"],
            activebackground=PUBLIC_THEME["panel"],
            activeforeground=PUBLIC_THEME["text"],
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right")

        self.player_body = tk.Frame(self.player_window, bg=PUBLIC_THEME["bg"], padx=28, pady=8)
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
        self._refresh_action_states()
        self._refresh_backup_info()
        self._refresh_player_view(force=force)


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
                tag = "active"
            elif row["status"] == "finished":
                tag = "finished"
            elif row["note"] == "bereit":
                tag = "ready"
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
            self.preview_tree.insert("", "end", values=(idx, row["team_a"], row["team_b"], row["status"]), tags=("ready",))

    def _refresh_action_states(self) -> None:
        has_swiss_matches = any(match.phase == "SWISS" for match in self.engine.state.matches.values())
        has_finished_swiss = any(match.phase == "SWISS" and match.status == "finished" for match in self.engine.state.matches.values())
        in_swiss = self.engine.state.phase == "SWISS"
        for button, enabled in [
            (self.undo_button, has_finished_swiss),
            (self.edit_button, has_finished_swiss),
            (self.reset_match_button, has_swiss_matches),
            (self.fill_button, in_swiss),
            (self.save_now_button, self.engine.state.phase != "SETUP"),
            (self.recompute_button, in_swiss),
        ]:
            if button is not None:
                self._set_button_enabled(button, enabled)
        can_start_ko = (
            self.engine.state.phase == "SWISS"
            and self.engine.swiss_complete()
            and not self.engine.state.active_tables
        )
        if self.ko_button is not None:
            self._set_button_enabled(self.ko_button, can_start_ko)

    def _refresh_prepared_wave_tree(self) -> None:
        for item in self.prepared_wave_tree.get_children():
            self.prepared_wave_tree.delete(item)
        for row in self.engine.prepared_wave_rows():
            tag = ""
            if row["status"] == "active":
                tag = "active"
            elif row["note"] == "bereit":
                tag = "ready"
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

    def _player_render_hash(self) -> str:
        payload = {
            "phase": self.engine.state.phase,
            "phase_label": self.engine.phase_label(),
            "progress": self.engine.progress_text(),
            "active": self.engine.active_matches_rows(),
            "preview": self.engine.preview_matches(),
            "ranking": self.engine.ranking_rows(),
            "knockout": self.engine.knockout_rows() if self.engine.state.phase in {"KO", "FINISHED"} else [],
            "podium": self.engine.state.podium,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _refresh_player_view(self, force: bool = False) -> None:
        if self.player_window is None or self.player_body is None:
            return

        self.player_title.config(text="Beerpong Turnier")
        self.player_status.config(text=self.engine.phase_label())

        render_hash = self._player_render_hash()
        if not force and render_hash == self._last_player_render_hash:
            return
        self._last_player_render_hash = render_hash

        for child in self.player_body.winfo_children():
            child.destroy()

        if self.engine.state.phase == "KO" or self.engine.state.phase == "FINISHED":
            self._render_player_knockout_view()
        else:
            self._render_player_swiss_view()

    def _public_card(self, parent: tk.Widget, title: str, subtitle: str = "", hero: bool = False) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(parent, bg=PUBLIC_THEME["shadow"], highlightthickness=0)
        card_bg = PUBLIC_THEME["highlight_soft"] if hero else PUBLIC_THEME["card"]
        body = tk.Frame(
            outer,
            bg=card_bg,
            padx=22 if hero else 18,
            pady=18 if hero else 14,
            highlightthickness=1,
            highlightbackground=PUBLIC_THEME["line_soft"],
        )
        body.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))
        tk.Label(
            body,
            text=title,
            fg=PUBLIC_THEME["accent_dark"] if hero else PUBLIC_THEME["text"],
            bg=card_bg,
            font=("Segoe UI", 16 if hero else 14, "bold"),
            anchor="w",
        ).pack(fill="x", anchor="w")
        if subtitle:
            tk.Label(
                body,
                text=subtitle,
                fg=PUBLIC_THEME["muted"],
                bg=card_bg,
                font=("Segoe UI", 10),
                anchor="w",
            ).pack(fill="x", anchor="w", pady=(2, 0))
        tk.Frame(body, bg=card_bg, height=10 if hero else 8).pack(fill="x")
        return outer, body

    def _render_player_ranking_row(self, parent: tk.Widget, row: dict, grid_row: int, grid_column: int, columns: int) -> None:
        is_top = row["rank"] <= TOP_CUT
        is_cut_line = row["rank"] == TOP_CUT
        bg = PUBLIC_THEME["top8_strong"] if is_cut_line else PUBLIC_THEME["top8"] if is_top else PUBLIC_THEME["row_alt"] if row["rank"] % 2 == 0 else PUBLIC_THEME["row"]
        padx = (0, 8) if grid_column == 0 and columns > 1 else (8, 0) if columns > 1 else (0, 0)
        frame = tk.Frame(parent, bg=bg, padx=0, pady=0)
        frame.grid(row=grid_row, column=grid_column, sticky="ew", padx=padx, pady=3)
        frame.grid_columnconfigure(2, weight=1)

        strip_color = PUBLIC_THEME["top8_strip"] if is_top else bg
        tk.Frame(frame, bg=strip_color, width=5).grid(row=0, column=0, sticky="ns")
        tk.Label(
            frame,
            text=f"#{row['rank']}",
            bg=bg,
            fg=PUBLIC_THEME["accent_dark"] if is_top else PUBLIC_THEME["text_soft"],
            font=("Segoe UI", 15, "bold"),
            width=4,
            anchor="center",
        ).grid(row=0, column=1, sticky="ns", padx=(10, 6), pady=8)
        tk.Label(
            frame,
            text=self._shorten(row["name"], 30),
            bg=bg,
            fg=PUBLIC_THEME["text"],
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).grid(row=0, column=2, sticky="ew", pady=8)

        if is_top:
            tk.Label(
                frame,
                text="Top 8",
                bg=PUBLIC_THEME["top8_badge"],
                fg=PUBLIC_THEME["text_soft"],
                font=("Segoe UI", 9, "bold"),
                padx=8,
                pady=3,
            ).grid(row=0, column=3, padx=(6, 8), pady=8)

        stats = tk.Frame(frame, bg=bg)
        stats.grid(row=0, column=4, sticky="e", padx=(0, 12), pady=8)
        for text in [
            f"{row['points']} Pkt",
            f"{row['cups']} Cups",
            f"{row['games']}/{SWISS_GAMES_PER_TEAM} Spiele",
        ]:
            tk.Label(
                stats,
                text=text,
                bg=bg,
                fg=PUBLIC_THEME["text_soft"],
                font=("Segoe UI", 11, "bold"),
                anchor="e",
            ).pack(side="left", padx=(12, 0))

    def _render_player_swiss_view(self) -> None:
        content = self.player_body
        if content is None:
            return
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=0)
        content.grid_rowconfigure(1, weight=0)
        content.grid_rowconfigure(2, weight=1)

        hero_shell, hero_card = self._public_card(content, "Next Match", self.engine.progress_text(), hero=True)
        hero_shell.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        preview = self.engine.preview_matches()
        if preview:
            first = preview[0]
            tk.Label(
                hero_card,
                text=f"{self._shorten(first['team_a'], 36)}  vs  {self._shorten(first['team_b'], 36)}",
                bg=PUBLIC_THEME["highlight_soft"],
                fg=PUBLIC_THEME["text"],
                font=("Segoe UI", 34, "bold"),
                anchor="w",
            ).pack(fill="x", anchor="w")
            tk.Label(
                hero_card,
                text=first.get("status", "Wir bitten sie, sich Spielbereit zu halten. Die Firma Dankt für Ihr Verständnis."),
                bg=PUBLIC_THEME["highlight_soft"],
                fg=PUBLIC_THEME["accent_dark"],
                font=("Segoe UI", 16, "bold"),
                anchor="w",
            ).pack(fill="x", anchor="w", pady=(2, 0))
            if len(preview) > 1:
                second = preview[1]
                tk.Label(
                    hero_card,
                    text=f"Danach: {self._shorten(second['team_a'], 28)} vs {self._shorten(second['team_b'], 28)}",
                    bg=PUBLIC_THEME["highlight_soft"],
                    fg=PUBLIC_THEME["muted"],
                    font=("Segoe UI", 12, "bold"),
                    anchor="w",
                ).pack(fill="x", anchor="w", pady=(10, 0))
        else:
            active = self.engine.active_matches_rows()
            title = "Alle Tische laufen" if active else "Noch kein Match bereit"
            detail = "Neue Paarungen erscheinen, sobald ein Tisch frei wird." if active else "Sobald das Turnier gestartet ist, erscheint hier das nächste Match."
            tk.Label(hero_card, text=title, bg=PUBLIC_THEME["highlight_soft"], fg=PUBLIC_THEME["text"], font=("Segoe UI", 30, "bold"), anchor="w").pack(fill="x", anchor="w")
            tk.Label(hero_card, text=detail, bg=PUBLIC_THEME["highlight_soft"], fg=PUBLIC_THEME["muted"], font=("Segoe UI", 14), anchor="w").pack(fill="x", anchor="w", pady=(4, 0))

        live_shell, live_card = self._public_card(content, "Live an den Tischen")
        live_shell.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        active = self.engine.active_matches_rows()
        if not active:
            tk.Label(live_card, text="Keine laufenden Spiele.", bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["muted"], font=("Segoe UI", 14)).pack(anchor="w")
        else:
            live_grid = tk.Frame(live_card, bg=PUBLIC_THEME["card"])
            live_grid.pack(fill="x")
            for idx, row in enumerate(active):
                live_grid.grid_columnconfigure(idx, weight=1)
                match_card = tk.Frame(live_grid, bg=PUBLIC_THEME["surface_alt"], padx=12, pady=10)
                match_card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 8, 0))
                tk.Label(match_card, text=f"T{row['table']}", bg=PUBLIC_THEME["surface_alt"], fg=PUBLIC_THEME["accent_dark"], font=("Segoe UI", 12, "bold")).pack(anchor="w")
                tk.Label(
                    match_card,
                    text=f"{self._shorten(row['team_a'], 24)} vs {self._shorten(row['team_b'], 24)}",
                    bg=PUBLIC_THEME["surface_alt"],
                    fg=PUBLIC_THEME["text"],
                    font=("Segoe UI", 13, "bold"),
                    anchor="w",
                ).pack(fill="x", anchor="w")

        ranking_shell, ranking_card = self._public_card(content, "Live Ranking", self.top_hint_var.get())
        ranking_shell.grid(row=2, column=0, sticky="nsew")
        ranking_rows = self.engine.ranking_rows()
        if not ranking_rows:
            tk.Label(ranking_card, text="Ranking erscheint nach Turnierstart.", bg=PUBLIC_THEME["card"], fg=PUBLIC_THEME["muted"], font=("Segoe UI", 15)).pack(anchor="w")
            return

        ranking_grid = tk.Frame(ranking_card, bg=PUBLIC_THEME["card"])
        ranking_grid.pack(fill="both", expand=True)
        columns = 2 if len(ranking_rows) > 12 else 1
        split = (len(ranking_rows) + columns - 1) // columns
        for col in range(columns):
            ranking_grid.grid_columnconfigure(col, weight=1, uniform="ranking")
        for idx, row in enumerate(ranking_rows):
            col = idx // split
            local_row = idx % split
            self._render_player_ranking_row(ranking_grid, row, local_row, col, columns)

    def _render_player_knockout_view(self) -> None:
        content = self.player_body
        if content is None:
            return
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=0)
        content.grid_rowconfigure(2, weight=0)

        bracket_shell, bracket_card = self._public_card(content, "KO-Turnierbaum", "KO-Anzeige ab Swiss-Ende")
        bracket_shell.grid(row=0, column=0, sticky="nsew", pady=(0, 14))

        canvas = tk.Canvas(bracket_card, bg=PUBLIC_THEME["card"], highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Configure>", lambda _event, c=canvas: self._draw_bracket_canvas(c))
        self._draw_bracket_canvas(canvas)

        podium_shell, podium_card = self._public_card(content, "Podest")
        podium_shell.grid(row=1, column=0, sticky="ew")
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
        width = max(canvas.winfo_width(), 1100)
        height = max(canvas.winfo_height(), 620)
        if width <= 1 or height <= 1:
            width, height = 1100, 620

        if not rows:
            canvas.create_text(
                70,
                70,
                anchor="nw",
                text="KO-Phase wird erst nach Abschluss der Swiss-Wellen angezeigt.",
                fill=PUBLIC_THEME["muted"],
                font=("Segoe UI", 15),
            )
            return

        box_h = 112
        qf_w = max(270, min(330, int(width * 0.24)))
        sf_w = max(300, min(360, int(width * 0.26)))
        final_w = max(320, min(390, int(width * 0.28)))
        qf_x = max(42, int(width * 0.055))
        sf_x = max(qf_x + qf_w + 58, int(width * 0.39))
        final_x = max(sf_x + sf_w + 58, int(width * 0.70))
        final_w = min(final_w, max(280, width - final_x - 45))

        qf_gap = max(22, min(40, int((height - 120 - (box_h * 4)) / 3)))
        qf_y = [62 + idx * (box_h + qf_gap) for idx in range(4)]
        sf_y = [
            int(((qf_y[0] + box_h / 2) + (qf_y[1] + box_h / 2)) / 2 - box_h / 2),
            int(((qf_y[2] + box_h / 2) + (qf_y[3] + box_h / 2)) / 2 - box_h / 2),
        ]
        sf_center = int(((sf_y[0] + box_h / 2) + (sf_y[1] + box_h / 2)) / 2)
        final_y = max(92, sf_center - box_h - 28)
        third_y = final_y + box_h + 54

        positions = {
            "QF": [(qf_x, y) for y in qf_y],
            "SF": [(sf_x, y) for y in sf_y],
            "FINAL": [(final_x, final_y)],
            "3RD": [(final_x, third_y)],
        }

        def draw_box(x: int, y: int, w: int, h: int, title: str, team_a: str, team_b: str, status: str, winner: str) -> None:
            fill = PUBLIC_THEME["highlight_soft"] if status == "active" else PUBLIC_THEME["ok_soft"] if status == "finished" else PUBLIC_THEME["card"]
            outline = PUBLIC_THEME["accent"] if status == "active" else PUBLIC_THEME["ok"] if status == "finished" else PUBLIC_THEME["line_soft"]
            title_color = PUBLIC_THEME["accent_dark"] if status == "active" else PUBLIC_THEME["ok"] if status == "finished" else PUBLIC_THEME["text_soft"]
            text_color = PUBLIC_THEME["text"]
            canvas.create_rectangle(x + 4, y + 5, x + w + 4, y + h + 5, fill=PUBLIC_THEME["shadow"], outline=PUBLIC_THEME["shadow"])
            canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=1)
            if status in {"active", "finished"}:
                canvas.create_rectangle(x, y, x + 6, y + h, fill=outline, outline=outline)
            canvas.create_text(x + 14, y + 12, anchor="nw", text=title, fill=title_color, font=("Segoe UI", 15, "bold"), width=w - 28)
            canvas.create_text(x + 14, y + 44, anchor="nw", text=team_a, fill=text_color, font=("Segoe UI", 13, "bold"), width=w - 28)
            canvas.create_text(x + 14, y + 69, anchor="nw", text=team_b, fill=text_color, font=("Segoe UI", 13, "bold"), width=w - 28)
            if status == "finished":
                canvas.create_text(x + 14, y + h - 24, anchor="nw", text=f"Sieger: {winner}", fill=PUBLIC_THEME["ok"], font=("Segoe UI", 11, "bold"), width=w - 28)
            elif status == "active":
                canvas.create_text(x + 14, y + h - 24, anchor="nw", text="läuft", fill=PUBLIC_THEME["accent_dark"], font=("Segoe UI", 11, "bold"))
            else:
                canvas.create_text(x + 14, y + h - 24, anchor="nw", text="wartet", fill=PUBLIC_THEME["muted"], font=("Segoe UI", 11, "bold"))

        def row_for(stage: str, label: str):
            for row in rows:
                if row["stage"] == stage and row["label"] == label:
                    return row
            return None

        for idx, (x, y) in enumerate(positions["QF"], start=1):
            label = f"Viertelfinale {idx}"
            row = row_for("QF", label)
            if row:
                draw_box(x, y, qf_w, box_h, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        for idx, (x, y) in enumerate(positions["SF"], start=1):
            label = f"Halbfinale {idx}"
            row = row_for("SF", label)
            if row:
                draw_box(x, y, sf_w, box_h, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        for stage, label, (x, y) in [("FINAL", "Finale", positions["FINAL"][0]), ("3RD", "Spiel um Platz 3", positions["3RD"][0])]:
            row = row_for(stage, label)
            if row:
                draw_box(x, y, final_w, box_h, label, row["team_a"], row["team_b"], row["status"], row["winner"])

        # Connectors.
        def connector(from_x: int, from_y: int, from_w: int, to_x: int, to_y: int) -> None:
            x1 = from_x + from_w
            y1 = from_y + box_h // 2
            x2 = to_x
            y2 = to_y + box_h // 2
            mid_x = int((x1 + x2) / 2)
            canvas.create_line(x1, y1, mid_x, y1, mid_x, y2, x2, y2, fill=PUBLIC_THEME["line"], width=2, tags=("connector",))

        connector(qf_x, qf_y[0], qf_w, sf_x, sf_y[0])
        connector(qf_x, qf_y[1], qf_w, sf_x, sf_y[0])
        connector(qf_x, qf_y[2], qf_w, sf_x, sf_y[1])
        connector(qf_x, qf_y[3], qf_w, sf_x, sf_y[1])
        connector(sf_x, sf_y[0], sf_w, final_x, final_y)
        connector(sf_x, sf_y[1], sf_w, final_x, final_y)
        connector(sf_x, sf_y[0], sf_w, final_x, third_y)
        connector(sf_x, sf_y[1], sf_w, final_x, third_y)
        canvas.tag_lower("connector")

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

    def save_now_from_gui(self) -> None:
        self.persist_state(label="manual_save", snapshot=True)
        self.status_var.set("Manuell gespeichert.")

    def save_snapshot(self) -> None:
        self.persist_state(label=f"phase_{self.engine.state.phase.lower()}", snapshot=True)
        self.status_var.set("Snapshot erstellt.")

    def rebuild_preview(self) -> None:
        # The preview is derived from the current state and is only redrawn on demand.
        self.refresh_all(force=True)
        self.status_var.set("Vorschau aktualisiert.")

    def recompute_pairing_from_gui(self) -> None:
        try:
            changed = self.engine.recompute_pairing()
            self.persist_state(label="recompute_pairing", snapshot=True)
            self.status_var.set("Nächste Welle neu berechnet." if changed else "Nächste Welle geprüft, keine Änderung nötig.")
            self.refresh_all(force=True)
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

    def undo_last_result_from_gui(self) -> None:
        try:
            match_id = self.engine.undo_last_result()
            self.persist_state(label="undo_result", snapshot=True)
            self.status_var.set(f"Letzte Eingabe zurückgenommen ({match_id}).")
            self.refresh_all(force=True)
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

    def _match_choice_rows(self, finished_only: bool = False) -> dict[str, str]:
        choices: dict[str, str] = {}
        for row in self.engine.editable_match_rows():
            if finished_only and row["status"] != "finished":
                continue
            label = (
                f"{row['slot']} | {row['team_a']} vs {row['team_b']} | "
                f"{row['status']} | Sieger: {row['winner']} | OT: {row['ot']} | Cups: {row['cups']}"
            )
            choices[label] = row["match_id"]
        return choices

    def open_edit_match_dialog(self) -> None:
        choices = self._match_choice_rows(finished_only=True)
        if not choices:
            messagebox.showinfo("Match bearbeiten", "Es gibt noch kein gespeichertes Swiss-Ergebnis.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Match bearbeiten")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=PUBLIC_THEME["bg"])
        dialog.geometry("560x260")

        match_var = tk.StringVar(value=next(iter(choices)))
        winner_var = tk.StringVar()
        cups_var = tk.StringVar(value="0")
        ot_var = tk.BooleanVar(value=False)

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Match").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        match_combo = ttk.Combobox(body, textvariable=match_var, values=list(choices.keys()), state="readonly")
        match_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(body, text="Sieger").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        winner_combo = ttk.Combobox(body, textvariable=winner_var, state="readonly")
        winner_combo.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(body, text="OT", variable=ot_var, command=lambda: cups_var.set("10" if ot_var.get() else "0")).grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Spinbox(body, textvariable=cups_var, from_=0, to=12, width=10).grid(row=2, column=1, sticky="w", pady=(0, 8))

        def sync_match_fields(_event: object = None) -> None:
            match = self.engine.state.matches[choices[match_var.get()]]
            winner_combo["values"] = [self.engine.team_name(match.team_a), self.engine.team_name(match.team_b)]
            winner_var.set(self.engine.team_name(match.winner) if match.winner else self.engine.team_name(match.team_a))
            ot_var.set(match.is_overtime)
            cups_var.set(str(match.loser_cups_hit if match.loser_cups_hit is not None else (10 if match.is_overtime else 0)))

        def save_edit() -> None:
            match = self.engine.state.matches[choices[match_var.get()]]
            winner_id = match.team_a if self.engine.team_name(match.team_a) == winner_var.get() else match.team_b
            try:
                self.engine.edit_match_result(match.match_id, winner_id, ot_var.get(), int(cups_var.get()))
                self.persist_state(label="edit_match", snapshot=True)
                self.status_var.set("Match bearbeitet.")
                self.refresh_all(force=True)
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("Fehler", str(exc), parent=dialog)

        match_combo.bind("<<ComboboxSelected>>", sync_match_fields)
        sync_match_fields()
        ttk.Button(body, text="Speichern", command=save_edit, style="Action.TButton").grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    def open_reset_match_dialog(self) -> None:
        choices = self._match_choice_rows(finished_only=False)
        if not choices:
            messagebox.showinfo("Match zurücksetzen", "Es gibt noch kein Swiss-Match.")
            return
        label = tk.StringVar(value=next(iter(choices)))
        dialog = tk.Toplevel(self.root)
        dialog.title("Match zurücksetzen")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=PUBLIC_THEME["bg"])
        dialog.geometry("560x170")

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Match auswählen").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(body, textvariable=label, values=list(choices.keys()), state="readonly").grid(row=1, column=0, sticky="ew", pady=(0, 12))

        def reset_selected() -> None:
            if not messagebox.askyesno("Match zurücksetzen", "Dieses Match wirklich auf offen setzen?", parent=dialog):
                return
            try:
                self.engine.reset_match(choices[label.get()])
                self.persist_state(label="reset_match", snapshot=True)
                self.status_var.set("Match zurückgesetzt.")
                self.refresh_all(force=True)
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("Fehler", str(exc), parent=dialog)

        ttk.Button(body, text="Zurücksetzen", command=reset_selected, style="Action.TButton").grid(row=2, column=0, sticky="ew")

    def start_knockout_from_gui(self) -> None:
        if self.engine.state.phase != "SWISS":
            messagebox.showinfo("KO starten", "Die KO-Phase ist aktuell nicht startbereit.")
            return
        if self.engine.state.active_tables:
            messagebox.showinfo("KO starten", "Bitte erst alle laufenden Swiss-Spiele beenden.")
            return
        if not self.engine.swiss_complete():
            messagebox.showinfo("KO starten", "KO kann erst nach allen Swiss-Spielen gestartet werden.")
            return
        try:
            self.engine.start_knockout()
            self.persist_state(label="start_ko", snapshot=True)
            self.status_var.set("KO-Phase gestartet.")
            self.refresh_all(force=True)
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc))

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
