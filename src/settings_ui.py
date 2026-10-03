"""Settings window (tkinter + the Sun Valley theme): projects, rules, live windows, general options."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import hotkeys
import rule_helpers as rh
import theme
import widgets
import winapi
from config import MODIFIER_CHOICES
from rule_editor import RuleEditor
from widgets import HotkeyField, ScrollFrame

AUTO_LABEL = "Automatic (use rules)"
NONE_LABEL = "No color"


def text_color_for(bg: str) -> str:
    return theme.text_on(bg)


class SettingsWindow:
    def __init__(self, root, cfg, engine, on_change, on_hotkeys=None):
        self.cfg, self.engine, self.on_change = cfg, engine, on_change
        self.on_hotkeys = on_hotkeys
        self._warned: set = set()
        self.win = tk.Toplevel(root)
        self.win.title("WindowTint")
        self.win.geometry("940x720")
        self.win.minsize(800, 600)
        self.win.configure(bg=theme.palette["bg"])
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        head = ttk.Frame(self.win, padding=(24, 18, 24, 0))
        head.pack(fill="x")
        ttk.Label(head, text="WindowTint", style="Heading.TLabel").pack(side="left")
        self.v_on = tk.BooleanVar(value=not getattr(engine, "paused", False))
        ttk.Checkbutton(head, text="Colors on", style="Switch.TCheckbutton", variable=self.v_on,
                        command=self._toggle_paused).pack(side="right")

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self.tab_projects = ttk.Frame(nb, padding=(8, 14))
        self.tab_rules = ttk.Frame(nb, padding=(8, 14))
        self.tab_windows = ttk.Frame(nb, padding=(8, 14))
        self.tab_general = ttk.Frame(nb)
        nb.add(self.tab_projects, text="  Projects  ")
        nb.add(self.tab_rules, text="  Rules  ")
        nb.add(self.tab_windows, text="  Windows  ")
        nb.add(self.tab_general, text="  Settings  ")
        self._build_projects()
        self._build_rules()
        self._build_windows()
        self._build_general()
        self._poll_windows()
        theme.style_window(self.win)

    def lift(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _commit(self):
        self.cfg.save()
        self._refresh_projects()
        self._refresh_rules()
        self.on_change()

    def _toggle_paused(self):
        self.engine.set_paused(not self.v_on.get())
        self.on_change()

    @staticmethod
    def _card(parent) -> ttk.Frame:
        card = widgets.tile(parent)
        card.outer.pack(fill="x", pady=3, padx=(0, 8))
        return card

    # ------------------------------------------------------------------ projects
    def _build_projects(self):
        f = self.tab_projects
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 10))
        text = ttk.Frame(top)
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text="Projects", style="Section.TLabel").pack(anchor="w")
        self.proj_hint = ttk.Label(text, style="Muted.TLabel", wraplength=640, justify="left")
        self.proj_hint.pack(anchor="w", pady=(2, 0))
        ttk.Button(top, text="+  New project", style="Accent.TButton", command=self._proj_add).pack(side="right")
        self.proj_scroll = ScrollFrame(f)
        self.proj_scroll.pack(fill="both", expand=True)
        self._refresh_projects()

    def _refresh_projects(self):
        body = self.proj_scroll.body
        for child in body.winfo_children():
            child.destroy()
        with self.cfg.lock:
            projects = [dict(p) for p in self.cfg.projects]
            rules = list(self.cfg.rules)
        mods = hotkeys.display_combo(self.cfg.data["hotkey_modifiers"] + "+1").rsplit("+", 1)[0]
        self.proj_hint.configure(
            text=f"Each project is a color. Order sets the hotkeys: {mods}+1 gives the window you are in the first "
                 f"project, {mods}+2 the second, and so on. {mods}+0 hands it back to the rules.")
        if not projects:
            ttk.Label(body, text="No projects yet. Create one to start coloring windows.",
                      style="Muted.TLabel").pack(pady=40)
        for i, p in enumerate(projects):
            card = self._card(body)
            widgets.dot(card, p["color"], 26).pack(side="left")
            info = ttk.Frame(card, style="Tile.TFrame")
            info.pack(side="left", fill="x", expand=True, padx=14)
            widgets.tlabel(info, text=p["name"], style="TileTitle.TLabel").pack(anchor="w")
            n = sum(1 for r in rules if r["project"] == p["name"])
            sub = f"{n} rule{'s' if n != 1 else ''}"
            if i < 9:
                sub += f"   ·   {mods}+{i + 1}"
            widgets.tlabel(info, text=sub, style="TileMuted.TLabel").pack(anchor="w")
            name = p["name"]
            for text, cmd in (("Edit", lambda n=name: self._proj_edit(n)),
                              ("↑", lambda n=name: self._proj_move(n, -1)),
                              ("↓", lambda n=name: self._proj_move(n, 1)),
                              ("Delete", lambda n=name: self._proj_delete(n))):
                ttk.Button(card, text=text, command=cmd,
                           width=3 if len(text) == 1 else 7).pack(side="left", padx=2)

    def _proj_add(self):
        widgets.ProjectDialog(self.win, "New project", taken=self.cfg.project_names(), on_done=self._proj_created)

    def _proj_created(self, name, color):
        with self.cfg.lock:
            self.cfg.projects.append({"name": name, "color": color})
        self._commit()

    def _proj_edit(self, old):
        taken = [n for n in self.cfg.project_names() if n != old]

        def done(new, color):
            if new != old:
                self.cfg.rename_project(old, new)
                self.engine.rename_project(old, new)
            with self.cfg.lock:
                for p in self.cfg.projects:
                    if p["name"] == new:
                        p["color"] = color
            self._commit()
        widgets.ProjectDialog(self.win, "Edit project", old, self.cfg.color_of(old), taken, done)

    def _proj_move(self, name, delta):
        with self.cfg.lock:
            projects = self.cfg.projects
            i = next(k for k, p in enumerate(projects) if p["name"] == name)
            j = i + delta
            if 0 <= j < len(projects):
                projects[i], projects[j] = projects[j], projects[i]
        self._commit()

    def _proj_delete(self, name):
        if not messagebox.askyesno("Delete project", f"Delete '{name}' and the rules that point to it?", parent=self.win):
            return
        self.cfg.delete_project(name)
        self.engine.delete_project(name)
        self._commit()

    # --------------------------------------------------------------------- rules
    def _build_rules(self):
        f = self.tab_rules
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 10))
        text = ttk.Frame(top)
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text="Rules", style="Section.TLabel").pack(anchor="w")
        ttk.Label(text, style="Muted.TLabel", wraplength=640, justify="left",
                  text="A rule sends matching windows to a project. They are checked from the top and the first match "
                       "wins. A window you assigned by hand always overrides the rules.").pack(anchor="w", pady=(2, 0))
        ttk.Button(top, text="+  New rule", style="Accent.TButton", command=self._rule_add).pack(side="right")
        self.rule_scroll = ScrollFrame(f)
        self.rule_scroll.pack(fill="both", expand=True)
        self._refresh_rules()

    def _refresh_rules(self):
        body = self.rule_scroll.body
        for child in body.winfo_children():
            child.destroy()
        with self.cfg.lock:
            rules = [dict(r) for r in self.cfg.rules]
        if not rules:
            ttk.Label(body, style="Muted.TLabel", justify="center",
                      text="No rules yet.\nPress Ctrl+Alt+R in any window to make one from it, or use New rule."
                      ).pack(pady=40)
        for i, r in enumerate(rules):
            card = self._card(body)
            color = self.cfg.color_of(r["project"]) or "#888888"
            widgets.dot(card, color, 26).pack(side="left")
            info = ttk.Frame(card, style="Tile.TFrame")
            info.pack(side="left", fill="x", expand=True, padx=14)
            widgets.tlabel(info, text=r["project"], style="TileTitle.TLabel").pack(anchor="w")
            widgets.tlabel(info, text="When " + rh.describe_rule(r), style="TileMuted.TLabel", wraplength=520,
                      justify="left").pack(anchor="w")
            for text, cmd in (("Edit", lambda k=i: self._rule_edit(k)),
                              ("↑", lambda k=i: self._rule_move(k, -1)),
                              ("↓", lambda k=i: self._rule_move(k, 1)),
                              ("Delete", lambda k=i: self._rule_delete(k))):
                ttk.Button(card, text=text, command=cmd,
                           width=3 if len(text) == 1 else 7).pack(side="left", padx=2)

    def refresh_all(self):
        """Called after the rule editor saves (it may have added a project and a rule)."""
        if not self.alive():
            return
        self._refresh_projects()
        self._refresh_rules()
        self.on_change()

    def _rule_add(self):
        RuleEditor(self.win, self.cfg, self.engine, on_saved=self.refresh_all)

    def _rule_edit(self, i):
        with self.cfg.lock:
            if not 0 <= i < len(self.cfg.rules):
                return
            rule = dict(self.cfg.rules[i])
        RuleEditor(self.win, self.cfg, self.engine, rule=rule, rule_index=i, on_saved=self.refresh_all)

    def _rule_move(self, i, delta):
        j = i + delta
        with self.cfg.lock:
            if not 0 <= j < len(self.cfg.rules):
                return
            self.cfg.rules[i], self.cfg.rules[j] = self.cfg.rules[j], self.cfg.rules[i]
        self._commit()

    def _rule_delete(self, i):
        with self.cfg.lock:
            if 0 <= i < len(self.cfg.rules):
                del self.cfg.rules[i]
        self._commit()

    # ------------------------------------------------------------------- windows
    def _build_windows(self):
        f = self.tab_windows
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 10))
        text = ttk.Frame(top)
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text="Open windows", style="Section.TLabel").pack(anchor="w")
        ttk.Label(text, style="Muted.TLabel",
                  text="Select a window to give it a project by hand, or turn it into a rule.").pack(anchor="w", pady=(2, 0))
        self.filter_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.filter_var, width=24)
        search.pack(side="right")
        ttk.Label(top, text="Filter", style="Muted.TLabel").pack(side="right", padx=8)
        self.filter_var.trace_add("write", lambda *a: self._fill_windows())

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        self.win_tree = ttk.Treeview(wrap, columns=("title", "ui", "process", "project", "source"), show="headings",
                                     selectmode="browse")
        for col, label, w, stretch in (("title", "Window", 260, True), ("ui", "In-app text", 220, True),
                                       ("process", "Program", 110, False), ("project", "Project", 120, False),
                                       ("source", "Via", 70, False)):
            self.win_tree.heading(col, text=label, anchor="w")
            self.win_tree.column(col, width=w, anchor="w", stretch=stretch)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.win_tree.yview)
        self.win_tree.configure(yscrollcommand=bar.set)
        self.win_tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        self.win_tree.bind("<Double-1>", lambda _e: self._win_make_rule())

        foot = ttk.Frame(f)
        foot.pack(fill="x", pady=(12, 0))
        ttk.Label(foot, text="Give it to").pack(side="left")
        self.assign_var = tk.StringVar(value=AUTO_LABEL)
        self.assign_box = ttk.Combobox(foot, textvariable=self.assign_var, state="readonly", width=26)
        self.assign_box.pack(side="left", padx=8)
        ttk.Button(foot, text="Apply", command=self._win_apply).pack(side="left")
        ttk.Button(foot, text="Make a rule from this window...", style="Accent.TButton",
                   command=self._win_make_rule).pack(side="right")
        self._last_windows: list = []

    def _fill_windows(self):
        query = self.filter_var.get().lower().strip()
        sel = self.win_tree.selection()
        self.win_tree.delete(*self.win_tree.get_children())
        for w, proj, src in self._last_windows:
            row = (w.title if len(w.title) <= 70 else w.title[:69] + "...", w.ui, w.exe, proj or "", src or "")
            if query and query not in " ".join(row).lower():
                continue
            self.win_tree.insert("", "end", iid=str(w.hwnd), values=row)
        if sel and self.win_tree.exists(sel[0]):
            self.win_tree.selection_set(sel[0])

    def _poll_windows(self):
        if not self.alive():
            return
        self.assign_box["values"] = [AUTO_LABEL, NONE_LABEL] + self.cfg.project_names()
        self._last_windows = list(self.engine.windows())
        self._fill_windows()
        self.win.after(1500, self._poll_windows)

    def _win_apply(self):
        sel = self.win_tree.selection()
        if not sel:
            messagebox.showinfo("Windows", "Select a window first.", parent=self.win)
            return
        hwnd, choice = int(sel[0]), self.assign_var.get()
        if choice == AUTO_LABEL:
            self.engine.auto(hwnd)
        elif choice == NONE_LABEL:
            self.engine.force_none(hwnd)
        else:
            self.engine.assign(hwnd, choice)

    def _win_make_rule(self):
        sel = self.win_tree.selection()
        if not sel:
            messagebox.showinfo("Windows", "Select a window first.", parent=self.win)
            return
        w = self.engine.find_window(int(sel[0]))
        if w is None:
            messagebox.showinfo("Windows", "That window is no longer open.", parent=self.win)
            return
        RuleEditor(self.win, self.cfg, self.engine, window=w, on_saved=self.refresh_all)

    # ------------------------------------------------------------------- settings
    def _build_general(self):
        scroll = ScrollFrame(self.tab_general)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        f = scroll.body
        d = self.cfg.data
        self.v_border = tk.BooleanVar(value=d["color_border"])
        self.v_caption = tk.BooleanVar(value=d["color_caption"])
        self.v_thick = tk.IntVar(value=d.get("border_px", 1))
        self.v_sidebar = tk.BooleanVar(value=d["sidebar_overlay"])
        self.v_tabs = tk.BooleanVar(value=d["tab_overlay"])
        self.v_alttab = tk.BooleanVar(value=d.get("alt_tab", False))
        self.v_poll = tk.IntVar(value=d["poll_ms"])
        self.v_mods = tk.StringVar(value=d["hotkey_modifiers"])
        self.v_auto = tk.BooleanVar(value=winapi.autostart_enabled())

        def section(title, note=""):
            ttk.Label(f, text=title, style="Section.TLabel").pack(anchor="w", pady=(18, 0))
            if note:
                ttk.Label(f, text=note, style="Muted.TLabel", wraplength=720, justify="left").pack(anchor="w", pady=(2, 0))
            card = widgets.tile(f, (16, 8))
            card.outer.pack(fill="x", pady=(8, 0), padx=(0, 8))
            return card

        def row(card, label, control=None, note=""):
            r = ttk.Frame(card, style="Tile.TFrame")
            r.pack(fill="x", pady=6)
            left = ttk.Frame(r, style="Tile.TFrame")
            left.pack(side="left", fill="x", expand=True)
            widgets.tlabel(left, text=label, style="Tile.TLabel").pack(anchor="w")
            if note:
                widgets.tlabel(left, text=note, style="TileMuted.TLabel", wraplength=520, justify="left").pack(anchor="w")
            return r

        def switch(card, label, var, note=""):
            r = row(card, label, note=note)
            ttk.Checkbutton(r, style="Tile.Switch.TCheckbutton", variable=var, command=self._general_save,
                            text="").pack(side="right")

        card = section("Window colors")
        switch(card, "Color the window border", self.v_border)
        r = row(card, "Border thickness", note="1 is Windows' own thin border. Larger values draw a thicker frame "
                                               "just outside the window.")
        spin = ttk.Spinbox(r, from_=1, to=8, width=4, textvariable=self.v_thick, command=self._general_save)
        spin.pack(side="right")
        spin.bind("<Return>", lambda e: self._general_save())
        spin.bind("<FocusOut>", lambda e: self._general_save())
        switch(card, "Color the title bar", self.v_caption, "Also picks a readable title text color.")

        card = section("Inside apps")
        switch(card, "Mark chats in the Claude sidebar", self.v_sidebar, "Needs a rule with In-app text.")
        switch(card, "Mark browser tabs", self.v_tabs, "Chrome, Brave, Vivaldi, Edge and other Chromium browsers. "
                                                      "A tab is marked when its title matches a rule.")

        card = section("Window switcher", "A grid of live window previews, each in its project color.")
        r = row(card, "Open the switcher")
        self.hk_switcher = HotkeyField(r, d["hotkey_switcher"], "ctrl+alt+w", self._general_save)
        self.hk_switcher.pack(side="right")
        switch(card, "Use it for Alt+Tab", self.v_alttab,
               "Hold Alt, tap Tab, release to switch. Ctrl+Alt+Tab and Win+Tab still open Windows' own views, and "
               "turning this off gives Alt+Tab straight back to Windows.")

        card = section("Hotkeys", "Click a box, then press the combination you want. Esc cancels.")
        r = row(card, "Make a rule from the window you are in")
        self.hk_rule = HotkeyField(r, d["hotkey_new_rule"], "ctrl+alt+r", self._general_save)
        self.hk_rule.pack(side="right")
        r = row(card, "Give the current window project 1 to 9", note="With 0 it goes back to the rules.")
        box = ttk.Combobox(r, textvariable=self.v_mods, values=MODIFIER_CHOICES, state="readonly", width=12)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda e: self._general_save())

        card = section("System")
        switch(card, "Start WindowTint when I sign in", self.v_auto)
        r = row(card, "Check windows every", note="Lower is snappier, higher uses less CPU.")
        widgets.tlabel(r, text="ms", style="Tile.TLabel").pack(side="right", padx=(6, 0))
        poll = ttk.Spinbox(r, from_=100, to=5000, increment=100, textvariable=self.v_poll, width=6,
                           command=self._general_save)
        poll.pack(side="right")
        poll.bind("<Return>", lambda e: self._general_save())
        poll.bind("<FocusOut>", lambda e: self._general_save())

        self.v_status = tk.StringVar(value="Changes apply immediately.")
        ttk.Label(f, textvariable=self.v_status, style="Muted.TLabel").pack(anchor="w", pady=(14, 20))

    def _general_save(self):
        """Applies the Settings tab right away; called whenever one of its controls changes."""
        if not self.alive():
            return
        try:
            poll = min(5000, max(100, int(self.v_poll.get())))
        except (tk.TclError, ValueError):
            poll = 500
        try:
            thick = min(8, max(1, int(self.v_thick.get())))
        except (tk.TclError, ValueError):
            thick = 1
        if self.hk_switcher.value == self.hk_rule.value:
            messagebox.showerror("Hotkeys", "The switcher and the rule hotkey need different key combinations.",
                                 parent=self.win)
            self.v_status.set("Not saved: the two hotkeys must differ.")
            return
        with self.cfg.lock:
            self.cfg.data["color_border"] = bool(self.v_border.get())
            self.cfg.data["color_caption"] = bool(self.v_caption.get())
            self.cfg.data["border_px"] = thick
            self.cfg.data["sidebar_overlay"] = bool(self.v_sidebar.get())
            self.cfg.data["tab_overlay"] = bool(self.v_tabs.get())
            self.cfg.data["alt_tab"] = bool(self.v_alttab.get())
            self.cfg.data["poll_ms"] = poll
            self.cfg.data["hotkey_modifiers"] = self.v_mods.get()
            self.cfg.data["hotkey_switcher"] = self.hk_switcher.value
            self.cfg.data["hotkey_new_rule"] = self.hk_rule.value
        self.cfg.save()
        failed = self.on_hotkeys() if self.on_hotkeys else []
        new_failures = [f for f in failed if f not in self._warned]
        self._warned = set(failed)
        if new_failures:
            messagebox.showwarning("Hotkeys", "Another program already uses: " + ", ".join(new_failures) +
                                   ".\nPick a different combination.", parent=self.win)
        want_auto = bool(self.v_auto.get())
        if want_auto != winapi.autostart_enabled():
            try:
                winapi.set_autostart(want_auto)
            except OSError as e:
                messagebox.showerror("Autostart", f"Could not change the startup setting:\n{e}", parent=self.win)
        self.v_status.set("Saved.")
        self._refresh_projects()
        self.on_change()
