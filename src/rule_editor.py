"""The rule editor window (tkinter): pick a window, tick the conditions to use, choose or create
the project and its color, and see which open windows the rule would match before saving."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

import rule_helpers as rh
import theme
import widgets
from rules import WinInfo

NEW_PROJECT = "+ New project..."
NO_WINDOW = "(none - fill in by hand)"
KEYS = ("process", "title", "ui", "cwd")


def window_label(w: WinInfo) -> str:
    title = w.title if len(w.title) <= 55 else w.title[:54] + "..."
    return f"{rh.process_stem(w.exe) or '?'}  -  {title}"


class RuleEditor(tk.Toplevel):
    def __init__(self, parent, cfg, engine, window: WinInfo | None = None, rule: dict | None = None,
                 rule_index: int | None = None, on_saved=None):
        super().__init__(parent)
        self.cfg, self.engine, self.on_saved, self.rule_index = cfg, engine, on_saved, rule_index
        self._window: WinInfo | None = None
        self._ui_text = ""
        self._preview_job = None
        self._ui_retries = 0
        self._closed = False
        self.title("Edit rule" if rule else "New rule")
        self.resizable(False, False)
        if parent.winfo_viewable():
            self.transient(parent)

        pal = theme.palette
        self.configure(bg=pal["bg"])
        frm = ttk.Frame(self, padding=(24, 20, 24, 20))
        frm.grid(sticky="nsew")
        frm.columnconfigure(0, weight=1)
        self.frm = frm

        ttk.Label(frm, text="Edit rule" if rule else "New rule", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(frm, style="Muted.TLabel", text="Windows that match all ticked conditions get the project's color."
                  ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        # -- start from an open window --------------------------------------
        ttk.Label(frm, text="Start from an open window", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        self.win_var = tk.StringVar(value=NO_WINDOW)
        self.win_box = ttk.Combobox(frm, textvariable=self.win_var, state="readonly", width=70)
        self.win_box.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.win_box.bind("<<ComboboxSelected>>", self._on_pick_window)
        self._labels: dict = {}
        self._fill_window_list(window)

        # -- project and color -----------------------------------------------
        ttk.Label(frm, text="Project", style="Section.TLabel").grid(row=4, column=0, sticky="w", pady=(18, 0))
        pf = ttk.Frame(frm)
        pf.grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.project_var = tk.StringVar()
        self.project_box = ttk.Combobox(pf, textvariable=self.project_var, state="readonly", width=30)
        self.project_box.pack(side="left")
        self.project_box.bind("<<ComboboxSelected>>", self._on_project_changed)
        self.color_var = tk.StringVar(value="#888888")
        self.swatch = tk.Label(pf, width=3, bg="#888888")
        self.swatch.pack(side="left", padx=(12, 8), ipady=4)
        self.color_btn = ttk.Button(pf, text="Color...", command=self._pick_color)
        self.color_btn.pack(side="left")
        self.new_name_var = tk.StringVar()
        self.new_row = ttk.Frame(frm)
        ttk.Label(self.new_row, text="New project name").pack(side="left")
        self.new_entry = ttk.Entry(self.new_row, textvariable=self.new_name_var, width=32)
        self.new_entry.pack(side="left", padx=8)
        self.new_row.grid(row=6, column=0, sticky="w", pady=(8, 0))
        self.new_row.grid_remove()

        # -- conditions ----------------------------------------------------------
        self.use = {k: tk.BooleanVar(value=False) for k in KEYS}
        self.val = {k: tk.StringVar() for k in KEYS}
        ttk.Label(frm, text="Conditions", style="Section.TLabel").grid(row=7, column=0, sticky="w", pady=(18, 0))
        card = widgets.tile(frm, (14, 6))
        card.outer.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        card.columnconfigure(1, weight=1)
        spec = [
            ("process", "Program", False, "Program name, such as claude, chrome or WindowsTerminal. * works as a wildcard."),
            ("title", "Title contains", False, "A regular expression, matched anywhere in the window title."),
            ("ui", "Text inside the app", True, ""),
            ("cwd", "Working folder under", True,
             "Matches when a shell in this window (or a program started from it) is in this folder."),
        ]
        self.widgets: dict = {}
        self.hints: dict = {}
        r = 0
        for key, label, combo, hint in spec:
            ttk.Checkbutton(card, text=label, variable=self.use[key], command=self._changed,
                            style="Tile.Switch.TCheckbutton").grid(row=r, column=0, sticky="w", pady=(8, 0), padx=(0, 16))
            if combo:
                w = ttk.Combobox(card, textvariable=self.val[key], width=52)
                w.bind("<<ComboboxSelected>>", lambda e, k=key: self._on_combo_choice(k))
            else:
                w = ttk.Entry(card, textvariable=self.val[key], width=54)
            w.grid(row=r, column=1, sticky="ew", pady=(8, 0))
            self.widgets[key] = w
            h = widgets.tlabel(card, text=hint, style="TileMuted.TLabel", wraplength=470, justify="left")
            h.grid(row=r + 1, column=1, sticky="w", pady=(2, 4))
            self.hints[key] = h
            self.val[key].trace_add("write", lambda *a, k=key: self._on_text_edit(k))
            r += 2
        self._set_ui_hint()

        # -- live preview -----------------------------------------------------------
        self.preview_var = tk.StringVar()
        self.preview_label = ttk.Label(frm, textvariable=self.preview_var, style="Section.TLabel")
        self.preview_label.grid(row=9, column=0, sticky="w", pady=(18, 0))
        self.preview_list = tk.Listbox(frm, height=5, activestyle="none", exportselection=False, bd=0,
                                       highlightthickness=1, highlightbackground=pal["border"],
                                       highlightcolor=pal["border"], bg=pal["card"], fg=pal["text"],
                                       selectbackground=pal["hover"], selectforeground=pal["text"],
                                       font=(theme.FONT, 9))
        self.preview_list.grid(row=10, column=0, sticky="ew", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, sticky="e", pady=(18, 0))
        ttk.Button(btns, text="Save rule", style="Accent.TButton", command=self._save).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Cancel", command=self._close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

        self._load_projects(rule["project"] if rule else None)
        if rule:
            self._load_rule(rule)
        elif window is not None:
            self._select_window(window)
        self._changed()

        theme.style_window(self)
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ------------------------------------------------------------------ loading
    def _fill_window_list(self, extra: WinInfo | None) -> None:
        wins = [w for w, _p, _s in self.engine.windows()]
        if extra is not None and all(w.hwnd != extra.hwnd for w in wins):
            wins.append(extra)
        wins.sort(key=lambda w: (rh.process_stem(w.exe).lower(), w.title.lower()))
        self._labels = {}
        for w in wins:
            label = window_label(w)
            while label in self._labels:  # keep labels unique
                label += " "
            self._labels[label] = w
        self.win_box["values"] = [NO_WINDOW] + list(self._labels)

    def _load_projects(self, selected: str | None) -> None:
        names = self.cfg.project_names()
        self.project_box["values"] = names + [NEW_PROJECT]
        if selected in names:
            self.project_var.set(selected)
        elif names:
            self.project_var.set(names[0])
        else:
            self.project_var.set(NEW_PROJECT)
        self._on_project_changed()

    def _load_rule(self, rule: dict) -> None:
        for key in KEYS:
            self.val[key].set(rule.get(key, ""))
            self.use[key].set(bool(rule.get(key, "").strip()))
        if rule.get("ui"):
            self.widgets["ui"]["values"] = [rule["ui"]]

    def _select_window(self, w: WinInfo) -> None:
        label = next((lbl for lbl, x in self._labels.items() if x.hwnd == w.hwnd), None)
        if label:
            self.win_var.set(label)
        self._load_window(w)

    def _on_pick_window(self, _event=None) -> None:
        w = self._labels.get(self.win_var.get())
        if w is not None:
            self._load_window(w)
        else:
            self._window = None
            self._changed()

    def _load_window(self, w: WinInfo) -> None:
        self._window = w
        try:
            cwds = self.engine.procs.cwds(w.pid)
        except Exception:
            cwds = []
        live = self.engine.find_window(w.hwnd) or w
        self._ui_text = live.ui or w.ui
        d = rh.defaults_for_window(WinInfo(w.hwnd, w.pid, w.title, w.exe, w.cls, ui=self._ui_text),
                                   cwds, os.path.expanduser("~"))
        for key in KEYS:
            self.val[key].set(d[key])
            self.use[key].set(d["use"][key])
        self.widgets["ui"]["values"] = d["ui_options"]
        self.widgets["cwd"]["values"] = d["cwd_options"]
        # Reading text out of the app takes a moment; keep looking for it while the editor is open.
        self.engine.preview_ui_process = d["process"]
        self._ui_retries = 0
        if not self._ui_text:
            self.after(900, self._look_for_ui_text)
        self._set_ui_hint()
        self._changed()

    def _look_for_ui_text(self) -> None:
        if self._closed or self._window is None or self._ui_text or self._ui_retries >= 4:
            return
        self._ui_retries += 1
        live = self.engine.find_window(self._window.hwnd)
        if live is not None and live.ui:
            self._ui_text = live.ui
            options = rh.suggest_ui_patterns(live.ui)
            self.widgets["ui"]["values"] = options
            if not self.val["ui"].get().strip():
                self.val["ui"].set(options[0])
                self.use["ui"].set(True)
            self._set_ui_hint()
            self._changed()
        else:
            self.after(900, self._look_for_ui_text)

    # ------------------------------------------------------------------ project / color
    def _on_project_changed(self, _event=None) -> None:
        name = self.project_var.get()
        if name == NEW_PROJECT:
            self.new_row.grid()
            self._set_color(rh.next_color(p["color"] for p in self.cfg.projects))
            self.new_entry.focus_set()
        else:
            self.new_row.grid_remove()
            self._set_color(self.cfg.color_of(name) or "#888888")

    def _set_color(self, color: str) -> None:
        self.color_var.set(color)
        self.swatch.configure(bg=color)

    def _pick_color(self) -> None:
        _rgb, color = colorchooser.askcolor(color=self.color_var.get(), title="Project color", parent=self)
        if color:
            self._set_color(color.lower())

    # ------------------------------------------------------------------ live preview
    def _on_combo_choice(self, key: str) -> None:
        self.use[key].set(True)
        if key == "ui":
            self._set_ui_hint()
        self._changed()

    def _on_text_edit(self, key: str) -> None:
        if self.val[key].get().strip():
            self.use[key].set(True)
        if key == "ui":
            self._set_ui_hint()
        self._changed()

    def _set_ui_hint(self) -> None:
        pattern = self.val["ui"].get().strip()
        if self._ui_text and pattern:
            text = f"This window reads: '{self._ui_text}'.  Your pattern matches {rh.describe_ui_pattern(pattern, self._ui_text)}."
        elif self._ui_text:
            text = f"This window reads: '{self._ui_text}'."
        else:
            text = ("Claude app: text is 'Project / Chat title', so ^Project / matches every chat in a project. "
                    "Needs Program to be set.")
        self.hints["ui"].configure(text=text)

    def _changed(self) -> None:
        if self._closed:
            return
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self._update_preview)

    def _candidate_rule(self) -> dict:
        project = self.project_var.get()
        if project == NEW_PROJECT:
            project = self.new_name_var.get().strip() or "(new)"
        return rh.build_rule(project, {k: v.get() for k, v in self.use.items()},
                             {k: v.get() for k, v in self.val.items()})

    def _update_preview(self) -> None:
        self._preview_job = None
        if self._closed:
            return
        rule = self._candidate_rule()
        self.preview_list.delete(0, "end")
        if rule["ui"] and rule["process"]:
            self.engine.preview_ui_process = rule["process"]   # lets the engine read in-app text for matching windows
        problem = rh.validate_rule({**rule, "project": rule["project"] or "x"})
        if problem and rh.has_condition(rule):
            self.preview_var.set(problem)
            return
        if not rh.has_condition(rule):
            self.preview_var.set("Tick at least one condition to see which windows it would match.")
            return
        windows = [w for w, _p, _s in self.engine.windows()]
        matches = rh.preview_matches(rule, windows, lambda w: self.engine.procs.cwds(w.pid))
        noun = "window" if len(matches) == 1 else "windows"
        note = ""
        if rule["ui"] and not any(w.ui for w in windows if w.exe):
            note = "  (still reading text from the app...)"
        self.preview_var.set(f"Matches {len(matches)} open {noun} right now.{note}")
        for w in matches[:50]:
            line = window_label(w)
            if w.ui:
                line += f"   [{w.ui}]"
            self.preview_list.insert("end", line)

    # ------------------------------------------------------------------ save / close
    def _save(self) -> None:
        name = self.project_var.get()
        creating = name == NEW_PROJECT
        if creating:
            name = self.new_name_var.get().strip()
            if not name:
                messagebox.showerror("Rule", "Give the new project a name.", parent=self)
                return
            if name in self.cfg.project_names():
                messagebox.showerror("Rule", f"A project named '{name}' already exists.", parent=self)
                return
        rule = rh.build_rule(name, {k: v.get() for k, v in self.use.items()},
                             {k: v.get() for k, v in self.val.items()})
        problem = rh.validate_rule(rule)
        if problem:
            messagebox.showerror("Rule", problem, parent=self)
            return
        color = self.color_var.get()
        with self.cfg.lock:
            if creating:
                self.cfg.projects.append({"name": name, "color": color})
            else:
                for p in self.cfg.projects:
                    if p["name"] == name and p["color"] != color:
                        p["color"] = color      # color changed from this dialog
            if self.rule_index is not None and 0 <= self.rule_index < len(self.cfg.rules):
                self.cfg.rules[self.rule_index] = rule
            else:
                self.cfg.rules.append(rule)
        self.cfg.save()
        self._close()
        self.engine.refresh_all()
        if self.on_saved:
            self.on_saved()

    def _close(self) -> None:
        self._closed = True
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except tk.TclError:
                pass
        self.engine.preview_ui_process = ""
        self.destroy()
