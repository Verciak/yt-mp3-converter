#!/usr/bin/env python3
"""
YT → MP3 — konwerter filmów z YouTube do MP3 z GUI.

Wymagania:
    pip install -r requirements.txt
    + ffmpeg dostępny w PATH
      Windows:  winget install Gyan.FFmpeg  (+ winget install DenoLand.Deno)
      Linux:    sudo apt install ffmpeg
      macOS:    brew install ffmpeg
"""

import json
import os
import re
import shutil
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yt_dlp

CONFIG_FILE = Path.home() / ".yt_mp3_config.json"
DEFAULT_FOLDER = str(Path.home() / "Music")
COOKIE_FILE_OPTION = "plik cookies.txt"
COOKIE_SOURCES = ["brak", "firefox", "chrome", "edge", "brave", "opera", COOKIE_FILE_OPTION]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def human_size(n) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("YT → MP3")
        self.geometry("680x560")
        self.minsize(560, 500)

        self.cfg = load_config()
        self.folder = ctk.StringVar(value=self.cfg.get("folder", DEFAULT_FOLDER))
        self.quality = ctk.StringVar(value=self.cfg.get("quality", "192"))
        self.playlist = ctk.BooleanVar(value=False)
        self.cover = ctk.BooleanVar(value=self.cfg.get("cover", True))
        self.cookies = ctk.StringVar(value=self.cfg.get("cookies", "brak"))
        self.cookie_path = self.cfg.get("cookie_path", "")
        self.busy = False

        self._build_ui()
        self.after(200, self._check_ffmpeg)

    # ---------------- UI ----------------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        # Nagłówek
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(22, 6), sticky="ew")
        ctk.CTkLabel(header, text="🎵 YouTube → MP3",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="Wklej link, wybierz folder i kliknij Pobierz.",
                     text_color="gray70").pack(anchor="w")

        # Link
        url_card = ctk.CTkFrame(self, corner_radius=12)
        url_card.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        url_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(url_card, text="Link do filmu", font=ctk.CTkFont(weight="bold")
                     ).grid(row=0, column=0, columnspan=2, padx=14, pady=(12, 4), sticky="w")
        self.url_entry = ctk.CTkEntry(url_card, height=38,
                                      placeholder_text="https://www.youtube.com/watch?v=...")
        self.url_entry.grid(row=1, column=0, padx=(14, 6), pady=(0, 14), sticky="ew")
        self.url_entry.bind("<Return>", lambda e: self.start())
        ctk.CTkButton(url_card, text="Wklej", width=80, height=38,
                      command=self._paste).grid(row=1, column=1, padx=(0, 14), pady=(0, 14))

        # Folder
        folder_card = ctk.CTkFrame(self, corner_radius=12)
        folder_card.grid(row=2, column=0, padx=24, pady=8, sticky="ew")
        folder_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(folder_card, text="Folder zapisu", font=ctk.CTkFont(weight="bold")
                     ).grid(row=0, column=0, columnspan=3, padx=14, pady=(12, 4), sticky="w")
        ctk.CTkEntry(folder_card, textvariable=self.folder, height=38
                     ).grid(row=1, column=0, padx=(14, 6), pady=(0, 14), sticky="ew")
        ctk.CTkButton(folder_card, text="Wybierz…", width=90, height=38,
                      command=self._choose_folder).grid(row=1, column=1, padx=(0, 6), pady=(0, 14))
        ctk.CTkButton(folder_card, text="Otwórz", width=80, height=38, fg_color="gray30",
                      hover_color="gray25", command=self._open_folder
                      ).grid(row=1, column=2, padx=(0, 14), pady=(0, 14))

        # Opcje
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=3, column=0, padx=24, pady=4, sticky="ew")
        ctk.CTkLabel(opts, text="Jakość:").pack(side="left")
        ctk.CTkSegmentedButton(opts, values=["128", "192", "256", "320"],
                               variable=self.quality).pack(side="left", padx=(8, 4))
        ctk.CTkLabel(opts, text="kbps").pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(opts, text="Okładka", variable=self.cover).pack(side="left", padx=(0, 14))
        ctk.CTkCheckBox(opts, text="Cała playlista", variable=self.playlist).pack(side="left")

        # Ciasteczka (filmy z ograniczeniem wiekowym / wymagające logowania)
        cookies_row = ctk.CTkFrame(self, fg_color="transparent")
        cookies_row.grid(row=4, column=0, padx=24, pady=(8, 0), sticky="ew")
        ctk.CTkLabel(cookies_row, text="Logowanie (ciasteczka z):").pack(side="left")
        ctk.CTkOptionMenu(cookies_row, values=COOKIE_SOURCES, variable=self.cookies,
                          width=150, command=self._on_cookie_change).pack(side="left", padx=8)
        self.cookie_label = ctk.CTkLabel(cookies_row, text="", text_color="gray60")
        self.cookie_label.pack(side="left")
        self._update_cookie_label()

        # Przycisk + postęp
        action = ctk.CTkFrame(self, fg_color="transparent")
        action.grid(row=5, column=0, padx=24, pady=(10, 6), sticky="ew")
        action.grid_columnconfigure(0, weight=1)
        self.btn = ctk.CTkButton(action, text="⬇  Pobierz MP3", height=44,
                                 font=ctk.CTkFont(size=16, weight="bold"), command=self.start)
        self.btn.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(action, height=12)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, pady=(12, 4), sticky="ew")
        self.status = ctk.CTkLabel(action, text="Gotowy.", text_color="gray70", anchor="w")
        self.status.grid(row=2, column=0, sticky="ew")

        # Log
        self.log_box = ctk.CTkTextbox(self, corner_radius=12, height=120,
                                      font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=6, column=0, padx=24, pady=(6, 20), sticky="nsew")
        self.log_box.configure(state="disabled")

    # ---------------- Ciasteczka ----------------
    def _on_cookie_change(self, choice):
        if choice == COOKIE_FILE_OPTION:
            path = filedialog.askopenfilename(title="Wybierz plik cookies.txt",
                                              filetypes=[("Pliki tekstowe", "*.txt"),
                                                         ("Wszystkie pliki", "*.*")])
            if path:
                self.cookie_path = path
            elif not self.cookie_path:
                self.cookies.set("brak")
        self._update_cookie_label()

    def _update_cookie_label(self):
        choice = self.cookies.get()
        if choice == COOKIE_FILE_OPTION and self.cookie_path:
            self.cookie_label.configure(text=Path(self.cookie_path).name)
        elif choice == "brak":
            self.cookie_label.configure(text="(potrzebne przy filmach 18+)")
        else:
            self.cookie_label.configure(text="musisz być zalogowany w tej przeglądarce")

    # ---------------- Pomocnicze ----------------
    def _paste(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text)

    def _choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.folder.get() or DEFAULT_FOLDER,
                                       title="Wybierz folder zapisu")
        if path:
            self.folder.set(path)

    def _open_folder(self):
        path = self.folder.get()
        if not os.path.isdir(path):
            messagebox.showwarning("Brak folderu", "Ten folder nie istnieje.")
            return
        if os.name == "nt":
            os.startfile(path)
        elif shutil.which("open"):
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def _check_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            self.log("⚠ Nie znaleziono ffmpeg w PATH — konwersja do MP3 nie zadziała.")
            messagebox.showwarning(
                "Brak ffmpeg",
                "Program potrzebuje ffmpeg do konwersji.\n\n"
                "Windows: winget install ffmpeg\nLinux: sudo apt install ffmpeg\n"
                "macOS: brew install ffmpeg\n\nPo instalacji uruchom program ponownie.")

    def log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_status(self, frac, text):
        if frac is not None:
            self.progress.set(max(0.0, min(1.0, frac)))
        self.status.configure(text=text)

    def _ui(self, func, *args):
        """Bezpieczne wywołanie funkcji UI z wątku roboczego."""
        self.after(0, func, *args)

    # ---------------- Pobieranie ----------------
    def start(self):
        if self.busy:
            return
        url = self.url_entry.get().strip()
        folder = self.folder.get().strip()
        if not url:
            messagebox.showinfo("Brak linku", "Wklej link do filmu z YouTube.")
            return
        if not folder:
            messagebox.showinfo("Brak folderu", "Wybierz folder zapisu.")
            return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Błąd folderu", str(e))
            return

        self.cfg.update(folder=folder, quality=self.quality.get(), cover=self.cover.get(),
                        cookies=self.cookies.get(), cookie_path=self.cookie_path)
        save_config(self.cfg)

        self.busy = True
        self.btn.configure(state="disabled", text="Pobieranie…")
        self._set_status(0, "Pobieranie informacji o filmie…")
        self.log(f"▶ {url}")
        threading.Thread(target=self._worker, args=(url, folder), daemon=True).start()

    def _progress_hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            frac = done / total if total else 0
            speed = human_size(d.get("speed"))
            speed = f"  •  {speed}/s" if speed else ""
            name = Path(d.get("filename", "")).stem[:45]
            self._ui(self._set_status, frac, f"Pobieranie: {name}  {frac * 100:.0f}%{speed}")
        elif d["status"] == "finished":
            self._ui(self._set_status, 1, "Konwertowanie do MP3…")

    def _pp_hook(self, d):
        if d["status"] == "finished" and d.get("postprocessor") == "MoveFiles":
            path = d.get("info_dict", {}).get("filepath")
            if path:
                self._ui(self.log, f"✔ Zapisano: {Path(path).name}")

    def _worker(self, url, folder):
        postprocessors = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": self.quality.get()},
            {"key": "FFmpegMetadata"},
        ]
        if self.cover.get():
            postprocessors.insert(0, {"key": "FFmpegThumbnailsConvertor",
                                      "format": "jpg", "when": "before_dl"})
            postprocessors.append({"key": "EmbedThumbnail"})

        opts = {
            "format": "bestaudio/bestaudio*/best*",
            "outtmpl": os.path.join(folder, "%(title)s.%(ext)s"),
            "noplaylist": not self.playlist.get(),
            "writethumbnail": self.cover.get(),
            "postprocessors": postprocessors,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._pp_hook],
            "windowsfilenames": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "color": {"stdout": "no_color", "stderr": "no_color"},
        }

        source = self.cookies.get()
        if source == COOKIE_FILE_OPTION and self.cookie_path:
            opts["cookiefile"] = self.cookie_path
        elif source not in ("brak", COOKIE_FILE_OPTION):
            opts["cookiesfrombrowser"] = (source,)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            self._ui(self._set_status, 1, "✅ Gotowe!")
            self._ui(self.url_entry.delete, 0, "end")
        except yt_dlp.utils.DownloadError as e:
            msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").strip()
            self._ui(self.log, f"✖ Błąd: {msg}")
            if "Sign in" in msg or "age" in msg.lower():
                self._ui(self.log, "  ↳ Ten film wymaga zalogowania — wybierz przeglądarkę "
                                   "w polu „Logowanie” i spróbuj ponownie.")
            elif "format is not available" in msg:
                self._ui(self.log, "  ↳ Zaktualizuj yt-dlp i zainstaluj Deno:\n"
                                   "    py -m pip install -U \"yt-dlp[default]\"\n"
                                   "    winget install DenoLand.Deno")
            elif "cookie" in msg.lower() or "DPAPI" in msg:
                self._ui(self.log, "  ↳ Nie udało się odczytać ciasteczek. Zamknij przeglądarkę "
                                   "albo użyj Firefoksa / pliku cookies.txt.")
            self._ui(self._set_status, 0, "Nie udało się pobrać — szczegóły w logu.")
        except Exception as e:
            self._ui(self.log, f"✖ Nieoczekiwany błąd: {e}")
            self._ui(self._set_status, 0, "Wystąpił błąd.")
        finally:
            self._ui(self._finish)

    def _finish(self):
        self.busy = False
        self.btn.configure(state="normal", text="⬇  Pobierz MP3")


if __name__ == "__main__":
    App().mainloop()
