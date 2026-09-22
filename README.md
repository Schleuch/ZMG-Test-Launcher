# ZMG Test Launcher

A desktop tool that brings the entire DayZ mod testing workflow to your local machine.
No more copying PBOs to a server, signing mods, or waiting for restarts.

The goal was simple: **reduce the edit → test cycle from minutes to seconds.**

<!-- Screenshot: replace with your own image, e.g. docs/screenshot.png -->
<!-- ![ZMG Test Launcher](docs/screenshot.png) -->

---

## Features

- 🚀 **One-click start** – DayZ server and client with a single click (DayZDiag + filePatching). Server and client exe types are matched automatically, so you never hit the "client uses Diag.exe, server does not" error.
- 🗺️ **Map profiles** – Chernarus, Livonia, Sakhal, Namalsk and Experimental out of the box. Every profile has its own mission folder, profiles folder, serverDZ config, port, mod selection and load order. Create as many custom profiles as you like.
- 📥 **Mission downloader** – fetches the official mission files straight from Bohemia's Central Economy repository, Namalsk from Sumrak's server repository, or copies them from your server installation so they match your build exactly.
- 🔧 **Required mods per map** – the launcher knows Namalsk needs *Namalsk Island* and *Namalsk Survival*, tells you when they are missing, opens the Workshop page and ticks them automatically once downloaded. The server will not start with required mods missing.
- 📦 **Mods from your P: drive** – source folders are packed into PBOs automatically and only rebuilt when files change. No AddonBuilder, no signing.
- 🔍 **Mod manager** – search, source filters (Workshop / P: / PBOs / folders), "active only" view, presets and a load-order editor. Handles 1000+ subscribed mods without lag.
- 📄 **Live log viewer** – errors highlighted in red; click an error line and the script opens at the exact line.
- 🔁 **JSON hot reload** – reload JSON configs while the server is running, plus a built-in JSON editor with syntax highlighting and validation before saving.
- ⚙️ **Quick settings** – login timer, server start time, storage wipe, auto-restart when source files change.
- 🛠️ **serverDZ template per profile** – `BattlEye = 0`, `verifySignatures = 0` and `allowFilePatching = 1` are set for you, and the `template` line always matches the profile's mission.
- 👮 **VPP Admin Tools setup** – enter SteamID and password once; CF is enabled automatically as a dependency.
- 🌙 Dark DayZ-style interface, German and English version.

---

## Requirements

- **Windows 10/11**
- **Python 3.8 or newer** – from [python.org](https://www.python.org/downloads/). Only the standard library is used, no `pip install` needed.
- **DayZ** (Steam) including **DayZDiag_x64.exe** – comes with the DayZ Tools or the diag branch (Steam → DayZ → Properties → Betas)
- Optional: **P: drive** mounted via DayZ Tools for testing your own mod sources
- Optional: **DayZ Server / DayZ Server Exp** installation for the Experimental profile

---

## Installation & start

1. Download `zmg_test_launcher.py` (German) or `zmg_test_launcher_en.py` (English) from the [Releases](../../releases) page.
2. Put the file into any folder.
3. Start it with a double click or from a terminal:

   ```
   python zmg_test_launcher_en.py
   ```

4. Pick a profile, click **Create** next to the serverDZ.cfg and mission fields, tick your mods and hit **Start both**.

Settings are stored in `%LOCALAPPDATA%\ZMG_Test_Launcher\` – you can replace the script with a newer version at any time without losing your profiles, presets or paths.

---

## Default profiles

| Profile | Mission | Port |
|---|---|---|
| Chernarus Stable | `dayzOffline.chernarusplus` | 2302 |
| EXP | `dayzOffline.chernarusplus` (from DayZ Server Exp) | 2402 |
| Livonia Stable | `dayzOffline.enoch` | 2502 |
| Sakhal Stable | `dayzOffline.sakhal` | 2602 |
| Namalsk Stable | `regular.namalsk` | 2702 |

Mission and profiles folders are created under `C:\ZmG_Test_Launcher\` by default; every path can be changed per profile.

---

## Notes

- **Local testing only.** The generated server configs disable BattlEye and signature checks. Never use them for a public server.
- **Sakhal** requires the Frostline DLC.
- **Namalsk** requires the Workshop mods [Namalsk Island](https://steamcommunity.com/sharedfiles/filedetails/?id=2289456201) and [Namalsk Survival](https://steamcommunity.com/sharedfiles/filedetails/?id=2289461232).
- **JSON hot reload** needs the small companion server mod `ZMG_HotReload`, which registers a reload handler for each config.
- Subscribing to Workshop items always happens in Steam – the launcher opens the page and tracks the download, it cannot subscribe on your behalf.

---

## Credits

- Mission files: [BohemiaInteractive/DayZ-Central-Economy](https://github.com/BohemiaInteractive/DayZ-Central-Economy)
- Namalsk mission files: [SumrakDZN/Namalsk-Server](https://github.com/SumrakDZN/Namalsk-Server)

---

## Deutsch

Der ZMG Test Launcher holt den kompletten DayZ-Mod-Testablauf auf den eigenen Rechner: Server und Client per Klick, Mods direkt vom Laufwerk P (automatisch gepackt), eigene Profile pro Map mit getrennten Missionen, Configs und Ports, Missions-Download, Pflicht-Mods pro Map und ein Log mit anklickbaren Fehlerzeilen.

Für die deutsche Oberfläche `zmg_test_launcher.py` herunterladen und mit Python 3.8+ starten. Einstellungen liegen in `%LOCALAPPDATA%\ZMG_Test_Launcher\` und bleiben bei Updates erhalten.

---

## License

See [LICENSE](LICENSE).
