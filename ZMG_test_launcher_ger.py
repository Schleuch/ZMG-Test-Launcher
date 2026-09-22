# -*- coding: utf-8 -*-
"""
ZMG Test Launcher
=================
Startet DayZ (DayZDiag_x64.exe) lokal als Server + Client mit -filePatching,
sodass Mods direkt von Laufwerk P: getestet werden koennen —
ohne PBO packen, signieren oder auf den Online-Server kopieren.

Benoetigt: Python 3.8+ (nur Standardbibliothek, kein pip noetig)
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# Skriptordner (aendert sich bei jeder neuen Version)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Fester Datenordner – bleibt bei Versionswechseln erhalten
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.expanduser("~/.local/share"), "ZMG_Test_Launcher")
try:
    os.makedirs(APP_DIR, exist_ok=True)
except OSError:
    APP_DIR = SCRIPT_DIR

CONFIG_FILE = os.path.join(APP_DIR, "zmg_launcher_config.json")
OLD_CONFIG_FILE = os.path.join(SCRIPT_DIR, "zmg_launcher_config.json")

# Standard-Basisordner fuer Profiles und Missionen
DEFAULT_BASE = r"C:\ZmG_Test_Launcher"

# Mod-Liste: Spalten und sichtbare Zeilen (Rest wird gescrollt)
MOD_COLS = 3
MOD_ROWS = 10
MOD_MAX_RENDER = 150      # mehr Treffer werden nicht gleichzeitig gezeichnet

# Farbschema (dunkel, DayZ-Stil)
C_BG = "#17181b"        # Fenster-Hintergrund
C_PANEL = "#22242a"     # Buttons / Panels
C_FIELD = "#2a2d33"     # Eingabefelder
C_FG = "#d6d7d9"        # Text
C_MUTED = "#9aa0a6"     # gedaempfter Text
C_ACCENT = "#d98e2b"    # DayZ-Amber
C_ACCENT2 = "#efb45a"   # Amber hell (Hover)
C_BORDER = "#3a3d44"    # Rahmen
C_RED = "#a03c3c"       # Stopp/Gefahr

# Ordner auf P:, die keine Mods sind (Vanilla-Daten / Tools)
IGNORE_DIRS = {"dz", "scripts", "bin", "core", "languagecore", "temp",
               "editor", "mikero", "gui", "worlds_data", "$logs$"}

# Muster fuer Log-Einfaerbung
ERR_PATTERNS = ("script error", "null pointer", "cannot load", "can't compile",
                "compile error", "error compiling", " error:", "crash",
                "cannot open", "missing")
WARN_PATTERNS = ("warning",)

# Dateitypen, die der Watchdog ueberwacht
WATCH_EXT = (".c", ".cpp", ".hpp", ".layout", ".xml", ".json")

# Erkennt Datei + Zeilennummer in Fehlerzeilen, z. B.
#   P:\ZMG_Mod\scripts\4_World\foo.c(123)  oder  scripts/3_Game/bar.c:45
FILE_LINE_RE = re.compile(
    r"((?:[A-Za-z]:)?[\w@$.\-\\/]+\.(?:c|cpp|hpp|layout))"
    r"(?:\((\d+)\)|:(\d+))?", re.IGNORECASE)

DEFAULT_SERVER_CFG = """hostname = "ZMG Local Test Server";
password = "";
passwordAdmin = "admin";
maxPlayers = 4;

BattlEye = 0;                // WICHTIG: 0 = BattlEye aus (lokaler Test)
verifySignatures = 0;        // WICHTIG: 0, damit unsignierte Mods laden
allowFilePatching = 1;       // WICHTIG: 1, sonst ignoriert der Server -filePatching
forceSameBuild = 0;

disableVoN = 0;
vonCodecQuality = 20;
disable3rdPerson = 0;
disableCrosshair = 0;

serverTime = "SystemTime";
serverTimeAcceleration = 12;
serverNightTimeAcceleration = 1;
serverTimePersistent = 0;

guaranteedUpdates = 1;
loginQueueConcurrentPlayers = 5;
loginQueueMaxPlayers = 500;
instanceId = 1;
storageAutoFix = 1;

class Missions
{
    class DayZ
    {
        template = "dayzOffline.chernarusplus";
    };
};
"""


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ZMG Test Launcher")
        h = min(1040, max(760, self.winfo_screenheight() - 90))
        self.geometry("1120x%d" % h)
        self.minsize(900, 620)

        self.server_proc = None
        self.client_proc = None
        self.log_thread = None
        self.log_stop = threading.Event()

        self.mod_vars = {}          # vollstaendiger Pfad -> BooleanVar
        self.log_lines = []         # (zeile, tag) fuer Filter/Neuaufbau
        self.cfg = self.load_config()

        self.apply_dark_theme()
        self.build_ui()
        self.scan_mods()
        self.update_preset_list()
        self.update_profile_list()
        self.after(300, self.activate_present_required)
        self._dark_titlebar(self)
        if getattr(self, "_migrated", False):
            self.log("[Launcher] Einstellungen der Vorversion uebernommen "
                     f"({OLD_CONFIG_FILE}).")
            self.log(f"[Launcher] Ablage jetzt versionsunabhaengig: {APP_DIR}")
            self.save_config()
        threading.Thread(target=self.watchdog_worker, daemon=True).start()
        self.after(1000, self.watch_processes)

    # ------------------------------------------------------------------ theme
    def apply_dark_theme(self):
        self.configure(bg=C_BG)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=C_BG, foreground=C_FG,
                        fieldbackground=C_FIELD, bordercolor=C_BORDER,
                        lightcolor=C_PANEL, darkcolor=C_BG,
                        troughcolor=C_PANEL, focuscolor=C_ACCENT,
                        selectbackground=C_ACCENT, selectforeground="#141414")
        style.configure("TFrame", background=C_BG)
        style.configure("TLabel", background=C_BG, foreground=C_FG)
        style.configure("TLabelframe", background=C_BG, bordercolor=C_BORDER)
        style.configure("TLabelframe.Label", background=C_BG,
                        foreground=C_ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("TButton", background=C_PANEL, foreground=C_FG,
                        bordercolor=C_BORDER, padding=(10, 4))
        style.map("TButton",
                  background=[("pressed", "#3a3f47"), ("active", "#2f333a")],
                  foreground=[("disabled", C_MUTED)])
        style.configure("Accent.TButton", background=C_ACCENT,
                        foreground="#141414")
        style.map("Accent.TButton",
                  background=[("pressed", "#c07a1f"), ("active", C_ACCENT2)])
        style.configure("Stop.TButton", background=C_RED, foreground="#f2e6e6")
        style.map("Stop.TButton",
                  background=[("pressed", "#7c2e2e"), ("active", "#b74a4a")])
        style.configure("TEntry", fieldbackground=C_FIELD, foreground=C_FG,
                        insertcolor=C_FG, bordercolor=C_BORDER)
        style.configure("TCheckbutton", background=C_BG, foreground=C_FG)
        style.map("TCheckbutton",
                  background=[("active", C_BG)],
                  indicatorcolor=[("selected", C_ACCENT),
                                  ("!selected", C_FIELD)])
        style.configure("TCombobox", fieldbackground=C_FIELD,
                        background=C_PANEL, foreground=C_FG,
                        arrowcolor=C_FG, bordercolor=C_BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", C_FIELD)])
        style.configure("Vertical.TScrollbar", background=C_PANEL,
                        troughcolor=C_BG, bordercolor=C_BORDER,
                        arrowcolor=C_FG)
        style.configure("Horizontal.TScrollbar", background=C_PANEL,
                        troughcolor=C_BG, bordercolor=C_BORDER,
                        arrowcolor=C_FG)
        # Dropdown-Liste der Combobox (tk-Listbox, kein ttk)
        self.option_add("*TCombobox*Listbox*Background", C_FIELD)
        self.option_add("*TCombobox*Listbox*Foreground", C_FG)
        self.option_add("*TCombobox*Listbox*selectBackground", C_ACCENT)
        self.option_add("*TCombobox*Listbox*selectForeground", "#141414")

    @staticmethod
    def _dark_titlebar(win):
        """Windows-Titelleiste dunkel schalten (ab Win10 1809, sonst no-op)."""
        try:
            import ctypes
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            val = ctypes.c_int(1)
            for attr in (20, 19):        # DWMWA_USE_IMMERSIVE_DARK_MODE
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(val),
                        ctypes.sizeof(val)) == 0:
                    break
        except Exception:
            pass

    # ---------------------------------------------------------------- config
    def load_config(self):
        defaults = {
            "dayz_path": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            "client_path": "",
            "p_drive": "P:\\",
            "workshop_path": "",
            "mission_path": os.path.join(DEFAULT_BASE, "Mission",
                                         "dayzOffline.chernarusplus"),
            "server_cfg": "",
            "profiles_path": os.path.join(DEFAULT_BASE, "TestProfiles"),
            "port": "2302",
            "extra_params": "",
            "selected_mods": [],
            "presets": {},
            "extra_mods": [],
            "watchdog": False,
            "watchdog_client": False,
            "login_timer": "5",
            "server_hour": "",
            "vpp_steamid": "",
            "vpp_password": "",
        }
        source = CONFIG_FILE
        if not os.path.isfile(CONFIG_FILE) and os.path.isfile(OLD_CONFIG_FILE):
            source = OLD_CONFIG_FILE        # Einstellungen der Vorversion uebernehmen
        try:
            with open(source, "r", encoding="utf-8") as f:
                defaults.update(json.load(f))
            self._migrated = source != CONFIG_FILE
        except (OSError, json.JSONDecodeError):
            pass

        # Sinnvolle Ableitungen fuer den ersten Start (nur wenn Feld leer):
        dp = defaults.get("dayz_path", "")
        if not defaults.get("workshop_path"):
            i = dp.lower().find("steamapps")
            if i != -1:
                defaults["workshop_path"] = os.path.join(
                    dp[:i + len("steamapps")], "workshop", "content", "221100")
        if not defaults.get("server_cfg") and dp:
            defaults["server_cfg"] = os.path.join(dp, "serverDZ_test.cfg")
        return defaults

    def save_config(self):
        self.cfg["dayz_path"] = self.e_dayz.get().strip()
        self.cfg["client_path"] = self.e_client.get().strip()
        self.cfg["p_drive"] = self.e_pdrive.get().strip()
        self.cfg["workshop_path"] = self.e_workshop.get().strip()
        self.cfg["mission_path"] = self.e_mission.get().strip()
        self.cfg["server_cfg"] = self.e_servercfg.get().strip()
        self.cfg["profiles_path"] = self.e_profiles.get().strip()
        self.cfg["port"] = self.e_port.get().strip()
        self.cfg["extra_params"] = self.e_extra.get().strip()
        self.cfg["selected_mods"] = [m for m, v in self.mod_vars.items() if v.get()]
        for key, v in getattr(self, "v_src", {}).items():
            self.cfg["src_" + key] = v.get()
        self.cfg["temp_pbo"] = self.v_temp_pbo.get()
        self.cfg["client_no_diag"] = self.v_no_diag.get()
        self.cfg["wrap_sources"] = self.v_wrap.get()
        self.cfg["watchdog"] = self.v_watchdog.get()
        self.cfg["watchdog_client"] = self.v_wd_client.get()
        self.cfg["login_timer"] = self.e_login.get().strip()
        self.cfg["server_hour"] = self.e_hour.get().strip()
        if hasattr(self, "cb_profile"):
            self.cfg.setdefault("profiles_sets", {})[
                self.cfg.get("active_profile", "Chernarus Stable")] = \
                self._collect_profile()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.log(f"[Launcher] Konnte Config nicht speichern: {e}")

    # --------------------------------------------------------------- profile
    def exp_defaults(self):
        """Vorschlagswerte fuer ein Experimental-Profil."""
        dp = self.e_dayz.get().strip()
        i = dp.lower().find("steamapps")
        stem = dp[:i + len("steamapps")] if i != -1 else dp
        exp = os.path.join(stem, "common", "DayZ Server Exp")
        ws = self.e_workshop.get().strip() or os.path.join(
            stem, "workshop", "content", "221100")
        return {
            "dayz_path": exp,
            "client_path": os.path.join(stem, "common", "DayZ Exp"),
            "p_drive": self.e_pdrive.get().strip(),
            "workshop_path": ws,
            "server_cfg": os.path.join(exp, "serverDZ_test.cfg"),
            "mission_path": os.path.join(DEFAULT_BASE, "Mission_Exp",
                                         "dayzOffline.chernarusplus"),
            "profiles_path": os.path.join(DEFAULT_BASE, "TestProfiles_Exp"),
            "port": "2402",
            "extra_params": "",
            "selected_mods": [],
            "mod_order": [],
        }

    def _collect_profile(self):
        # Pflicht-Mods gehoeren zum Profil und duerfen beim Speichern
        # nicht verlorengehen
        stored = self.cfg.get("profiles_sets", {}).get(
            self.cfg.get("active_profile", ""), {})
        return {
            "required_mods": [dict(m) for m in
                              stored.get("required_mods", [])],
            "dayz_path": self.e_dayz.get().strip(),
            "client_path": self.e_client.get().strip(),
            "p_drive": self.e_pdrive.get().strip(),
            "workshop_path": self.e_workshop.get().strip(),
            "server_cfg": self.e_servercfg.get().strip(),
            "mission_path": self.e_mission.get().strip(),
            "profiles_path": self.e_profiles.get().strip(),
            "port": self.e_port.get().strip(),
            "extra_params": self.e_extra.get().strip(),
            "selected_mods": [m for m, v in self.mod_vars.items() if v.get()],
            "mod_order": list(self.cfg.get("mod_order", [])),
        }

    def _apply_profile(self, data):
        for entry, key in ((self.e_dayz, "dayz_path"),
                           (self.e_client, "client_path"),
                           (self.e_pdrive, "p_drive"),
                           (self.e_workshop, "workshop_path"),
                           (self.e_servercfg, "server_cfg"),
                           (self.e_mission, "mission_path"),
                           (self.e_profiles, "profiles_path"),
                           (self.e_port, "port"),
                           (self.e_extra, "extra_params")):
            val = data.get(key, "")
            # Felder, die ein Profil nicht zwingend speichert, nicht leeren
            if not val and key in ("workshop_path", "p_drive", "dayz_path"):
                val = entry.get().strip()
            entry.delete(0, "end")
            entry.insert(0, val)
        self.cfg["selected_mods"] = data.get("selected_mods", [])
        self.cfg["mod_order"] = data.get("mod_order", [])

    # Vorgegebene Map-Profile: Name -> (Missionsordner, Port)
    DEFAULT_PROFILES = (
        ("Chernarus Stable", "dayzOffline.chernarusplus", "2302"),
        ("Livonia Stable", "dayzOffline.enoch", "2502"),
        ("Sakhal Stable", "dayzOffline.sakhal", "2602"),
        ("Namalsk Stable", "regular.namalsk", "2702"),
    )

    def _stable_root(self):
        """Pfad der Stable-Installation aus vorhandenen Profilen ableiten."""
        sets = self.cfg.get("profiles_sets", {})
        for name, data in sets.items():
            if "exp" not in name.lower() and data.get("dayz_path"):
                return data["dayz_path"]
        cur = self.e_dayz.get().strip()
        if cur and "exp" not in cur.lower():
            return cur
        return r"C:\Program Files (x86)\Steam\steamapps\common\DayZ"

    # Pflicht-Mods je Map (Workshop-ID, Name)
    MAP_REQUIRED = {
        "regular.namalsk": [{"id": "2289456201", "name": "Namalsk Island"},
                            {"id": "2289461232", "name": "Namalsk Survival"}],
    }

    def map_profile_defaults(self, folder, port):
        root = self._stable_root()
        short = folder.split(".")[-1]
        return {
            "required_mods": [dict(m) for m in
                              self.MAP_REQUIRED.get(folder, [])],
            "dayz_path": root,
            "client_path": "",
            "p_drive": self.e_pdrive.get().strip() or "P:\\",
            "workshop_path": self.e_workshop.get().strip(),
            "server_cfg": os.path.join(root, self.cfg_name_for(folder)),
            "mission_path": os.path.join(DEFAULT_BASE, "Mission_" + short,
                                         folder),
            "profiles_path": os.path.join(DEFAULT_BASE,
                                          "TestProfiles_" + short),
            "port": port,
            "extra_params": "",
            "selected_mods": [],
            "mod_order": [],
        }

    def seed_default_profiles(self):
        sets = self.cfg.setdefault("profiles_sets", {})
        # altes Sammelprofil "Stable" in das Chernarus-Profil ueberfuehren
        if "Stable" in sets and "Chernarus Stable" not in sets:
            sets["Chernarus Stable"] = sets.pop("Stable")
            if self.cfg.get("active_profile") == "Stable":
                self.cfg["active_profile"] = "Chernarus Stable"
        for name, folder, port in self.DEFAULT_PROFILES:
            if name not in sets:
                sets[name] = self.map_profile_defaults(folder, port)
                continue
            # Altbestand: gemeinsame serverDZ_test.cfg auf eigene umstellen
            want = self.cfg_name_for(folder)
            cur = sets[name].get("server_cfg", "")
            if cur and os.path.basename(cur) != want:
                sets[name]["server_cfg"] = os.path.join(
                    os.path.dirname(cur), want)

    def update_map_label(self):
        mission = self.e_mission.get().strip()
        name = os.path.basename(os.path.normpath(mission)) if mission else ""
        if name:
            self.lbl_map.config(text="Map: %s" % self._pretty_map(name))
        else:
            self.lbl_map.config(text="")

    def seed_required_mods(self):
        """Bekannte Pflicht-Mods in Profile ohne Eintraege nachtragen."""
        for name, data in self.cfg.get("profiles_sets", {}).items():
            folder = os.path.basename(os.path.normpath(
                data.get("mission_path", "")))
            req = self.MAP_REQUIRED.get(folder)
            if not req:
                continue
            have = data.setdefault("required_mods", [])
            known = {m.get("id", "") for m in have}
            known |= {m.get("name", "").lower() for m in have}
            added = 0
            for m in req:
                if m["id"] in known or m["name"].lower() in known:
                    continue
                have.append(dict(m))
                added += 1
            if added:
                self.log("[Mods] Profil %s: %d Pflicht-Mod(s) ergaenzt." % (name, added))

    def normalize_profile_cfgs(self):
        """Jedem Profil eine eigene Server-Config geben (keine Dubletten)."""
        sets = self.cfg.get("profiles_sets", {})
        used = set()
        for name in sorted(sets):
            data = sets[name]
            cur = data.get("server_cfg", "")
            if not cur:
                continue
            folder = os.path.dirname(cur)
            key = os.path.normpath(cur).lower()
            if key not in used:
                used.add(key)
                continue
            # Dublette: eigenen Dateinamen finden
            mission = os.path.basename(os.path.normpath(
                data.get("mission_path", "")))
            slug = self.profile_slug(name)
            candidates = []
            if mission:
                candidates.append(self.cfg_name_for(mission))
            candidates.append("serverDZ_%s_test.cfg" % slug)
            candidates += ["serverDZ_%s_%d_test.cfg" % (slug, i)
                           for i in range(2, 10)]
            for cand in candidates:
                new_path = os.path.join(folder, cand)
                nkey = os.path.normpath(new_path).lower()
                if nkey in used:
                    continue
                data["server_cfg"] = new_path
                used.add(nkey)
                self.log("[Launcher] Profil %s bekommt eigene Config: %s" % (name, cand))
                break

    def update_profile_list(self):
        sets = self.cfg.setdefault("profiles_sets", {})
        active = self.cfg.setdefault("active_profile", "Chernarus Stable")
        sets.setdefault(active, self._collect_profile())
        self.seed_default_profiles()
        self.normalize_profile_cfgs()
        self.seed_required_mods()
        active = self.cfg["active_profile"]
        if "EXP" not in sets:
            sets["EXP"] = self.exp_defaults()
        self.cb_profile["values"] = sorted(sets)
        self.cb_profile.set(active)
        self.update_map_label()

    def switch_profile(self, event=None):
        name = self.cb_profile.get()
        active = self.cfg.get("active_profile", "Chernarus Stable")
        if name == active:
            return
        self.cfg.setdefault("profiles_sets", {})[active] = self._collect_profile()
        data = self.cfg["profiles_sets"].get(name)
        if data is None:
            data = self.new_profile_defaults(name)
            self.cfg["profiles_sets"][name] = data
        self.cfg["active_profile"] = name
        self._apply_profile(data)
        self.update_map_label()
        self.ensure_profile_cfg()
        self.sync_cfg_template()
        self.after(200, self.check_required_mods)
        self.save_config()
        self.scan_mods()
        self.log("[Launcher] Profil gewechselt: " + name)

    @staticmethod
    def profile_slug(name):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
        return slug or "Profil"

    def free_port(self):
        """Freien Port finden (100er-Schritte ueber dem hoechsten vergebenen)."""
        used = []
        for data in self.cfg.get("profiles_sets", {}).values():
            try:
                used.append(int(data.get("port", "2302")))
            except ValueError:
                pass
        return str((max(used) if used else 2302) + 100)

    def new_profile_defaults(self, name):
        """Neues Profil: eigener Missions- und Profiles-Ordner, freier Port."""
        base = self.exp_defaults() if "exp" in name.lower() \
            else self._collect_profile()
        slug = self.profile_slug(name)
        base["mission_path"] = os.path.join(DEFAULT_BASE, "Mission_" + slug)
        root = os.path.dirname(base.get("server_cfg", "")) or \
            base.get("dayz_path", "")
        base["server_cfg"] = os.path.join(root, "serverDZ_%s_test.cfg" % slug)
        base["profiles_path"] = os.path.join(DEFAULT_BASE,
                                             "TestProfiles_" + slug)
        base["port"] = self.free_port()
        for d in (base["mission_path"], base["profiles_path"]):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                self.log("[Launcher] %s" % e)
        return base

    def new_profile(self):
        name = simpledialog.askstring("Neues Profil", "Name des Profils.\n\nEs bekommt automatisch einen eigenen Missions- und Profiles-Ordner sowie einen freien Port.\nEnthaelt der Name 'Exp', werden Experimental-Pfade genutzt.",
                                      initialvalue="", parent=self)
        if not name:
            return
        name = name.strip()
        if name in self.cfg.get("profiles_sets", {}):
            messagebox.showinfo("Profil", "Dieses Profil gibt es bereits.")
            return
        self.cfg.setdefault("profiles_sets", {})[
            self.cfg.get("active_profile", "Chernarus Stable")] = self._collect_profile()
        self.cfg["profiles_sets"][name] = self.new_profile_defaults(name)
        self.cfg["active_profile"] = name
        self.normalize_profile_cfgs()
        self._apply_profile(self.cfg["profiles_sets"][name])
        self.ensure_profile_cfg()
        self.update_profile_list()
        self.save_config()
        self.scan_mods()
        self.log("[Launcher] Profil angelegt: " + name)

    def delete_profile(self):
        name = self.cb_profile.get()
        sets = self.cfg.get("profiles_sets", {})
        if len(sets) < 2:
            messagebox.showinfo("Profil", "Das letzte Profil kann nicht geloescht werden.")
            return
        if not messagebox.askyesno("Profil loeschen", "Profil wirklich loeschen: " + name):
            return
        sets.pop(name, None)
        nxt = sorted(sets)[0]
        self.cfg["active_profile"] = nxt
        self._apply_profile(sets[nxt])
        self.update_profile_list()
        self.save_config()
        self.scan_mods()

    # ------------------------------------------------------------------- ui
    def build_ui(self):
        pad = {"padx": 6, "pady": 3}

        frm_prof = ttk.Frame(self)
        frm_prof.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(frm_prof, text="Profil:").pack(side="left")
        self.cb_profile = ttk.Combobox(frm_prof, width=20, state="readonly")
        self.cb_profile.pack(side="left", padx=6)
        self.cb_profile.bind("<<ComboboxSelected>>", self.switch_profile)
        ttk.Button(frm_prof, text="Neu",
                   command=self.new_profile).pack(side="left", padx=3)
        ttk.Button(frm_prof, text="Loeschen",
                   command=self.delete_profile).pack(side="left")
        ttk.Button(frm_prof, text="Benoetigte Mods",
                   command=self.open_required_mods).pack(side="left", padx=(12, 0))
        self.lbl_map = ttk.Label(frm_prof, text="", foreground=C_MUTED)
        self.lbl_map.pack(side="left", padx=10)

        frm_paths = ttk.LabelFrame(self, text="Pfade & Einstellungen")
        frm_paths.pack(fill="x", padx=8, pady=6)
        frm_paths.columnconfigure(1, weight=1)

        def row(r, label, key, browse_dir=True, filetypes=None):
            ttk.Label(frm_paths, text=label).grid(row=r, column=0, sticky="w", **pad)
            e = ttk.Entry(frm_paths)
            e.insert(0, self.cfg.get(key, ""))
            e.grid(row=r, column=1, sticky="ew", **pad)

            def browse():
                if browse_dir:
                    p = filedialog.askdirectory(initialdir=e.get() or "C:\\")
                else:
                    p = filedialog.askopenfilename(
                        initialdir=os.path.dirname(e.get()) or "C:\\",
                        filetypes=filetypes or [("Alle Dateien", "*.*")])
                if p:
                    e.delete(0, "end")
                    e.insert(0, os.path.normpath(p))
            ttk.Button(frm_paths, text="...", width=3,
                       command=browse).grid(row=r, column=2, **pad)
            return e

        self.e_dayz = row(0, "DayZ-Ordner (Server):", "dayz_path")
        self.e_client = row(1, "DayZ-Ordner (Client, optional):", "client_path")
        self.e_pdrive = row(2, "Laufwerk P (Mod-Quelle):", "p_drive")
        self.e_workshop = row(3, "Workshop-Ordner (!Workshop):", "workshop_path")
        self.e_servercfg = row(4, "serverDZ.cfg:", "server_cfg", browse_dir=False,
                               filetypes=[("cfg", "*.cfg"), ("Alle", "*.*")])
        ttk.Button(frm_paths, text="Erstellen",
                   command=self.create_server_cfg).grid(row=4, column=3, **pad)
        self.e_mission = row(5, "Mission-Ordner (Server):", "mission_path")
        ttk.Button(frm_paths, text="Erstellen",
                   command=self.download_missions).grid(row=5, column=3, **pad)
        self.e_profiles = row(6, "Profiles-Ordner (Logs):", "profiles_path")

        ttk.Label(frm_paths, text="Port:").grid(row=7, column=0, sticky="w", **pad)
        sub = ttk.Frame(frm_paths)
        sub.grid(row=7, column=1, columnspan=2, sticky="ew", **pad)
        self.e_port = ttk.Entry(sub, width=8)
        self.e_port.insert(0, self.cfg.get("port", "2302"))
        self.e_port.pack(side="left")
        ttk.Label(sub, text="   Zusatzparameter:").pack(side="left")
        self.e_extra = ttk.Entry(sub)
        self.e_extra.insert(0, self.cfg.get("extra_params", ""))
        self.e_extra.pack(side="left", fill="x", expand=True, padx=4)

        # Mods
        frm_mods = ttk.LabelFrame(self, text="Verfügbare Mods:")
        frm_mods.pack(fill="x", padx=8, pady=4)
        top = ttk.Frame(frm_mods)
        top.pack(fill="x")
        ttk.Button(top, text="Neu scannen", command=self.scan_mods).pack(side="left", padx=6, pady=4)
        ttk.Button(top, text="＋ PBOs als Mod", command=self.add_pbos).pack(side="left", padx=(14, 3))
        ttk.Button(top, text="＋ Mod-Ordner", command=self.add_mod_folder).pack(side="left")
        ttk.Label(top, text="Preset:").pack(side="left", padx=(18, 2))
        self.cb_preset = ttk.Combobox(top, width=18)
        self.cb_preset.pack(side="left")
        self.cb_preset.bind("<<ComboboxSelected>>", self.apply_preset)
        ttk.Button(top, text="Speichern", command=self.save_preset).pack(side="left", padx=3)
        ttk.Button(top, text="Löschen", command=self.delete_preset).pack(side="left")
        ttk.Button(top, text="Ladereihenfolge",
                   command=self.open_load_order).pack(side="left", padx=(14, 0))
        self.b_vpp = ttk.Button(top, text="VPP Einstellungen",
                                command=self.open_vpp_settings)
        # wird nur eingeblendet, wenn ein VPP-Mod angehakt ist

        # Suchzeile
        srow = ttk.Frame(frm_mods)
        srow.pack(fill="x", padx=6)
        ttk.Label(srow, text="Suche:").pack(side="left")
        self.e_search = ttk.Entry(srow, width=28)
        self.e_search.pack(side="left", padx=4, pady=(0, 4))
        self.e_search.bind("<KeyRelease>", lambda e: self.schedule_render())
        ttk.Button(srow, text="✕", width=3,
                   command=self.clear_search).pack(side="left")
        self.v_src = {}
        for key, text in (("ws", "Workshop"), ("p", "Laufwerk P"),
                          ("pbo", "PBOs"), ("dir", "Mod-Ordner")):
            v = tk.BooleanVar(value=bool(self.cfg.get("src_" + key, True)))
            self.v_src[key] = v
            ttk.Checkbutton(srow, text=text, variable=v,
                            command=self.render_mods).pack(side="left", padx=(8, 0))
        self.v_only_active = tk.BooleanVar(value=False)
        ttk.Checkbutton(srow, text="Nur aktive", variable=self.v_only_active,
                        command=self.render_mods).pack(side="left", padx=(10, 0))
        self.lbl_modcount = ttk.Label(srow, text="", foreground=C_MUTED)
        self.lbl_modcount.pack(side="left", padx=10)

        # Scrollbarer Bereich fuer die Mod-Checkboxen
        wrap = ttk.Frame(frm_mods)
        wrap.pack(fill="x", padx=6, pady=(0, 6))
        row_h = 22
        self.mod_canvas = tk.Canvas(wrap, bg=C_BG, highlightthickness=0,
                                    height=MOD_ROWS * row_h)
        msb = ttk.Scrollbar(wrap, orient="vertical",
                            command=self.mod_canvas.yview)
        self.mod_canvas.configure(yscrollcommand=msb.set)
        msb.pack(side="right", fill="y")
        self.mod_canvas.pack(side="left", fill="x", expand=True)
        self.mod_frame = ttk.Frame(self.mod_canvas)
        self._mod_window = self.mod_canvas.create_window(
            (0, 0), window=self.mod_frame, anchor="nw")
        self.mod_frame.bind(
            "<Configure>",
            lambda e: self.mod_canvas.configure(
                scrollregion=self.mod_canvas.bbox("all")))
        self.mod_canvas.bind(
            "<Configure>",
            lambda e: self.mod_canvas.itemconfigure(self._mod_window,
                                                    width=e.width))
        for widget in (self.mod_canvas, self.mod_frame):
            widget.bind("<Enter>", lambda e: self._bind_wheel(True))
            widget.bind("<Leave>", lambda e: self._bind_wheel(False))

        # Buttons
        frm_btn = ttk.Frame(self)
        frm_btn.pack(fill="x", padx=8, pady=4)
        self.b_server = ttk.Button(frm_btn, text="▶ Server starten",
                                   style="Accent.TButton", command=self.start_server)
        self.b_server.pack(side="left", padx=4)
        self.b_client = ttk.Button(frm_btn, text="▶ Client starten",
                                   style="Accent.TButton", command=self.start_client)
        self.b_client.pack(side="left", padx=4)
        ttk.Button(frm_btn, text="▶▶ Beides starten", style="Accent.TButton",
                   command=self.start_both).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="■ Alles stoppen", style="Stop.TButton",
                   command=self.stop_all).pack(side="left", padx=12)
        ttk.Button(frm_btn, text="🗑 Storage wipen", command=self.wipe_storage).pack(side="left", padx=4)
        self.v_watchdog = tk.BooleanVar(value=bool(self.cfg.get("watchdog", False)))
        self.v_wd_client = tk.BooleanVar(value=bool(self.cfg.get("watchdog_client", False)))
        ttk.Checkbutton(frm_btn, text="Auto-Restart bei Änderung",
                        variable=self.v_watchdog).pack(side="left", padx=(14, 2))
        ttk.Checkbutton(frm_btn, text="+ Client",
                        variable=self.v_wd_client).pack(side="left")
        self.v_no_diag = tk.BooleanVar(
            value=bool(self.cfg.get("client_no_diag", False)))
        ttk.Checkbutton(frm_btn, text="Client ohne Diag",
                        variable=self.v_no_diag).pack(side="left", padx=(14, 0))
        self.v_wrap = tk.BooleanVar(value=bool(self.cfg.get("wrap_sources", True)))
        ttk.Checkbutton(frm_btn, text="P:-Quellen automatisch packen",
                        variable=self.v_wrap).pack(side="left", padx=(14, 0))
        self.lbl_status = ttk.Label(frm_btn, text="Server: aus   |   Client: aus")
        self.lbl_status.pack(side="right", padx=6)

        # Schnell-Einstellungen (schreiben direkt in Mission/Server-Config)
        frm_quick = ttk.Frame(self)
        frm_quick.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Label(frm_quick, text="Login-Timer (s):").pack(side="left", padx=(2, 3))
        self.e_login = ttk.Entry(frm_quick, width=5)
        self.e_login.insert(0, self.cfg.get("login_timer", "5"))
        self.e_login.pack(side="left")
        ttk.Button(frm_quick, text="Setzen",
                   command=self.set_login_timer).pack(side="left", padx=(3, 16))
        ttk.Label(frm_quick, text="Serverzeit beim Start (Stunde, leer = Systemzeit):"
                  ).pack(side="left", padx=(0, 3))
        self.e_hour = ttk.Entry(frm_quick, width=7)
        self.e_hour.insert(0, self.cfg.get("server_hour", ""))
        self.e_hour.pack(side="left")
        ttk.Button(frm_quick, text="Setzen",
                   command=self.set_server_time).pack(side="left", padx=3)
        self.v_temp_pbo = tk.BooleanVar(value=bool(self.cfg.get("temp_pbo", False)))
        ttk.Checkbutton(frm_quick, text="PBOs nach dem Test löschen",
                        variable=self.v_temp_pbo).pack(side="right", padx=6)
        self.b_cache = ttk.Button(frm_quick, text="PBO-Cache leeren",
                                  command=self.clear_pbo_cache)
        self.b_cache.pack(side="right", padx=4)

        # Log
        frm_log = ttk.LabelFrame(self, text="Server-Log (neueste *.RPT im Profiles-Ordner)")
        frm_log.pack(fill="both", expand=True, padx=8, pady=6)
        logtop = ttk.Frame(frm_log)
        logtop.pack(fill="x")
        self.v_only_err = tk.BooleanVar(value=False)
        ttk.Checkbutton(logtop, text="Nur Fehler zeigen", variable=self.v_only_err,
                        command=self.re_render_log).pack(side="left", padx=6, pady=2)
        ttk.Button(logtop, text="Log leeren",
                   command=self.clear_log).pack(side="left", padx=6)
        ttk.Button(logtop, text="📂 Profiles-Ordner öffnen",
                   command=self.open_profiles).pack(side="left", padx=6)
        ttk.Button(logtop, text="🔁 JSON Hot-Reload",
                   command=self.open_hotreload).pack(side="left", padx=6)
        self.txt = tk.Text(frm_log, wrap="none", bg="#101114", fg="#cfd2d6",
                           insertbackground="#cfd2d6", relief="flat",
                           highlightthickness=1, highlightbackground="#3a3d44",
                           font=("Consolas", 9))
        self.txt.tag_configure("err", foreground="#ff6b6b")
        self.txt.tag_configure("warn", foreground="#ffd93d")
        self.link_count = 0
        ysb = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ mods
    def scan_mods(self):
        self.mod_vars.clear()
        self.mod_source = {}
        self.mod_notes = []          # Hinweiszeilen (z. B. Pfad fehlt)

        mods = []  # Liste aus (Anzeigename, voller Pfad); Workshop zuerst!

        # 1) Workshop-Mods (fertige @-Ordner, z. B. @CF, @Dabs Framework)
        ws = self.e_workshop.get().strip()
        if not ws:
            ws = os.path.join(self.e_dayz.get().strip(), "!Workshop")
        if os.path.isdir(ws):
            try:
                for name in sorted(os.listdir(ws), key=str.lower):
                    full = os.path.join(ws, name)
                    if not os.path.isdir(full):
                        continue
                    if name.startswith("@"):
                        mods.append((name, full))
                        self.mod_source[full] = "ws"
                    elif name.isdigit():
                        # Steam-ID-Layout (steamapps\workshop\content\221100)
                        label = self._workshop_name(full)
                        if label:
                            mods.append((label, full))
                        else:
                            mods.append(("ID " + name, full))
                        self.mod_source[full] = "ws"
            except OSError:
                pass

        # 2) Eigene Mods von Laufwerk P
        p = self.e_pdrive.get().strip() or "P:\\"
        if os.path.isdir(p):
            try:
                for name in sorted(os.listdir(p), key=str.lower):
                    full = os.path.join(p, name)
                    if not os.path.isdir(full):
                        continue
                    if name.lower() in IGNORE_DIRS or name.startswith((".", "$")):
                        continue
                    mods.append(("[P:] " + name, full))
                    self.mod_source[full] = "p"
            except OSError as e:
                self.mod_notes.append(f"Fehler beim Scannen von P: {e}")
        else:
            self.mod_notes.append(f"Pfad nicht gefunden: {p}")

        # 3) Manuell hinzugefuegte Mods ([PBO]: PBO-Import & externe Ordner)
        extra = []
        seen_local = set()
        for local_dir in (os.path.join(APP_DIR, "LocalMods"),
                          os.path.join(SCRIPT_DIR, "LocalMods")):
            if not os.path.isdir(local_dir):
                continue
            for name in sorted(os.listdir(local_dir), key=str.lower):
                full = os.path.join(local_dir, name)
                if os.path.isdir(full) and name.lower() not in seen_local:
                    seen_local.add(name.lower())
                    extra.append((full, "pbo"))
        for full in self.cfg.get("extra_mods", []):
            if os.path.isdir(full) and full not in [e[0] for e in extra]:
                extra.append((full, "dir"))
        for full, kind in extra:
            tag = "[PBO] " if kind == "pbo" else "[Ordner] "
            mods.append((tag + os.path.basename(full), full))
            self.mod_source[full] = kind

        selected = set(self.cfg.get("selected_mods", []))
        self.all_mods = mods
        self.mod_labels = {}
        for label, full in mods:
            var = tk.BooleanVar(value=full in selected)
            self.mod_vars[full] = var
            self.mod_labels[full] = label
            var.trace_add("write", lambda *a, p=full: self._on_mod_toggle(p))

        self.render_mods()
        self._update_vpp_button()
        self.activate_present_required()

    def clear_search(self):
        self.e_search.delete(0, "end")
        self.render_mods()

    def _bind_wheel(self, on):
        if on:
            self.mod_canvas.bind_all("<MouseWheel>", self._on_wheel)
            self.mod_canvas.bind_all("<Button-4>", self._on_wheel)
            self.mod_canvas.bind_all("<Button-5>", self._on_wheel)
        else:
            self.mod_canvas.unbind_all("<MouseWheel>")
            self.mod_canvas.unbind_all("<Button-4>")
            self.mod_canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.mod_canvas.yview_scroll(delta, "units")

    def schedule_render(self, delay=220):
        """Neuzeichnen entprellen - sonst ruckelt die Eingabe bei vielen Mods."""
        job = getattr(self, "_render_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._render_job = self.after(delay, self.render_mods)

    def _mod_cell(self, i):
        """Zeile aus dem Widget-Pool holen (oder neu anlegen)."""
        pool = self.mod_pool
        while len(pool) <= i:
            cell = ttk.Frame(self.mod_frame)
            cb = ttk.Checkbutton(cell)
            cb.pack(side="left")
            lab = ttk.Label(cell, cursor="hand2")
            lab.pack(side="left")
            lab.bind("<Enter>", lambda e, w=lab: w.configure(foreground=C_ACCENT))
            lab.bind("<Leave>", lambda e, w=lab: w.configure(foreground=C_FG))
            pool.append((cell, cb, lab))
        return pool[i]

    def render_mods(self):
        """Mod-Checkboxen zeichnen - gefiltert nach dem Suchfeld."""
        self._render_job = None
        if not hasattr(self, "mod_pool"):
            self.mod_pool = []
            self.note_pool = []

        query = self.e_search.get().strip().lower()
        mods = getattr(self, "all_mods", [])
        only_active = self.v_only_active.get()
        srcs = {k for k, v in self.v_src.items() if v.get()}
        source = getattr(self, "mod_source", {})
        shown = [(l, p) for l, p in mods
                 if (not query or query in l.lower() or query in p.lower())
                 and (not only_active or self.mod_vars[p].get())
                 and source.get(p, "ws") in srcs]

        notes = list(getattr(self, "mod_notes", []))
        limited = len(shown) > MOD_MAX_RENDER
        visible = shown[:MOD_MAX_RENDER] if limited else shown
        term = self.e_search.get().strip()
        if not mods:
            notes.append("Keine Mod-Ordner gefunden.")
        elif not shown:
            notes.append("Keine aktiven Mods" if only_active and not query
                         else "Kein Treffer fuer: %s" % term)
        elif limited:
            notes.append("Nur die ersten %d von %d Treffern angezeigt - Suche eingrenzen." % (MOD_MAX_RENDER, len(shown)))

        while len(self.note_pool) < len(notes):
            self.note_pool.append(ttk.Label(self.mod_frame, foreground=C_MUTED))
        for i, lbl in enumerate(self.note_pool):
            if i < len(notes):
                lbl.configure(text=notes[i])
                lbl.grid(row=i, column=0, columnspan=MOD_COLS,
                         sticky="w", padx=6)
            else:
                lbl.grid_remove()
        row = len(notes)

        for i, (label, full) in enumerate(visible):
            cell, cb, lab = self._mod_cell(i)
            cb.configure(variable=self.mod_vars[full])
            lab.configure(text=label, foreground=C_FG)
            lab.bind("<Button-1>", lambda e, p=full: self.open_mod_link(p))
            if source.get(full) in ("pbo", "dir"):
                for w in (cb, lab):
                    w.bind("<Button-3>",
                           lambda e, p=full: self.remove_extra_mod(p))
            else:
                cb.unbind("<Button-3>")
                lab.unbind("<Button-3>")
            cell.grid(row=row + i // MOD_COLS, column=i % MOD_COLS,
                      sticky="w", padx=6, pady=1)
        for j in range(len(visible), len(self.mod_pool)):
            self.mod_pool[j][0].grid_remove()

        checked = sum(1 for v in self.mod_vars.values() if v.get())
        self.lbl_modcount.config(text="%d/%d angezeigt - %d aktiv" % (len(shown), len(mods), checked))
        self.mod_canvas.yview_moveto(0)

    @staticmethod
    def _workshop_id(folder):
        """Steam-Workshop-ID eines Mod-Ordners ermitteln (oder None)."""
        base = os.path.basename(os.path.normpath(folder))
        if base.isdigit():
            return base
        meta = os.path.join(folder, "meta.cpp")
        try:
            with open(meta, "r", encoding="utf-8", errors="replace") as f:
                m = re.search(r"publishedid\s*=\s*(\d+)", f.read())
                if m and m.group(1) != "0":
                    return m.group(1)
        except OSError:
            pass
        return None

    def open_mod_link(self, path):
        """Klick auf einen Mod-Namen: Workshop-Seite oder Ordner oeffnen."""
        import webbrowser
        wid = self._workshop_id(path)
        if wid:
            url = ("https://steamcommunity.com/sharedfiles/filedetails/?id="
                   + wid)
            webbrowser.open(url)
            self.log(f"[Mods] Workshop-Seite geoeffnet: {url}")
        else:
            self.log(f"[Mods] Keine Workshop-ID – oeffne Ordner: {path}")
            self.open_in_default(path)

    # ------------------------------------------------- Auto-Packen (PBO)
    PBO_SKIP_NAMES = {"$pboprefix$", "$pboprefix$.txt", "meta.cpp", "mod.cpp"}
    PBO_SKIP_EXT = (".pbo", ".bisign", ".biprivatekey", ".bak", ".log",
                    ".psd", ".blend")
    PBO_SKIP_DIRS = {".git", ".svn", "__pycache__", ".vs"}

    def _stage_dir(self):
        return os.path.join(APP_DIR, "_filePatching",
                            self.cfg.get("active_profile", "Chernarus Stable"))

    @staticmethod
    def _has_config(folder):
        return (os.path.isfile(os.path.join(folder, "config.cpp")) or
                os.path.isfile(os.path.join(folder, "config.bin")))

    @classmethod
    def _pbo_files(cls, src):
        out = []
        for dirpath, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d.lower() not in cls.PBO_SKIP_DIRS]
            for fn in sorted(files):
                if fn.lower() in cls.PBO_SKIP_NAMES or \
                        fn.lower().endswith(cls.PBO_SKIP_EXT):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src).replace("/", "\\")
                out.append((rel, full))
        return out

    @staticmethod
    def _pbo_prefix(src, fallback):
        """Prefix aus $PBOPREFIX$ lesen, sonst Ordnername."""
        for name in ("$PBOPREFIX$", "$PBOPREFIX$.txt"):
            p = os.path.join(src, name)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                if "=" in line and \
                                        line.lower().startswith("prefix"):
                                    line = line.split("=", 1)[1].strip()
                                return line
                except OSError:
                    pass
        return fallback

    def build_pbo(self, src, out_path, prefix):
        """Ordner als unkomprimiertes PBO schreiben (Format wie AddonBuilder)."""
        import hashlib
        import struct
        entries = self._pbo_files(src)
        head = bytearray()
        head += b"\x00" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0)
        head += b"prefix\x00" + prefix.encode("cp1252", "replace") + b"\x00"
        head += b"\x00"
        data = bytearray()
        for rel, full in entries:
            size = os.path.getsize(full)
            head += rel.encode("cp1252", "replace") + b"\x00"
            head += struct.pack("<5I", 0, size, 0,
                                int(os.path.getmtime(full)), size)
            with open(full, "rb") as f:
                data += f.read()
        head += b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0)
        body = bytes(head) + bytes(data)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(body)
            f.write(b"\x00")
            f.write(hashlib.sha1(body).digest())
        return len(entries), len(body)

    def _needs_pack(self, src, pbo):
        if not os.path.isfile(pbo):
            return True
        stamp = os.path.getmtime(pbo)
        for _, full in self._pbo_files(src):
            try:
                if os.path.getmtime(full) > stamp:
                    return True
            except OSError:
                return True
        return False

    def wrap_source(self, path):
        """P:-Quelle nach _filePatching\\@Name\\addons\\<Prefix>.pbo packen."""
        name = os.path.basename(os.path.normpath(path))
        if self._has_config(path):
            entries = [(name, path)]
        else:
            entries = []
            try:
                for sub in sorted(os.listdir(path), key=str.lower):
                    subp = os.path.join(path, sub)
                    if os.path.isdir(subp) and self._has_config(subp):
                        entries.append((sub, subp))
            except OSError:
                pass
        if not entries:
            return path

        addons = os.path.join(self._stage_dir(), "@" + name, "addons")
        for folder_name, target in entries:
            prefix = self._pbo_prefix(target, folder_name)
            # Altlast aus der Junction-Version entfernen
            link = os.path.join(addons, prefix)
            if os.path.isdir(link):
                try:
                    os.rmdir(link)
                except OSError:
                    pass
            pbo = os.path.join(addons, prefix + ".pbo")
            if not self._needs_pack(target, pbo):
                self.log(f"[Mods] aktuell, kein Neupacken: {prefix}.pbo")
                continue
            try:
                count, size = self.build_pbo(target, pbo, prefix)
            except (OSError, ValueError) as e:
                self.log(f"[Mods] FEHLER beim Packen von {target}: {e}")
                return path
            self.log(f"[Mods] gepackt: {target} -> {pbo} "
                     f"({count} Dateien, {size / 1048576:.1f} MB, "
                     f"prefix={prefix})")
        self.update_cache_label()
        return os.path.join(self._stage_dir(), "@" + name)

    def _stage_size(self):
        total = 0
        stage = self._stage_dir()
        for dirpath, _, files in os.walk(stage):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        return total

    def update_cache_label(self):
        try:
            mb = self._stage_size() / 1048576
        except OSError:
            return
        self.b_cache.config(text=f"PBO-Cache leeren ({mb:.0f} MB)"
                            if mb >= 1 else "PBO-Cache leeren")

    def clear_pbo_cache(self, quiet=False):
        """Erzeugte PBOs im _filePatching-Ordner loeschen."""
        stage = self._stage_dir()
        if not os.path.isdir(stage):
            if not quiet:
                self.log("[Mods] Kein PBO-Cache vorhanden.")
            return
        if (self.server_proc and self.server_proc.poll() is None) or \
                (self.client_proc and self.client_proc.poll() is None):
            if not quiet:
                messagebox.showinfo("PBO-Cache",
                                    "Bitte zuerst Server und Client beenden.")
            return
        freed = self._stage_size()
        try:
            shutil.rmtree(stage)
        except OSError as e:
            self.log(f"[Mods] Cache konnte nicht geleert werden: {e}")
            return
        self.log(f"[Mods] PBO-Cache geleert ({freed / 1048576:.0f} MB frei).")
        self.update_cache_label()

    # -------------------------------------------------------- Ladereihenfolge
    def ordered_chosen(self):
        """Angehakte Mods in der aktuellen Ladereihenfolge."""
        chosen = [p for p, v in self.mod_vars.items() if v.get()]
        order = self.cfg.get("mod_order", [])
        idx = {p: i for i, p in enumerate(order)}
        base = {p: i for i, p in enumerate(self.mod_vars)}
        return sorted(chosen,
                      key=lambda p: (idx.get(p, len(idx) + base.get(p, 0)),))

    def open_load_order(self):
        chosen = self.ordered_chosen()
        if not chosen:
            messagebox.showinfo("Ladereihenfolge",
                                "Es ist kein Mod angehakt.")
            return
        win = tk.Toplevel(self)
        win.title("Ladereihenfolge – oben wird zuerst geladen")
        win.geometry("640x520")
        win.configure(bg=C_BG)
        self._dark_titlebar(win)

        ttk.Label(win, text="Frameworks (CF, Dabs) gehören nach oben, "
                            "Erweiterungen darunter.",
                  foreground=C_MUTED).pack(anchor="w", padx=10, pady=(8, 4))

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=10)
        lb = tk.Listbox(body, bg=C_FIELD, fg=C_FG, selectbackground=C_ACCENT,
                        selectforeground="#141414", relief="flat",
                        highlightthickness=1, highlightbackground=C_BORDER,
                        activestyle="none", font=("Segoe UI", 9))
        sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        paths = list(chosen)

        def refresh(sel=None):
            lb.delete(0, "end")
            for i, p in enumerate(paths, start=1):
                label = self.mod_labels.get(p, os.path.basename(p))
                lb.insert("end", f"{i:>2}.  {label}")
            if sel is not None:
                lb.selection_clear(0, "end")
                lb.selection_set(sel)
                lb.see(sel)

        def move(step):
            sel = lb.curselection()
            if not sel:
                return
            i = sel[0]
            j = i + step
            if 0 <= j < len(paths):
                paths[i], paths[j] = paths[j], paths[i]
                refresh(j)

        def auto():
            # Workshop zuerst, dann P:-Quellen, dann PBOs/Ordner
            rank = {"ws": 0, "p": 1, "pbo": 2, "dir": 3}
            paths.sort(key=lambda p: (
                rank.get(self.mod_source.get(p, "ws"), 9),
                self.mod_labels.get(p, "").lower()))
            refresh(0)

        side = ttk.Frame(win)
        side.pack(fill="x", padx=10, pady=8)
        ttk.Button(side, text="▲ nach oben",
                   command=lambda: move(-1)).pack(side="left", padx=3)
        ttk.Button(side, text="▼ nach unten",
                   command=lambda: move(1)).pack(side="left", padx=3)
        ttk.Button(side, text="Automatisch sortieren",
                   command=auto).pack(side="left", padx=12)

        def save():
            rest = [p for p in self.cfg.get("mod_order", []) if p not in paths]
            self.cfg["mod_order"] = paths + rest
            self.save_config()
            self.log("[Mods] Ladereihenfolge gespeichert: " + ", ".join(
                self.mod_labels.get(p, os.path.basename(p)) for p in paths))
            win.destroy()

        ttk.Button(side, text="Übernehmen", style="Accent.TButton",
                   command=save).pack(side="right", padx=3)
        refresh(0)

    def mod_param(self):
        # Reihenfolge = Reihenfolge in der Liste (Workshop/Frameworks zuerst)
        chosen = []
        wrap = getattr(self, "v_wrap", None)
        for path in self.ordered_chosen():
            if (wrap and wrap.get()
                    and getattr(self, "mod_source", {}).get(path) == "p"
                    and not os.path.isdir(os.path.join(path, "addons"))):
                path = self.wrap_source(path)
            chosen.append(path)
        if not chosen:
            return None
        return "-mod=" + ";".join(chosen)

    @staticmethod
    def _workshop_name(folder):
        """Mod-Namen aus meta.cpp eines Workshop-ID-Ordners lesen."""
        meta = os.path.join(folder, "meta.cpp")
        try:
            with open(meta, "r", encoding="utf-8", errors="replace") as f:
                m = re.search(r'name\s*=\s*"([^"]+)"', f.read())
                if m:
                    return m.group(1).strip()
        except OSError:
            pass
        return None

    # ---------------------------------------------------- mods manuell zufuegen
    def add_pbos(self):
        files = filedialog.askopenfilenames(
            title="PBO-Dateien auswaehlen",
            filetypes=[("PBO", "*.pbo"), ("Alle Dateien", "*.*")])
        if not files:
            return
        default = os.path.splitext(os.path.basename(files[0]))[0]
        name = simpledialog.askstring(
            "Mod-Name", "Name fuer den neuen Mod-Ordner:",
            initialvalue=default, parent=self)
        if not name:
            return
        name = name.strip()
        if not name.startswith("@"):
            name = "@" + name
        addons = os.path.join(APP_DIR, "LocalMods", name, "addons")
        os.makedirs(addons, exist_ok=True)
        copied = 0
        for f in files:
            try:
                shutil.copy2(f, addons)
                copied += 1
                # passende Signaturdateien gleich mitnehmen (schadet lokal nicht)
                base = os.path.splitext(f)[0]
                folder = os.path.dirname(f)
                for other in os.listdir(folder):
                    if other.lower().startswith(
                            os.path.basename(base).lower()) \
                            and other.lower().endswith(".bisign"):
                        shutil.copy2(os.path.join(folder, other), addons)
            except OSError as e:
                messagebox.showerror("Fehler beim Kopieren", str(e))
                return
        self.log(f"[Launcher] {copied} PBO(s) nach {name}\\addons kopiert.")
        self.save_config()
        self.scan_mods()
        if os.path.dirname(addons) in self.mod_vars:
            self.mod_vars[os.path.dirname(addons)].set(True)

    def add_mod_folder(self):
        p = filedialog.askdirectory(title="Mod-Ordner (@...) auswaehlen")
        if not p:
            return
        p = os.path.normpath(p)
        # Plausibilitaet: enthaelt der Ordner (direkt oder in addons/) PBOs?
        has_pbo = any(fn.lower().endswith(".pbo")
                      for dirpath, _, files in os.walk(p) for fn in files)
        if not has_pbo and not messagebox.askyesno(
                "Keine PBOs gefunden",
                "In diesem Ordner wurden keine .pbo-Dateien gefunden.\n"
                "Trotzdem hinzufuegen?"):
            return
        extras = self.cfg.setdefault("extra_mods", [])
        if p not in extras:
            extras.append(p)
        self.save_config()
        self.scan_mods()
        if p in self.mod_vars:
            self.mod_vars[p].set(True)
        self.log(f"[Launcher] Mod-Ordner hinzugefuegt: {p}")

    def remove_extra_mod(self, path):
        in_local = any(
            os.path.normpath(path).lower().startswith(
                os.path.normpath(os.path.join(d, "LocalMods")).lower())
            for d in (APP_DIR, SCRIPT_DIR))
        if in_local:
            if messagebox.askyesno(
                    "Mod entfernen",
                    f"{os.path.basename(path)} aus LocalMods loeschen?\n"
                    "(Die kopierten PBOs werden geloescht)"):
                try:
                    shutil.rmtree(path)
                except OSError as e:
                    messagebox.showerror("Fehler", str(e))
                    return
        else:
            if not messagebox.askyesno(
                    "Mod entfernen",
                    f"{os.path.basename(path)} aus der Liste entfernen?\n"
                    "(Der Ordner selbst bleibt unangetastet)"):
                return
            if path in self.cfg.get("extra_mods", []):
                self.cfg["extra_mods"].remove(path)
        self.save_config()
        self.scan_mods()
        self.log(f"[Launcher] Entfernt: {path}")

    # --------------------------------------------------------------- helpers
    SERVER_EXES = ("DayZDiag_x64.exe", "DayZServer_x64.exe")
    CLIENT_EXES = ("DayZDiag_x64.exe", "DayZ_x64.exe")

    def _find_exe(self, folder, names):
        for n in names:
            p = os.path.join(folder, n)
            if os.path.isfile(p):
                return p
        return None

    def server_exe(self):
        folder = self.e_dayz.get().strip()
        # 1. Diag im Server-Ordner
        exe = self._find_exe(folder, ("DayZDiag_x64.exe",))
        if exe:
            return exe
        # 2. Diag aus dem Client-Ordner – laeuft mit -server und ohne BattlEye
        client = self.e_client.get().strip()
        if client:
            exe = self._find_exe(client, ("DayZDiag_x64.exe",))
            if exe:
                self.log("[Launcher] Keine Diag-Exe im Server-Ordner – nutze "
                         f"die Diag aus dem Client-Ordner ({exe}). "
                         "Damit laeuft der Test ohne BattlEye.")
                return exe
        # 3. Retail-Server (BattlEye aktiv!)
        exe = self._find_exe(folder, ("DayZServer_x64.exe",))
        if exe:
            self.log("[Launcher] ACHTUNG: Server startet als Retail-Server – "
                     "BattlEye ist aktiv und kickt lokale Clients "
                     "(0x000400F0). Besser eine DayZDiag_x64.exe verwenden.")
            return exe
        messagebox.showerror(
            "Server-Exe fehlt",
            "Im DayZ-Ordner (Server) wurde weder DayZDiag_x64.exe noch\n"
            f"DayZServer_x64.exe gefunden:\n{folder}\n\n"
            "Bitte den Pfad pruefen (Server-Installation oder Client mit\n"
            "Diag-Exe).")
        return None

    def server_is_diag(self):
        """Laeuft der Server mit einer Diag-Exe?"""
        folder = self.e_dayz.get().strip()
        client = self.e_client.get().strip()
        if self._find_exe(folder, ("DayZDiag_x64.exe",)):
            return True
        return bool(client and self._find_exe(client, ("DayZDiag_x64.exe",)))

    def client_exe(self):
        folder = self.e_client.get().strip() or self.e_dayz.get().strip()
        names = self.CLIENT_EXES
        no_diag = bool(getattr(self, "v_no_diag", None)) and self.v_no_diag.get()
        # Client und Server muessen zusammenpassen (Fehler 0x00020017)
        if not no_diag and not self.server_is_diag() \
                and self._find_exe(folder, ("DayZ_x64.exe",)):
            no_diag = True
            self.log("[Launcher] Server laeuft ohne Diag – Client wird "
                     "ebenfalls ohne Diag gestartet (sonst Fehler 0x00020017).")
        elif no_diag and self.server_is_diag() \
                and self._find_exe(folder, ("DayZDiag_x64.exe",)):
            no_diag = False
            self.log("[Launcher] Server laeuft mit Diag – Client wird "
                     "ebenfalls mit Diag gestartet.")
        if no_diag:
            names = tuple(reversed(self.CLIENT_EXES))   # DayZ_x64.exe zuerst
        exe = self._find_exe(folder, names)
        if exe:
            return exe
        messagebox.showerror(
            "Client-Exe fehlt",
            "Im Client-Ordner wurde weder DayZDiag_x64.exe noch\n"
            f"DayZ_x64.exe gefunden:\n{folder}\n\n"
            "Tipp: Fuer den Client den Spiel-Ordner eintragen (Feld\n"
            "'DayZ-Ordner (Client, optional)'). Die Diag-Exe kommt ueber\n"
            "Steam > DayZ > Eigenschaften > Betas.")
        return None

    def create_server_cfg(self):
        cur = self.e_servercfg.get().strip()
        start_dir = os.path.dirname(cur) if cur else self.e_dayz.get().strip()
        start_name = os.path.basename(cur) if cur else "serverDZ_test.cfg"
        path = filedialog.asksaveasfilename(
            defaultextension=".cfg", initialdir=start_dir,
            initialfile=start_name,
            filetypes=[("cfg", "*.cfg")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_SERVER_CFG)
        self.e_servercfg.delete(0, "end")
        self.e_servercfg.insert(0, os.path.normpath(path))
        self.log(f"[Launcher] Vorlage erstellt: {path}")
        messagebox.showinfo("Erstellt",
                            "serverDZ_test.cfg wurde erstellt.\n"
                            "verifySignatures=0 und allowFilePatching=1 sind gesetzt.")

    @staticmethod
    def classify(line):
        low = line.lower()
        if any(p in low for p in ERR_PATTERNS):
            return "err"
        if any(p in low for p in WARN_PATTERNS):
            return "warn"
        return None

    def _insert_line(self, line, tag):
        """Zeile ins Log-Widget einfuegen und Dateiverweise klickbar machen."""
        self.txt.insert("end", line + "\n", tag or ())
        m = FILE_LINE_RE.search(line)
        if not m:
            return
        path = m.group(1)
        lineno = int(m.group(2) or m.group(3) or 1)
        row = int(self.txt.index("end-2c").split(".")[0])
        self.link_count += 1
        lt = f"link{self.link_count}"
        self.txt.tag_add(lt, f"{row}.{m.start()}", f"{row}.{m.end()}")
        self.txt.tag_configure(lt, underline=True)
        self.txt.tag_bind(lt, "<Button-1>",
                          lambda e, p=path, l=lineno: self.open_source_popup(p, l))
        self.txt.tag_bind(lt, "<Enter>",
                          lambda e: self.txt.configure(cursor="hand2"))
        self.txt.tag_bind(lt, "<Leave>",
                          lambda e: self.txt.configure(cursor=""))

    def log(self, line):
        line = line.rstrip()
        tag = self.classify(line)
        self.log_lines.append((line, tag))
        if len(self.log_lines) > 20000:            # Speicher begrenzen
            del self.log_lines[:5000]
        if self.v_only_err.get() and tag != "err":
            return
        self._insert_line(line, tag)
        self.txt.see("end")

    def re_render_log(self):
        self.txt.delete("1.0", "end")
        for line, tag in self.log_lines:
            if self.v_only_err.get() and tag != "err":
                continue
            self._insert_line(line, tag)
        self.txt.see("end")

    def clear_log(self):
        self.log_lines.clear()
        self.txt.delete("1.0", "end")

    # ---------------------------------------------------------- source popup
    def resolve_path(self, raw):
        """Pfad aus der Fehlerzeile in einen echten Dateipfad aufloesen."""
        raw = raw.replace("/", "\\").strip()
        if os.path.isfile(raw):
            return raw
        pd = self.e_pdrive.get().strip() or "P:\\"
        cand = os.path.join(pd, raw.lstrip("\\"))
        if os.path.isfile(cand):
            return cand
        # letzter Versuch: Dateiname in den angehakten P:-Mods suchen
        fname = os.path.basename(raw).lower()
        pdl = os.path.normpath(pd).lower()
        for root_dir, v in self.mod_vars.items():
            if not v.get() or not os.path.normpath(root_dir).lower().startswith(pdl):
                continue
            for dirpath, _, files in os.walk(root_dir):
                for fn in files:
                    if fn.lower() == fname:
                        return os.path.join(dirpath, fn)
        return None

    def open_source_popup(self, path, lineno):
        real = self.resolve_path(path)
        if not real:
            messagebox.showwarning("Datei nicht gefunden",
                                   f"Konnte die Datei nicht aufloesen:\n{path}")
            return
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            messagebox.showerror("Fehler", str(e))
            return

        win = tk.Toplevel(self)
        win.title(f"{real}   —   Zeile {lineno}")
        win.geometry("980x620")
        win.configure(bg=C_BG)
        self._dark_titlebar(win)

        bar = ttk.Frame(win)
        bar.pack(fill="x")
        ttk.Label(bar, text=os.path.basename(real),
                  font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=4)
        ttk.Button(bar, text="In VS Code öffnen",
                   command=lambda: self.open_in_vscode(real, lineno)).pack(side="right", padx=6)
        ttk.Button(bar, text="Standard-Editor",
                   command=lambda: self.open_in_default(real)).pack(side="right")

        txt = tk.Text(win, wrap="none", bg="#101114", fg="#cfd2d6",
                      relief="flat", highlightthickness=1,
                      highlightbackground="#3a3d44", font=("Consolas", 10))
        ysb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        xsb = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)

        txt.tag_configure("hit", background="#665500", foreground="#ffffff")
        txt.tag_configure("lno", foreground="#777777")
        width = len(str(len(lines)))
        for i, l in enumerate(lines, start=1):
            txt.insert("end", f"{i:>{width}} | ", "lno")
            txt.insert("end", l.rstrip("\n") + "\n",
                       "hit" if i == lineno else ())
        txt.configure(state="disabled")
        txt.see(f"{max(1, lineno - 8)}.0")          # Zielzeile mit Kontext oben
        txt.see(f"{lineno}.0")

    def open_in_vscode(self, path, lineno):
        try:
            subprocess.Popen(f'code -g "{path}:{lineno}"', shell=True)
        except OSError:
            messagebox.showinfo("VS Code",
                                "VS Code ('code') wurde nicht gefunden.")

    def open_in_default(self, path):
        try:
            os.startfile(path)                      # Windows
        except (AttributeError, OSError):
            messagebox.showinfo("Editor", "Konnte Standard-Editor nicht starten.")

    def open_profiles(self):
        profiles = self.e_profiles.get().strip()
        if not profiles:
            profiles = os.path.join(DEFAULT_BASE, "TestProfiles")
            self.e_profiles.insert(0, profiles)
        os.makedirs(profiles, exist_ok=True)
        self.open_in_default(profiles)

    # ------------------------------------------------------ quick settings
    def _quick_report(self, quiet, ok, text):
        if quiet or ok:
            self.log("[Quick] " + text)
        else:
            messagebox.showerror("Schnell-Einstellung", text)

    def set_login_timer(self, quiet=False):
        val = self.e_login.get().strip()
        if not val.isdigit():
            self._quick_report(quiet, False,
                               "Login-Timer: bitte eine Zahl in Sekunden eingeben.")
            return False
        mission = self.e_mission.get().strip()
        path = os.path.join(mission, "db", "globals.xml")
        if not os.path.isfile(path):
            self._quick_report(quiet, False,
                               f"globals.xml nicht gefunden: {path}")
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            new_content, n = re.subn(
                r'(<var\s+name="TimeLogin"[^>]*\bvalue=")\d+(")',
                r'\g<1>' + val + r'\g<2>', content, count=1)
            if n == 0:
                self._quick_report(quiet, False,
                                   "Eintrag TimeLogin in der globals.xml "
                                   "nicht gefunden.")
                return False
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except OSError as e:
            self._quick_report(quiet, False, f"Login-Timer: {e}")
            return False
        self.save_config()
        self.log(f"[Quick] TimeLogin = {val} gesetzt ({path}) – "
                 "wirkt ab dem naechsten Serverstart.")
        return True

    def set_server_time(self, quiet=False):
        raw = self.e_hour.get().strip()
        cfgfile = self.e_servercfg.get().strip()
        if not os.path.isfile(cfgfile):
            self._quick_report(quiet, False,
                               f"Serverzeit: serverDZ.cfg nicht gefunden ({cfgfile}).")
            return False
        if not raw or raw.lower() in ("sys", "system", "systemtime"):
            new_val = "SystemTime"
        else:
            m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", raw)
            if not m or not (0 <= int(m.group(1)) <= 23) \
                    or (m.group(2) and not (0 <= int(m.group(2)) <= 59)):
                self._quick_report(quiet, False,
                                   "Serverzeit: Stunde (0-23) oder "
                                   "Stunde:Minute eingeben, leer = Systemzeit.")
                return False
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            new_val = f"2026/05/01/{hour}/{minute:02d}"
        try:
            with open(cfgfile, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            new_content, n = re.subn(
                r'serverTime\s*=\s*"[^"]*"\s*;',
                f'serverTime = "{new_val}";', content, count=1)
            if n == 0:      # Zeile fehlt komplett -> anhaengen
                new_content = content.rstrip() + \
                    f'\nserverTime = "{new_val}";\n'
            with open(cfgfile, "w", encoding="utf-8") as f:
                f.write(new_content)
        except OSError as e:
            self._quick_report(quiet, False, f"Serverzeit: {e}")
            return False
        self.save_config()
        self.log(f'[Quick] serverTime = "{new_val}" gesetzt ({cfgfile}) – '
                 "wirkt ab dem naechsten Serverstart.")
        return True

    # ------------------------------------------------- benoetigte Workshop-Mods
    def required_mods(self):
        sets = self.cfg.get("profiles_sets", {})
        data = sets.get(self.cfg.get("active_profile", ""), {})
        return list(data.get("required_mods", []))

    def _workshop_root(self):
        ws = self.e_workshop.get().strip()
        if not ws:
            ws = os.path.join(self.e_dayz.get().strip(), "!Workshop")
        return ws

    def _mod_present(self, entry):
        """Liegt die Mod (ID oder @Name) im Workshop-Ordner?"""
        ws = self._workshop_root()
        wid = entry.get("id", "").strip()
        name = entry.get("name", "").strip()
        if wid and os.path.isdir(os.path.join(ws, wid)):
            return True
        for cand in (name, "@" + name.lstrip("@")):
            if cand.strip("@") and os.path.isdir(os.path.join(ws, cand)):
                return True
        # Steam-ID-Ordner mit passender meta.cpp
        if name and os.path.isdir(ws):
            try:
                for d in os.listdir(ws):
                    full = os.path.join(ws, d)
                    if os.path.isdir(full) and \
                            (self._workshop_name(full) or "").lower() == name.lower():
                        return True
            except OSError:
                pass
        return False

    def activate_present_required(self):
        """Vorhandene Pflicht-Mods automatisch anhaken."""
        for m in self.required_mods():
            if self._mod_present(m):
                self.activate_mod_entry(m)

    def check_required_mods(self, silent=False):
        """Fehlende Pflicht-Mods melden und Abo anbieten."""
        self.activate_present_required()
        missing = [m for m in self.required_mods() if not self._mod_present(m)]
        if not missing:
            return True
        names = "\n".join(
            "  - %s%s" % (m.get("name") or "Workshop-Mod",
                          "  (ID %s)" % m["id"] if m.get("id") else "")
            for m in missing)
        if silent:
            self.log("[Mods] %d benoetigte Workshop-Mod(s) fehlen." % len(missing))
            return False
        if not messagebox.askyesno(
                "Benoetigte Mods",
                "Fuer dieses Profil fehlen folgende Workshop-Mods:\n\n%s\n\nJetzt in Steam zum Abonnieren oeffnen?" % names):
            return False
        import webbrowser
        for m in missing:
            wid = m.get("id", "").strip()
            if not wid:
                continue
            webbrowser.open("steam://url/CommunityFilePage/" + wid)
            self.log("[Mods] Steam-Seite geoeffnet: %s (ID %s)" % (m.get("name") or wid, wid))
        self.log("[Mods] In Steam auf 'Abonnieren' klicken – der Download wird hier verfolgt.")
        threading.Thread(target=self._watch_downloads,
                         args=(missing, self._workshop_root()),
                         daemon=True).start()
        return False

    def ensure_required_active(self):
        """Vor dem Start: Pflicht-Mods muessen vorhanden UND angehakt sein."""
        req = self.required_mods()
        if not req:
            return True
        missing, inactive = [], []
        for m in req:
            if not self._mod_present(m):
                missing.append(m)
                continue
            wid = m.get("id", "").strip()
            name = m.get("name", "").strip().lstrip("@").lower()
            active = False
            for p, var in self.mod_vars.items():
                base = os.path.basename(os.path.normpath(p))
                label = getattr(self, "mod_labels", {}).get(p, "")
                if (wid and base == wid) or (
                        name and (label.strip().lower() == name
                                  or base.lstrip("@").lower() == name)):
                    active = var.get()
                    break
            if not active:
                inactive.append(m)
        if missing:
            names = "\n".join("  - " + (m.get("name") or m.get("id", ""))
                               for m in missing)
            messagebox.showerror("Pflicht-Mods fehlen", "Fuer diese Map werden Mods benoetigt, die nicht installiert sind:\n\n%s\n\nDer Server wird ohne sie nicht starten." % names)
            self.check_required_mods()
            return False
        if inactive:
            names = "\n".join("  - " + (m.get("name") or m.get("id", ""))
                               for m in inactive)
            if messagebox.askyesno("Pflicht-Mods fehlen", "Folgende Pflicht-Mods sind installiert, aber nicht aktiviert:\n\n%s\n\nJetzt aktivieren und starten?" % names):
                for m in inactive:
                    self.activate_mod_entry(m)
                return True
            return False
        return True

    def activate_mod_entry(self, entry):
        """Heruntergeladene Pflicht-Mod in der Liste anhaken."""
        wid = entry.get("id", "").strip()
        name = entry.get("name", "").strip().lstrip("@").lower()
        for p, var in self.mod_vars.items():
            base = os.path.basename(os.path.normpath(p))
            label = getattr(self, "mod_labels", {}).get(p, "")
            hit = (wid and base == wid) or (
                name and (label.strip().lower() == name
                          or base.lstrip("@").lower() == name))
            if hit:
                if not var.get():
                    var.set(True)
                    self.log("[Mods] automatisch aktiviert: " + (label or base))
                    self.save_config()
                return True
        return False

    def _finish_download(self, entry):
        self.scan_mods()
        if not self.activate_mod_entry(entry):
            self.log("[Mods] noch nicht in der Liste gefunden (Neu scannen): " + (entry.get("name") or entry.get("id", "")))

    def _watch_downloads(self, missing, ws_root):
        """Workshop-Ordner beobachten und Fortschritt ins Log schreiben."""
        pending = {m.get("id") or m.get("name"): m for m in missing}
        sizes = {}
        deadline = time.time() + 1800          # 30 Minuten
        while pending and time.time() < deadline:
            time.sleep(3)
            for key, m in list(pending.items()):
                wid = m.get("id", "").strip()
                folder = os.path.join(ws_root, wid) if wid else None
                if folder and os.path.isdir(folder):
                    size = 0
                    for dirpath, _, files in os.walk(folder):
                        for fn in files:
                            try:
                                size += os.path.getsize(os.path.join(dirpath, fn))
                            except OSError:
                                pass
                    mb = size / 1048576
                    if sizes.get(key) == size and size > 0:
                        self.after(0, self.log, "[Mods] Download fertig: %s (%.0f MB)" % (
                            m.get("name") or wid, mb))
                        self.after(0, self._finish_download, m)
                        pending.pop(key, None)
                        continue
                    if size and abs(size - sizes.get(key, 0)) > 1048576:
                        self.after(0, self.log, "[Mods] laedt: %s (%.0f MB)" % (
                            m.get("name") or wid, mb))
                    sizes[key] = size
        if pending:
            self.after(0, self.log, "[Mods] Beobachtung beendet – nicht alle Mods sind angekommen.")

    def open_required_mods(self):
        win = tk.Toplevel(self)
        win.title("Benoetigte Mods")
        win.geometry("640x460")
        win.configure(bg=C_BG)
        self._dark_titlebar(win)
        ttk.Label(win, text="Pflicht-Mods dieses Profils. Fehlende Mods koennen direkt\nabonniert werden; der Download wird im Log verfolgt.", foreground=C_MUTED,
                  justify="left").pack(anchor="w", padx=10, pady=(8, 4))

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=10)

        def store(entries):
            sets = self.cfg.setdefault("profiles_sets", {})
            data = sets.setdefault(self.cfg.get("active_profile", ""), {})
            data["required_mods"] = entries
            self.save_config()

        def refresh():
            for w in body.winfo_children():
                w.destroy()
            self.activate_present_required()
            entries = self.required_mods()
            if not entries:
                ttk.Label(body, text="Noch keine Pflicht-Mods eingetragen.",
                          foreground=C_MUTED).pack(anchor="w", pady=6)
            for i, m in enumerate(entries):
                row = ttk.Frame(body)
                row.pack(fill="x", pady=2)
                present = self._mod_present(m)
                ttk.Label(row, text="vorhanden" if present else "fehlt",
                          foreground=("#8bc48b" if present else "#f28b82"),
                          width=12).pack(side="left")
                label = m.get("name") or m.get("id", "")
                wid = m.get("id", "")
                lab = ttk.Label(row, text=label, width=28,
                                cursor="hand2" if wid else "",
                                foreground=C_ACCENT if wid else C_FG)
                lab.pack(side="left")
                if wid:
                    lab.bind("<Button-1>",
                             lambda e, w=wid: self.open_workshop_page(w))
                    lab.bind("<Enter>", lambda e, w=lab:
                             w.configure(foreground=C_ACCENT2))
                    lab.bind("<Leave>", lambda e, w=lab:
                             w.configure(foreground=C_ACCENT))
                ttk.Label(row, text=wid, foreground=C_MUTED,
                          width=12).pack(side="left")
                ttk.Button(row, text="X", width=4,
                           command=lambda idx=i: (
                               store([e for j, e in
                                      enumerate(self.required_mods())
                                      if j != idx]), refresh())
                           ).pack(side="right", padx=2)
                if not present and wid:
                    ttk.Button(row, text="Herunterladen", style="Accent.TButton",
                               command=lambda e=m: self._subscribe_one(e, refresh)
                               ).pack(side="right", padx=2)
                elif not present:
                    ttk.Label(row, text="keine Workshop-ID",
                              foreground=C_MUTED).pack(side="right", padx=2)

        add = ttk.Frame(win)
        add.pack(fill="x", padx=10, pady=(6, 2))
        ttk.Label(add, text="Workshop-ID:").pack(side="left")
        e_id = ttk.Entry(add, width=14)
        e_id.pack(side="left", padx=4)
        ttk.Label(add, text="Name:").pack(side="left")
        e_name = ttk.Entry(add, width=24)
        e_name.pack(side="left", padx=4)

        def add_entry():
            wid = e_id.get().strip()
            nm = e_name.get().strip()
            if not wid and not nm:
                return
            if wid and not wid.isdigit():
                messagebox.showinfo("Benoetigte Mods", "Die Workshop-ID darf nur Ziffern enthalten.", parent=win)
                return
            entries = self.required_mods() + [{"id": wid, "name": nm}]
            store(entries)
            e_id.delete(0, "end")
            e_name.delete(0, "end")
            refresh()

        ttk.Button(add, text="Hinzufuegen", command=add_entry).pack(side="left", padx=4)
        ttk.Button(win, text="Schliessen", style="Accent.TButton",
                   command=win.destroy).pack(pady=8)
        refresh()

    def open_workshop_page(self, wid):
        """Workshop-Seite einer Mod im Browser/Steam oeffnen."""
        import webbrowser
        url = "https://steamcommunity.com/sharedfiles/filedetails/?id=" + wid
        webbrowser.open(url)
        self.log("[Mods] Workshop-Seite geoeffnet: " + url)

    def _subscribe_one(self, entry, refresh=None):
        """Workshop-Seite oeffnen und Download verfolgen."""
        import webbrowser
        wid = entry.get("id", "").strip()
        if not wid:
            return
        webbrowser.open("https://steamcommunity.com/sharedfiles/"
                        "filedetails/?id=" + wid)
        self.log("[Mods] Workshop-Seite geoeffnet: %s (ID %s)" % (entry.get("name") or wid, wid))
        threading.Thread(target=self._watch_downloads,
                         args=([entry], self._workshop_root()),
                         daemon=True).start()
        if refresh:
            self.after(4000, refresh)

    # ------------------------------------------------------------ Map wechseln
    CE_ZIP = ("https://github.com/BohemiaInteractive/"
              "DayZ-Central-Economy/archive/refs/heads/master.zip")
    NAMALSK_ZIP = ("https://github.com/SumrakDZN/Namalsk-Server/"
                   "archive/refs/heads/main.zip")

    # Missionsordner -> (Archiv-URL, Pfad im Archiv, Hinweis)
    MAP_SOURCES = {
        "dayzOffline.chernarusplus": (CE_ZIP, "dayzOffline.chernarusplus", ""),
        "dayzOffline.enoch": (CE_ZIP, "dayzOffline.enoch", ""),
        "dayzOffline.sakhal": (CE_ZIP, "dayzOffline.sakhal", "Sakhal benoetigt das Frostline-DLC."),
        "regular.namalsk": (NAMALSK_ZIP, "Mission Files/regular.namalsk",
                            "Namalsk benoetigt zusaetzlich die Namalsk-Mods aus dem Workshop - unter 'Benoetigte Mods' eintragen."),
    }
    GITHUB_MISSIONS = tuple(MAP_SOURCES)

    MAP_NAMES = {"chernarusplus": "Chernarus", "enoch": "Livonia",
                 "sakhal": "Sakhal", "namalsk": "Namalsk",
                 "deerisle": "DeerIsle", "banov": "Banov",
                 "esseker": "Esseker", "livonia": "Livonia"}

    def _pretty_map(self, folder_name):
        key = folder_name.lower().split(".")[-1]
        return self.MAP_NAMES.get(key, folder_name.split(".")[-1].title())

    @staticmethod
    def cfg_name_for(folder):
        """Dateiname der serverDZ-Config zur Mission."""
        key = folder.lower()
        if "chernarusplus" in key:
            return "serverDZ_test.cfg"
        short = folder.split(".")[-1]
        return "serverDZ_%s_test.cfg" % short.capitalize()

    def create_cfg_if_missing(self, cfgfile, mission_name):
        """Fehlende Profil-Config auf Wunsch aus der Vorlage anlegen."""
        if os.path.isfile(cfgfile):
            return True
        if not messagebox.askyesno("serverDZ.cfg anlegen", "Fuer dieses Profil gibt es noch keine Server-Config:\n%s\n\nJetzt aus der Vorlage anlegen?" % cfgfile):
            return False
        text = DEFAULT_SERVER_CFG.replace('"dayzOffline.chernarusplus"',
                                          '"%s"' % mission_name)
        prof = self.cfg.get("active_profile", "")
        text = text.replace('hostname = "ZMG Local Test Server";',
                            'hostname = "ZMG Test %s";' % prof)
        try:
            os.makedirs(os.path.dirname(cfgfile), exist_ok=True)
            with open(cfgfile, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            messagebox.showerror("serverDZ.cfg anlegen", str(e))
            return False
        self.log("[Launcher] Server-Config angelegt: %s" % cfgfile)
        return True

    def ensure_profile_cfg(self):
        """Beim Profilwechsel: fehlende Server-Config anbieten."""
        cfgfile = self.e_servercfg.get().strip()
        mission = self.e_mission.get().strip()
        if not cfgfile or not mission or os.path.isfile(cfgfile):
            return
        self.create_cfg_if_missing(
            cfgfile, os.path.basename(os.path.normpath(mission)))

    def sync_cfg_template(self):
        """template der serverDZ.cfg an den Mission-Ordner angleichen."""
        mission = self.e_mission.get().strip()
        if mission:
            self.set_cfg_template(os.path.basename(os.path.normpath(mission)))

    def set_cfg_template(self, mission_name):
        """template-Zeile der serverDZ.cfg auf die Mission setzen."""
        cfgfile = self.e_servercfg.get().strip()
        if not os.path.isfile(cfgfile):
            return
        try:
            with open(cfgfile, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            cur = re.search(r'template\s*=\s*"([^"]*)"', text)
            if cur and cur.group(1) == mission_name:
                return                      # passt bereits
            new, n = re.subn(r'(template\s*=\s*")[^"]*(")',
                             r"\g<1>" + mission_name + r"\g<2>", text, count=1)
            if n == 0:
                return
            with open(cfgfile, "w", encoding="utf-8") as f:
                f.write(new)
        except OSError as e:
            self.log("[Map] %s" % e)
            return
        self.log("[Map] serverDZ.cfg template = %s gesetzt." % mission_name)

    # ------------------------------------------------------- mission download
    MISSION_ZIP_URL = ("https://github.com/BohemiaInteractive/"
                       "DayZ-Central-Economy/archive/refs/heads/master.zip")

    def copy_local_mission(self, src_dir, dest):
        """Mission aus der Server-Installation kopieren (ohne Persistenz)."""
        if os.path.isdir(dest) and os.listdir(dest):
            if not messagebox.askyesno(
                    "Mission kopieren",
                    f"Der Zielordner ist nicht leer:\n{dest}\n\n"
                    "Inhalt ueberschreiben?"):
                return
        if (self.server_proc and self.server_proc.poll() is None) or \
                (self.client_proc and self.client_proc.poll() is None):
            messagebox.showinfo("Mission kopieren",
                                "Bitte zuerst Server und Client beenden.")
            return
        skipped = []
        count = 0
        try:
            os.makedirs(dest, exist_ok=True)
            for dirpath, dirs, files in os.walk(src_dir):
                # Persistenz und Logs der Serverinstallation nicht mitnehmen
                dirs[:] = [d for d in dirs
                           if not d.lower().startswith("storage_")]
                rel = os.path.relpath(dirpath, src_dir)
                target = dest if rel == "." else os.path.join(dest, rel)
                os.makedirs(target, exist_ok=True)
                for fn in files:
                    if fn.lower().endswith((".log", ".rpt", ".adm", ".mdmp")):
                        continue
                    try:
                        shutil.copy2(os.path.join(dirpath, fn),
                                     os.path.join(target, fn))
                        count += 1
                    except OSError as e:
                        # gesperrte Datei (WinError 32) ueberspringen
                        skipped.append(f"{os.path.join(rel, fn)}  ({e.strerror})")
        except OSError as e:
            messagebox.showerror("Mission kopieren", str(e))
            return
        if skipped:
            for s in skipped:
                self.log(f"[Mission] uebersprungen: {s}")
            messagebox.showwarning(
                "Mission kopieren",
                f"{count} Dateien kopiert, {len(skipped)} gesperrt und "
                "uebersprungen.\n\n"
                "Gesperrte Dateien kommen meist von einem laufenden Server "
                "(auch aus einer Start-Batch). Beende ihn und klicke erneut "
                "auf 'Erstellen'.\n\nDetails stehen im Log.")
        self.e_mission.delete(0, "end")
        self.e_mission.insert(0, dest)
        self.save_config()
        self.log(f"[Mission] {count} Dateien kopiert: {src_dir} -> {dest}")
        self.set_login_timer(quiet=True)
        self.set_server_time(quiet=True)
        self.update_map_label()

    def download_missions(self):
        mission = self.e_mission.get().strip()
        if not mission:
            messagebox.showinfo("Mission laden", "Bitte zuerst im Feld 'Mission-Ordner' den Zielpfad angeben.")
            return
        mission = os.path.normpath(mission)
        # Missionsordner aus dem Profil ableiten (z. B. regular.namalsk)
        base = os.path.basename(mission)
        if base in self.MAP_SOURCES or "." in base:
            folder = base
            parent = os.path.dirname(mission)
        else:
            folder = "dayzOffline.chernarusplus"
            parent = mission
            mission = os.path.join(parent, folder)

        # Liegt die Mission in der Server-Installation? Dann kopieren –
        # sie passt garantiert zum Build (wichtig bei Experimental).
        local = os.path.join(self.e_dayz.get().strip(), "mpmissions", folder)
        if os.path.isfile(os.path.join(local, "init.c")):
            if messagebox.askyesno(
                    "Mission kopieren",
                    "Die Mission liegt in der Server-Installation:\n%s\n\nSie passt exakt zu diesem Build. Kopieren nach:\n%s\n\n(Ja = kopieren, Nein = stattdessen von GitHub laden)" % (local, mission)):
                self.copy_local_mission(local, mission)
                return

        url, subpath, hint = self.MAP_SOURCES.get(folder, (None, None, ""))
        if url is None:
            messagebox.showinfo(
                "Mission laden",
                "Fuer '%s' ist keine Download-Quelle hinterlegt.\n\nBitte die Mission manuell in den Ordner legen oder ueber 'Maps' eine andere Karte waehlen." % folder)
            return
        if not messagebox.askyesno("Mission laden", "%s von GitHub laden und entpacken nach:\n\n%s\n\n(laeuft im Hintergrund, Fortschritt unten im Log)" % (folder, mission)):
            return
        if hint:
            self.log("[Map] " + hint)
        self.log("[Download] Starte Download: %s ..." % folder)
        threading.Thread(target=self._download_missions_worker,
                         args=(parent, mission, folder, url, subpath),
                         daemon=True).start()

    def _urlopen(self, url):
        """Download-Verbindung mit SSL-Fallbacks oeffnen.

        1. Systemzertifikate  2. certifi (falls installiert)
        3. ohne Zertifikatspruefung (mit Hinweis im Log)
        """
        import ssl
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "ZMG-Launcher"})
        try:
            return urllib.request.urlopen(req)
        except urllib.error.URLError as e:
            if not isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise                       # anderes Problem -> normal melden
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            return urllib.request.urlopen(req, context=ctx)
        except Exception:
            pass
        self.after(0, self.log,
                   "[Download] Hinweis: Keine gueltigen SSL-Zertifikate im "
                   "System gefunden – lade diesen Download ohne "
                   "Zertifikatspruefung.")
        ctx = ssl._create_unverified_context()
        return urllib.request.urlopen(req, context=ctx)

    def _download_missions_worker(self, target, mission,
                                  folder="dayzOffline.chernarusplus",
                                  url=None, subpath=None):
        import tempfile
        import zipfile

        tmp = os.path.join(tempfile.gettempdir(), "dayz_ce_master.zip")
        try:
            with self._urlopen(url or self.MISSION_ZIP_URL) as r, \
                    open(tmp, "wb") as out:
                read = 0
                last = 0
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    read += len(chunk)
                    if read - last >= 25 * 1024 * 1024:     # alle 25 MB melden
                        last = read
                        self.after(0, self.log,
                                   f"[Download] {read / 1048576:.0f} MB ...")
        except Exception as e:                              # Netz/HTTP/SSL etc.
            self.after(0, self.log, f"[Download] FEHLER beim Laden: {e}")
            return

        self.after(0, self.log,
                   f"[Download] {read / 1048576:.0f} MB geladen, entpacke "
                   + folder + " ...")
        prefix = (subpath or folder).rstrip("/") + "/"
        count = 0
        try:
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    parts = info.filename.split("/", 1)     # Repo-Prefix weg
                    if len(parts) < 2 or not parts[1]:
                        continue
                    rel = parts[1]
                    if not rel.startswith(prefix):
                        continue
                    inner = rel[len(prefix):]               # Pfad in der Mission
                    dest = os.path.join(mission, *inner.split("/")) \
                        if inner else mission
                    if info.is_dir():
                        os.makedirs(dest, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(info) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1
        except Exception as e:
            self.after(0, self.log, f"[Download] FEHLER beim Entpacken: {e}")
            return
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        def done():
            self.log(f"[Download] Fertig – {count} Dateien entpackt.")
            self.log(f"[Download] Mission: {mission}")
            self.e_mission.delete(0, "end")
            self.e_mission.insert(0, mission)
            self.save_config()
            # Login-Timer und Serverzeit direkt auf die frische Mission anwenden
            self.set_login_timer(quiet=True)
            self.set_server_time(quiet=True)
        self.after(0, done)

    # ------------------------------------------------------------ hot reload
    def open_hotreload(self):
        profiles = self.e_profiles.get().strip()
        if not profiles or not os.path.isdir(profiles):
            messagebox.showinfo("Hot-Reload",
                                "Bitte zuerst einen gueltigen Profiles-Ordner setzen.")
            return

        # alle JSONs unterhalb des Profiles-Ordners einsammeln
        found = []
        for dirpath, dirs, files in os.walk(profiles):
            if os.path.basename(dirpath).lower() == "zmg_hotreload":
                continue
            for fn in sorted(files, key=str.lower):
                if fn.lower().endswith(".json"):
                    rel = os.path.relpath(os.path.join(dirpath, fn), profiles)
                    found.append(rel.replace("\\", "/"))
        if not found:
            messagebox.showinfo("Hot-Reload",
                                "Keine JSON-Dateien im Profiles-Ordner gefunden.")
            return

        win = tk.Toplevel(self)
        win.title("JSON Hot-Reload – Dateien auswaehlen")
        win.geometry("560x520")
        win.configure(bg=C_BG)
        self._dark_titlebar(win)
        ttk.Label(win, text=f"JSONs in: {profiles}",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=8, pady=4)

        # scrollbare Checkbox-Liste, nach Unterordner gruppiert
        canvas = tk.Canvas(win, highlightthickness=0, bg=C_BG)
        ysb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True, padx=8)

        sel_vars = {}
        last_folder = None
        for rel in found:
            folder = os.path.dirname(rel) or "(Profiles-Wurzel)"
            if folder != last_folder:
                ttk.Label(inner, text=folder,
                          font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 1))
                last_folder = folder
            v = tk.BooleanVar(value=False)
            sel_vars[rel] = v
            rowf = ttk.Frame(inner)
            rowf.pack(anchor="w", fill="x")
            ttk.Checkbutton(rowf, variable=v).pack(side="left", padx=(12, 0))
            lnk = ttk.Label(rowf, text=os.path.basename(rel),
                            foreground=C_ACCENT, cursor="hand2")
            lnk.pack(side="left")
            full_json = os.path.join(profiles, *rel.split("/"))
            lnk.bind("<Button-1>",
                     lambda e, fp=full_json: self.open_json_editor(fp))
            lnk.bind("<Enter>", lambda e, w=lnk: w.configure(foreground=C_ACCENT2))
            lnk.bind("<Leave>", lambda e, w=lnk: w.configure(foreground=C_ACCENT))

        bar = ttk.Frame(win)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Ausgewaehlte neu laden",
                   command=lambda: self.request_hotreload(
                       profiles, [r for r, v in sel_vars.items() if v.get()], win)
                   ).pack(side="left", padx=8)
        ttk.Label(bar, text="Benoetigt die ZMG_HotReload-Mod auf dem Server!"
                  ).pack(side="left", padx=6)

    # ----------------------------------------------------------- JSON-Editor
    # Farben angelehnt an VS Code "Dark+"
    JS_BG = "#1e1e1e"
    JS_FG = "#d4d4d4"
    JS_KEY = "#9cdcfe"
    JS_STR = "#ce9178"
    JS_NUM = "#b5cea8"
    JS_KW = "#569cd6"
    JS_GUT = "#858585"

    JSON_TOKEN_RE = re.compile(
        r'"(?:\\.|[^"\\])*"\s*:'          # Schluessel (mit Doppelpunkt)
        r'|"(?:\\.|[^"\\])*"'              # String
        r'|\b(?:true|false|null)\b'          # Schluesselwoerter
        r'|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'  # Zahlen
    )

    def open_json_editor(self, path):
        if not os.path.isfile(path):
            messagebox.showerror("JSON-Editor", f"Datei nicht gefunden:\n{path}")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            messagebox.showerror("JSON-Editor", str(e))
            return

        win = tk.Toplevel(self)
        win.title(os.path.basename(path) + "  –  " + path)
        win.geometry("900x640")
        win.configure(bg=C_BG)
        self._dark_titlebar(win)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=6, pady=4)
        status = ttk.Label(bar, text=path, foreground=C_MUTED)
        status.pack(side="right", padx=6)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        gutter = tk.Text(body, width=5, padx=4, takefocus=0, border=0,
                         bg="#252526", fg=self.JS_GUT, state="disabled",
                         font=("Consolas", 10), wrap="none")
        gutter.pack(side="left", fill="y")
        ysb = ttk.Scrollbar(body, orient="vertical")
        ysb.pack(side="right", fill="y")
        txt = tk.Text(body, wrap="none", undo=True, bg=self.JS_BG,
                      fg=self.JS_FG, insertbackground="#aeafad",
                      selectbackground="#264f78", relief="flat",
                      font=("Consolas", 10))
        txt.pack(side="left", fill="both", expand=True)

        def on_yscroll(first, last):
            ysb.set(first, last)
            gutter.yview_moveto(first)
        txt.configure(yscrollcommand=on_yscroll)
        ysb.configure(command=lambda *a: (txt.yview(*a), gutter.yview(*a)))

        txt.tag_configure("k", foreground=self.JS_KEY)
        txt.tag_configure("s", foreground=self.JS_STR)
        txt.tag_configure("n", foreground=self.JS_NUM)
        txt.tag_configure("w", foreground=self.JS_KW)
        txt.tag_configure("err", background="#5a1d1d")
        txt.insert("1.0", content)
        txt.edit_reset()

        def update_gutter():
            lines = int(txt.index("end-1c").split(".")[0])
            gutter.configure(state="normal")
            gutter.delete("1.0", "end")
            gutter.insert("1.0", "\n".join(str(i) for i in range(1, lines + 1)))
            gutter.configure(state="disabled")
            gutter.yview_moveto(txt.yview()[0])

        def highlight():
            data = txt.get("1.0", "end-1c")
            for tag in ("k", "s", "n", "w"):
                txt.tag_remove(tag, "1.0", "end")
            for m in self.JSON_TOKEN_RE.finditer(data):
                tok = m.group(0)
                start = f"1.0+{m.start()}c"
                if tok.endswith(":"):
                    end = f"1.0+{m.start() + len(tok.rstrip()[:-1].rstrip())}c"
                    txt.tag_add("k", start, end)
                elif tok.startswith('"'):
                    txt.tag_add("s", start, f"1.0+{m.end()}c")
                elif tok[0] in "-0123456789":
                    txt.tag_add("n", start, f"1.0+{m.end()}c")
                else:
                    txt.tag_add("w", start, f"1.0+{m.end()}c")
            update_gutter()

        pending = {"job": None}

        def schedule(event=None):
            if pending["job"]:
                win.after_cancel(pending["job"])
            pending["job"] = win.after(250, highlight)

        txt.bind("<KeyRelease>", schedule)

        def do_format():
            try:
                data = json.loads(txt.get("1.0", "end-1c"))
            except json.JSONDecodeError as e:
                status.config(text=f"Kein gültiges JSON: {e}", foreground="#f28b82")
                return
            txt.delete("1.0", "end")
            txt.insert("1.0", json.dumps(data, indent=4, ensure_ascii=False))
            highlight()
            status.config(text="Formatiert (noch nicht gespeichert)",
                          foreground=C_ACCENT)

        def do_save(event=None):
            txt.tag_remove("err", "1.0", "end")
            raw = txt.get("1.0", "end-1c")
            try:
                json.loads(raw)
            except json.JSONDecodeError as e:
                txt.tag_add("err", f"{e.lineno}.0", f"{e.lineno}.end")
                txt.see(f"{e.lineno}.0")
                status.config(text=f"Fehler Zeile {e.lineno}: {e.msg}",
                              foreground="#f28b82")
                messagebox.showerror(
                    "JSON-Editor",
                    f"Kein gültiges JSON – nicht gespeichert.\n\n"
                    f"Zeile {e.lineno}, Spalte {e.colno}: {e.msg}", parent=win)
                return "break"
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(raw)
            except OSError as e:
                messagebox.showerror("JSON-Editor", str(e), parent=win)
                return "break"
            status.config(text="Gespeichert: " + path, foreground="#8bc48b")
            self.log(f"[JSON] Gespeichert: {path}")
            return "break"

        ttk.Button(bar, text="Speichern (Strg+S)", style="Accent.TButton",
                   command=do_save).pack(side="left", padx=4)
        ttk.Button(bar, text="Formatieren", command=do_format).pack(side="left", padx=4)
        ttk.Button(bar, text="Neu laden",
                   command=lambda: (txt.delete("1.0", "end"),
                                    txt.insert("1.0", open(
                                        path, encoding="utf-8",
                                        errors="replace").read()),
                                    highlight(),
                                    status.config(text="Neu geladen",
                                                  foreground=C_MUTED))
                   ).pack(side="left", padx=4)
        win.bind("<Control-s>", do_save)
        highlight()
        txt.focus_set()

    def request_hotreload(self, profiles, files, win):
        if not files:
            messagebox.showinfo("Hot-Reload", "Nichts ausgewaehlt.")
            return
        if not (self.server_proc and self.server_proc.poll() is None):
            if not messagebox.askyesno(
                    "Server laeuft nicht",
                    "Es laeuft kein Server (ueber den Launcher).\n"
                    "Anforderung trotzdem schreiben?"):
                return
        ts = int(time.time())
        folder = os.path.join(profiles, "zmg_hotreload")
        os.makedirs(folder, exist_ok=True)
        try:
            with open(os.path.join(folder, "request.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"ts": ts, "files": files}, f, indent=2)
        except OSError as e:
            messagebox.showerror("Fehler", str(e))
            return
        self.log(f"[HotReload] Anforderung geschrieben ({len(files)} Datei(en)), "
                 "warte auf Server-Antwort ...")
        win.destroy()
        threading.Thread(target=self._poll_reload_response,
                         args=(profiles, ts), daemon=True).start()

    def _poll_reload_response(self, profiles, ts):
        resp_path = os.path.join(profiles, "zmg_hotreload", "response.json")
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                with open(resp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("ts") != ts:
                continue
            files = data.get("files", [])
            oks = data.get("ok", [])
            msgs = data.get("msg", [])
            for i, fn in enumerate(files):
                ok = i < len(oks) and oks[i]
                msg = msgs[i] if i < len(msgs) else ""
                status = "OK" if ok else "FEHLER"
                self.after(0, self.log,
                           f"[HotReload] {status}: {fn}  {msg}".rstrip())
            return
        self.after(0, self.log,
                   "[HotReload] Keine Antwort vom Server erhalten – laeuft die "
                   "ZMG_HotReload-Mod und ist der Server an?")

    # -------------------------------------------------------- VPP Admin Tools
    def _vpp_selected(self):
        for path, v in self.mod_vars.items():
            if not v.get():
                continue
            label = getattr(self, "mod_labels", {}).get(path, "")
            name = (label + " " + os.path.basename(path)).lower()
            if "vpp" in name:
                return True
        return False

    def _update_vpp_button(self):
        if self._vpp_selected():
            if not self.b_vpp.winfo_ismapped():
                self.b_vpp.pack(side="right", padx=6)
        else:
            self.b_vpp.pack_forget()

    def _on_mod_toggle(self, path):
        self._update_vpp_button()
        if getattr(self, "v_only_active", None) and self.v_only_active.get():
            self.after_idle(self.render_mods)
            return
        if hasattr(self, "lbl_modcount"):
            checked = sum(1 for v in self.mod_vars.values() if v.get())
            txt = self.lbl_modcount.cget("text")
            if "·" in txt:
                self.lbl_modcount.config(
                    text=txt.split("·")[0] + f"· {checked} aktiv")
        v = self.mod_vars.get(path)
        if not v or not v.get():
            return
        name = (getattr(self, "mod_labels", {}).get(path, "") + " " +
                os.path.basename(path)).lower()
        if "vpp" in name:
            self._ensure_cf()

    @staticmethod
    def _is_cf(text):
        t = text.lower().strip()
        return t in ("cf", "@cf") or "community framework" in t

    def _ensure_cf(self):
        cf_path = None
        for p, label in getattr(self, "mod_labels", {}).items():
            if self._is_cf(label) or self._is_cf(os.path.basename(p)):
                cf_path = p
                break
        if cf_path:
            if not self.mod_vars[cf_path].get():
                self.mod_vars[cf_path].set(True)
                self.log("[Mods] CF automatisch mit aktiviert "
                         "(VPPAdminTools benötigt CF).")
        else:
            messagebox.showwarning(
                "CF fehlt",
                "VPPAdminTools benötigt die Mod 'CF (Community Framework)'.\n\n"
                "CF wurde in keiner Mod-Quelle gefunden – bitte im Steam\n"
                "Workshop abonnieren, sonst funktioniert VPPAdminTools nicht.")

    def open_vpp_settings(self):
        win = tk.Toplevel(self)
        win.title("VPP Einstellungen")
        win.geometry("520x260")
        win.resizable(False, False)
        win.configure(bg=C_BG)
        self._dark_titlebar(win)

        ttk.Label(win, text=(
            "Hinweis: Der Server (und Client) muss mit aktivierten\n"
            "VPPAdminTools EINMAL gestartet worden sein, damit VPP seine\n"
            "Ordner im Profiles-Ordner anlegt. Danach hier speichern."),
            foreground=C_ACCENT, justify="left").pack(anchor="w", padx=10, pady=8)

        frm = ttk.Frame(win)
        frm.pack(fill="x", padx=10)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Steam ID (Admin, SteamID64):").grid(
            row=0, column=0, sticky="w", pady=4)
        e_sid = ttk.Entry(frm)
        e_sid.insert(0, self.cfg.get("vpp_steamid", ""))
        e_sid.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(frm, text="VPP Passwort:").grid(
            row=1, column=0, sticky="w", pady=4)
        e_pw = ttk.Entry(frm, show="*")
        e_pw.insert(0, self.cfg.get("vpp_password", ""))
        e_pw.grid(row=1, column=1, sticky="ew", padx=6)

        ttk.Button(win, text="Speichern",
                   command=lambda: self._save_vpp(
                       win, e_sid.get().strip(), e_pw.get().strip())
                   ).pack(pady=10)

    def _save_vpp(self, win, sid, pw):
        if not sid or not pw:
            messagebox.showinfo("VPP Einstellungen",
                                "Bitte Steam ID und Passwort eingeben.",
                                parent=win)
            return
        if not sid.isdigit():
            messagebox.showinfo("VPP Einstellungen",
                                "Die Steam ID darf nur Ziffern enthalten "
                                "(SteamID64).", parent=win)
            return
        if len(sid) != 17 and not messagebox.askyesno(
                "VPP Einstellungen",
                "Eine SteamID64 hat normalerweise 17 Ziffern.\n"
                f"Deine Eingabe hat {len(sid)}. Trotzdem speichern?",
                parent=win):
            return

        profiles = self.e_profiles.get().strip() or \
            os.path.join(DEFAULT_BASE, "TestProfiles")
        perm = os.path.join(profiles, "VPPAdminTools", "Permissions")
        if not os.path.isdir(perm):
            if not messagebox.askyesno(
                    "VPP-Ordner fehlt",
                    "Der Ordner VPPAdminTools\\Permissions existiert noch "
                    "nicht im Profiles-Ordner.\n\n"
                    "Er wird normalerweise beim ERSTEN Serverstart mit VPP "
                    "angelegt.\n\nOrdner jetzt trotzdem anlegen und die "
                    "Dateien schreiben?", parent=win):
                return
        sa_dir = os.path.join(perm, "SuperAdmins")
        try:
            os.makedirs(sa_dir, exist_ok=True)
            with open(os.path.join(sa_dir, "SuperAdmins.txt"), "w",
                      encoding="utf-8") as f:
                f.write(sid + "\n")
            with open(os.path.join(perm, "credentials.txt"), "w",
                      encoding="utf-8") as f:
                f.write(pw + "\n")
        except OSError as e:
            messagebox.showerror("VPP Einstellungen", str(e), parent=win)
            return
        self.cfg["vpp_steamid"] = sid
        self.cfg["vpp_password"] = pw
        self.save_config()
        self.log(f"[VPP] SuperAdmins.txt und credentials.txt geschrieben "
                 f"({perm}).")
        win.destroy()

    # ---------------------------------------------------------------- presets
    def update_preset_list(self):
        names = sorted(self.cfg.get("presets", {}).keys())
        self.cb_preset["values"] = names

    def apply_preset(self, event=None):
        name = self.cb_preset.get()
        paths = set(self.cfg.get("presets", {}).get(name, []))
        if not paths:
            return
        for p, v in self.mod_vars.items():
            v.set(p in paths)
        self.log(f"[Launcher] Preset geladen: {name}")

    def save_preset(self):
        name = self.cb_preset.get().strip()
        if not name:
            messagebox.showinfo("Preset", "Bitte erst einen Namen ins Preset-Feld tippen.")
            return
        self.cfg.setdefault("presets", {})[name] = \
            [p for p, v in self.mod_vars.items() if v.get()]
        self.update_preset_list()
        self.save_config()
        self.log(f"[Launcher] Preset gespeichert: {name}")

    def delete_preset(self):
        name = self.cb_preset.get().strip()
        if name in self.cfg.get("presets", {}):
            del self.cfg["presets"][name]
            self.update_preset_list()
            self.cb_preset.set("")
            self.save_config()
            self.log(f"[Launcher] Preset gelöscht: {name}")

    # ----------------------------------------------------------- storage wipe
    def wipe_storage(self):
        if self.server_proc and self.server_proc.poll() is None:
            messagebox.showwarning("Storage wipen",
                                   "Bitte zuerst den Server stoppen.")
            return
        mission = self.e_mission.get().strip()
        storage = os.path.join(mission, "storage_1")
        if not os.path.isdir(storage):
            messagebox.showinfo("Storage wipen",
                                f"Kein storage_1-Ordner gefunden in:\n{mission}")
            return
        if messagebox.askyesno("Storage wipen",
                               "storage_1 wirklich löschen?\n"
                               "(Persistenz/Economy der Testmission wird zurückgesetzt)"):
            try:
                shutil.rmtree(storage)
                self.log("[Launcher] storage_1 gelöscht – frische Economy beim nächsten Start.")
            except OSError as e:
                messagebox.showerror("Fehler", str(e))

    # ---------------------------------------------------------------- watchdog
    def watchdog_worker(self):
        snapshot = {}
        while True:
            time.sleep(2)
            try:
                active = self.v_watchdog.get()
            except RuntimeError:      # Fenster wird gerade geschlossen
                return
            if not active or \
                    not (self.server_proc and self.server_proc.poll() is None):
                snapshot = {}
                continue
            pd = os.path.normpath(self.e_pdrive.get().strip() or "P:\\").lower()
            roots = [p for p, v in self.mod_vars.items()
                     if v.get() and os.path.normpath(p).lower().startswith(pd)]
            new_snap = {}
            changed = None
            for root_dir in roots:
                for dirpath, _, files in os.walk(root_dir):
                    for fn in files:
                        if not fn.lower().endswith(WATCH_EXT):
                            continue
                        fp = os.path.join(dirpath, fn)
                        try:
                            m = os.path.getmtime(fp)
                        except OSError:
                            continue
                        new_snap[fp] = m
                        if snapshot and snapshot.get(fp) != m:
                            changed = fp
            if snapshot and changed:
                time.sleep(2)                      # kurze Ruhe fuer Mehrfach-Saves
                self.after(0, self.on_source_changed, changed)
                snapshot = {}                      # nach Neustart frisch aufbauen
            else:
                snapshot = new_snap

    def on_source_changed(self, changed_file):
        self.log(f"[Watchdog] Änderung erkannt: {changed_file}")
        restart_client = self.v_wd_client.get() and \
            self.client_proc and self.client_proc.poll() is None
        self.log("[Watchdog] Starte Server neu ...")
        self.stop_all()
        self.after(2500, self.start_server)
        if restart_client:
            self.log("[Watchdog] Client startet in ~12 Sekunden neu ...")
            self.after(12000, self.start_client)

    # ---------------------------------------------------------------- starts
    def common_checks(self, role="server"):
        exe = self.server_exe() if role == "server" else self.client_exe()
        if not exe:
            return None
        self.save_config()
        return exe

    def check_mods(self):
        """Angehakte Mods pruefen und Auffaelligkeiten ins Log schreiben."""
        chosen = [p for p, v in self.mod_vars.items() if v.get()]
        if not chosen:
            self.log("[Mods] Kein Mod angehakt – Start ohne Mods.")
            return
        self.log(f"[Mods] {len(chosen)} Mod(s) werden uebergeben:")
        classes = {}          # CfgPatches-Klasse -> Mod
        required = {}         # Mod -> benoetigte Addons
        for p in chosen:
            cpp = os.path.join(p, "config.cpp")
            binf = os.path.join(p, "config.bin")
            addons = os.path.join(p, "addons")
            has_pbo = False
            if os.path.isdir(addons):
                try:
                    has_pbo = any(f.lower().endswith(".pbo")
                                  for f in os.listdir(addons))
                except OSError:
                    pass
            if has_pbo:
                kind = "PBO"
            elif os.path.isfile(cpp) or os.path.isfile(binf):
                kind = "Quelle unverpackt (braucht -filePatching)"
            else:
                kind = "!! keine Config gefunden"
            self.log(f"    - {p}   [{kind}]")

            if os.path.isfile(cpp) and os.path.isfile(binf):
                self.log("        !! config.cpp UND config.bin vorhanden – "
                         "die .bin gewinnt, Aenderungen an der .cpp wirken "
                         "nicht. Tipp: config.bin loeschen.")

            if kind.startswith("!!"):
                # eine Ebene tiefer suchen: haeufigster Fehler (falscher Ordner)
                try:
                    for sub in os.listdir(p):
                        subp = os.path.join(p, sub)
                        if os.path.isdir(subp) and (
                                os.path.isfile(os.path.join(subp, "config.cpp"))
                                or os.path.isfile(os.path.join(subp,
                                                               "config.bin"))):
                            self.log(f"        -> Config liegt in: {subp}")
                            self.log("        -> Diesen Unterordner anhaken, "
                                     "nicht den Elternordner!")
                            break
                except OSError:
                    pass
                continue

            # CfgPatches auswerten (nur aus lesbarer config.cpp moeglich)
            if os.path.isfile(cpp) and not os.path.isfile(binf):
                try:
                    with open(cpp, "r", encoding="utf-8",
                              errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                m = re.search(r"class\s+CfgPatches\s*\{(.*?)\n\s*\};",
                              text, re.S)
                if not m:
                    self.log("        !! kein CfgPatches-Block in der "
                             "config.cpp – DayZ laedt die Mod dann nicht.")
                    continue
                block = m.group(1)
                for cls in re.findall(r"class\s+(\w+)", block):
                    classes[cls] = p
                req = re.findall(r'requiredAddons\s*\[\s*\]\s*=\s*\{([^}]*)\}',
                                 block)
                names = []
                for r in req:
                    names += [x.strip().strip('"') for x in r.split(",")
                              if x.strip()]
                if names:
                    required[p] = names

        # fehlende Abhaengigkeiten melden
        known = {c.lower() for c in classes}
        for p, names in required.items():
            missing = [n for n in names
                       if n.lower() not in known and not n.lower().startswith(
                           ("dz_", "dayz_"))]
            if missing:
                self.log(f"        !! {os.path.basename(p)}: requiredAddons "
                         f"nicht in der Mod-Liste: {', '.join(missing)}")
        self.log("[Mods] Pruefung fertig. Im RPT weiter unten steht, welche "
                 "Addons wirklich geladen wurden.")

    def resolve_mission(self, mission):
        """Zeigt das Feld auf den Ueberordner, den echten Missionsordner finden."""
        if not mission or not os.path.isdir(mission):
            return mission
        if os.path.isfile(os.path.join(mission, "init.c")):
            return mission
        try:
            subs = [s for s in sorted(os.listdir(mission))
                    if os.path.isdir(os.path.join(mission, s))
                    and os.path.isfile(os.path.join(mission, s, "init.c"))]
        except OSError:
            return mission
        if len(subs) == 1:
            found = os.path.join(mission, subs[0])
            self.e_mission.delete(0, "end")
            self.e_mission.insert(0, found)
            self.save_config()
            self.log(f"[Launcher] Missionsordner erkannt: {found}")
            return found
        return mission

    CFG_FLAGS = (("BattlEye", "0"),
                 ("verifySignatures", "0"),
                 ("allowFilePatching", "1"))

    def ensure_cfg_flags(self, cfgfile):
        """serverDZ.cfg auf die Testschalter pruefen und auf Wunsch setzen."""
        try:
            with open(cfgfile, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return
        missing = []
        for key, want in self.CFG_FLAGS:
            m = re.search(r"^\s*%s\s*=\s*([^;]+);" % key, text,
                          re.IGNORECASE | re.MULTILINE)
            if not m or m.group(1).strip() != want:
                missing.append((key, want, m is not None))
        if not missing:
            return
        namen = ", ".join(f"{k} = {v}" for k, v, _ in missing)
        if not messagebox.askyesno(
                "serverDZ.cfg anpassen",
                "Fuer lokale Tests fehlen in der serverDZ.cfg:\n\n"
                f"{namen}\n\n"
                "Ohne BattlEye = 0 kickt BattlEye dich beim Login "
                "(0x000400F0).\n\nJetzt setzen?"):
            return
        for key, want, exists in missing:
            if exists:
                text = re.sub(r"^\s*%s\s*=\s*[^;]+;" % key,
                              f"{key} = {want};", text,
                              count=1, flags=re.IGNORECASE | re.MULTILINE)
            else:
                text = f"{key} = {want};\n" + text
        try:
            with open(cfgfile, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            self.log(f"[Launcher] serverDZ.cfg nicht schreibbar: {e}")
            return
        self.log(f"[Launcher] serverDZ.cfg angepasst: {namen}")

    def ensure_profiles_inside(self, exe, profiles):
        """BattlEye braucht den Profiles-Ordner in der Server-Installation.

        Liegt er ausserhalb, initialisiert BE nicht und Clients fliegen mit
        0x000400F0 raus (klassischer Unterschied zu einer Start-Batch, die
        -profiles=config nutzt).
        """
        if os.path.basename(exe).lower().startswith("dayzdiag"):
            return profiles            # Diag laeuft ohne BattlEye
        root = os.path.normpath(os.path.dirname(exe)).lower()
        if os.path.normpath(profiles).lower().startswith(root):
            return profiles
        inside = os.path.join(os.path.dirname(exe), "config")
        if not messagebox.askyesno(
                "Profiles-Ordner und BattlEye",
                "Der Profiles-Ordner liegt ausserhalb der Server-Installation:"
                f"\n{profiles}\n\n"
                "BattlEye legt seine Dateien dort ab und startet sonst nicht "
                "sauber – der Client wird dann mit 0x000400F0 gekickt.\n\n"
                f"Stattdessen diesen Ordner nutzen?\n{inside}"):
            return profiles
        os.makedirs(inside, exist_ok=True)
        self.e_profiles.delete(0, "end")
        self.e_profiles.insert(0, inside)
        self.save_config()
        self.log(f"[Launcher] Profiles-Ordner auf {inside} umgestellt "
                 "(BattlEye-Anforderung).")
        self.start_log_tail(inside)
        return inside

    def start_server(self):
        exe = self.common_checks()
        if not exe:
            return
        mission = self.e_mission.get().strip()
        cfgfile = self.e_servercfg.get().strip()
        profiles = self.e_profiles.get().strip()
        mission = self.resolve_mission(mission)
        if not os.path.isdir(mission) or \
                not os.path.isfile(os.path.join(mission, "init.c")):
            if messagebox.askyesno("Mission fehlt", "Fuer dieses Profil fehlen die Missionsdateien:\n%s\n\nJetzt holen (aus der Server-Installation kopieren oder von GitHub laden)?" % mission):
                self.download_missions()
            return
        if not os.path.isfile(cfgfile):
            if not self.create_cfg_if_missing(
                    cfgfile, os.path.basename(os.path.normpath(mission))):
                return
        if not profiles:
            profiles = os.path.join(DEFAULT_BASE, "TestProfiles")
        os.makedirs(profiles, exist_ok=True)
        self.e_profiles.delete(0, "end")
        self.e_profiles.insert(0, profiles)

        self.sync_cfg_template()
        if not self.ensure_required_active():
            return
        self.ensure_cfg_flags(cfgfile)
        profiles = self.ensure_profiles_inside(exe, profiles)
        args = [exe, "-server",
                f"-config={cfgfile}",
                f"-mission={mission}",
                f"-profiles={profiles}",
                f"-port={self.e_port.get().strip() or '2302'}",
                "-doLogs", "-adminLog", "-freezeCheck"]
        if os.path.basename(exe).lower().startswith("dayzdiag"):
            args.insert(6, "-filePatching")
        else:
            # Retail-Server: BattlEye braucht seinen eigenen Ordner, sonst
            # fliegen Clients mit 0x000400F0 raus
            bepath = os.path.join(os.path.dirname(exe), "battleye")
            if os.path.isdir(bepath):
                args.append(f"-BEpath={bepath}")
                self.log(f"[Launcher] BattlEye-Pfad gesetzt: {bepath}")
            else:
                self.log("[Launcher] ACHTUNG: kein battleye-Ordner in der "
                         "Server-Installation gefunden – BattlEye kann "
                         "Clients kicken (0x000400F0).")
            self.log("[Launcher] Retail-Server: ohne -filePatching (BattlEye kickt \n                 sonst); Mods laufen als gepackte PBOs.")
        mp = self.mod_param()
        if mp:
            args.append(mp)
        args += self.e_extra.get().strip().split()

        self.check_mods()
        self.log("[Launcher] Starte Server:\n  " + " ".join(args))
        try:
            self.server_proc = subprocess.Popen(args, cwd=self.e_dayz.get().strip())
        except OSError as e:
            messagebox.showerror("Fehler", str(e))
            return
        self.start_log_tail(profiles)

    def start_client(self):
        exe = self.common_checks(role="client")
        if not exe:
            return
        port = self.e_port.get().strip() or "2302"
        args = [exe, f"-connect=127.0.0.1", f"-port={port}",
                "-doLogs", "-name=ZMG_Tester"]
        if os.path.basename(exe).lower().startswith("dayzdiag"):
            args.insert(3, "-filePatching")
        mp = self.mod_param()
        if mp:
            args.append(mp)
        args += self.e_extra.get().strip().split()

        self.log("[Launcher] Client-Exe: " + os.path.basename(exe))
        self.log("[Launcher] Starte Client:\n  " + " ".join(args))
        try:
            self.client_proc = subprocess.Popen(
                args, cwd=os.path.dirname(exe))
        except OSError as e:
            messagebox.showerror("Fehler", str(e))

    def start_both(self):
        self.start_server()
        # Client leicht verzoegert, damit der Server den Port belegt
        self.after(8000, self.start_client)
        self.log("[Launcher] Client startet in 8 Sekunden ...")

    def stop_all(self):
        for name in ("server_proc", "client_proc"):
            proc = getattr(self, name)
            if proc and proc.poll() is None:
                proc.terminate()
                self.log(f"[Launcher] {name} beendet.")
            setattr(self, name, None)
        self.log_stop.set()
        if getattr(self, "v_temp_pbo", None) and self.v_temp_pbo.get():
            self.after(1500, lambda: self.clear_pbo_cache(quiet=True))

    # ------------------------------------------------------------- log tail
    def start_log_tail(self, profiles):
        self.log_stop.set()
        time.sleep(0.1)
        self.log_stop = threading.Event()
        t = threading.Thread(target=self.tail_worker,
                             args=(profiles, self.log_stop), daemon=True)
        t.start()
        self.log_thread = t

    def tail_worker(self, profiles, stop):
        # wartet auf die neueste RPT-Datei und haengt sich dran
        current = None
        fh = None
        deadline = time.time() + 30
        while not stop.is_set():
            try:
                rpts = [os.path.join(profiles, f) for f in os.listdir(profiles)
                        if f.lower().endswith(".rpt")]
                newest = max(rpts, key=os.path.getmtime) if rpts else None
                if newest and newest != current and \
                        os.path.getmtime(newest) > time.time() - 120:
                    if fh:
                        fh.close()
                    current = newest
                    fh = open(current, "r", encoding="utf-8", errors="replace")
                    self.after(0, self.log, f"[Launcher] Log: {current}")
                if fh:
                    line = fh.readline()
                    if line:
                        self.after(0, self.log, line)
                        continue
                elif time.time() > deadline:
                    self.after(0, self.log,
                               "[Launcher] Noch keine RPT-Datei gefunden ...")
                    deadline = time.time() + 30
            except OSError:
                pass
            time.sleep(0.4)
        if fh:
            fh.close()

    # ------------------------------------------------------------ lifecycle
    def watch_processes(self):
        s = "an" if self.server_proc and self.server_proc.poll() is None else "aus"
        c = "an" if self.client_proc and self.client_proc.poll() is None else "aus"
        self.lbl_status.config(text=f"Server: {s}   |   Client: {c}")
        self.after(1000, self.watch_processes)

    def on_close(self):
        self.save_config()
        if self.v_temp_pbo.get():
            self.clear_pbo_cache(quiet=True)
        self.log_stop.set()
        self.destroy()


if __name__ == "__main__":
    Launcher().mainloop()
