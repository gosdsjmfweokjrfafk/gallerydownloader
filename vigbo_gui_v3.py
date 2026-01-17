import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import requests
import json
import re
import os
import threading
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}

class DownloaderLogic:
    def __init__(self, log_callback, progress_callback, finish_callback):
        self.log = log_callback
        self.update_progress = progress_callback
        self.finish = finish_callback
        self.is_running = False

    def clean_url(self, raw_url):
        match = re.search(r'(https?://[^\s)\]"\']+)', raw_url)
        if match:
            url = match.group(1)
            if url.endswith('/'): url = url[:-1]
            return url
        return None

    def extract_author(self, url):
        """Вытаскивает автора из поддомена: https://author.gallery.photo -> author"""
        match = re.search(r'https?://([^.]+)\.gallery\.photo', url)
        if match:
            return match.group(1)
        return "unknown"

    def get_unique_filename(self, folder, filename):
        """Если файл существует, добавляет (1), (2) и т.д."""
        name, ext = os.path.splitext(filename)
        counter = 1
        unique_name = filename
        
        while os.path.exists(os.path.join(folder, unique_name)):
            unique_name = f"{name}({counter}){ext}"
            counter += 1
            
        return unique_name

    def get_gallery_id(self, url, session):
        try:
            response = session.get(url, timeout=10)
            if response.status_code != 200:
                self.log(f"❌ {url} -> Ошибка: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            script_tag = soup.find('script', id='__NEXT_DATA__')
            
            if not script_tag:
                self.log(f"❌ {url} -> Не найден JSON")
                return None

            data = json.loads(script_tag.string)
            gallery = data['props']['pageProps']['gallery']
            return gallery['id'], gallery.get('name', 'gallery')

        except Exception as e:
            self.log(f"💀 Ошибка парсинга {url}: {e}")
            return None

    def get_download_link(self, base_url, gallery_id, version, session):
        match = re.match(r'(https?://[^/]+)', base_url)
        if not match: return None
        origin = match.group(1)
        api_url = f"{origin}/api/v1/download-gallery/{gallery_id}/{version}/"
        
        try:
            resp = session.get(api_url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('data', {}).get('url') or data.get('url')
            elif resp.status_code == 404:
                self.log(f"⚠️ Версия {version} не найдена")
            elif resp.status_code == 403:
                self.log(f"⛔ Доступ запрещен (пароль?)")
        except Exception:
            pass
        return None

    def download_file(self, url, folder, filename):
        # Генерируем уникальное имя ПЕРЕД скачиванием
        final_filename = self.get_unique_filename(folder, filename)
        local_path = os.path.join(folder, final_filename)
        
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('content-length', 0))
                
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if not self.is_running: break
                        if chunk: f.write(chunk)
            
            if self.is_running:
                self.log(f"✅ Скачано: {final_filename}")
            else:
                self.log("🛑 Загрузка прервана")
                if os.path.exists(local_path): os.remove(local_path)

        except Exception as e:
            self.log(f"❌ Ошибка скачивания: {e}")

    def start_download(self, links_file, save_dir, version):
        self.is_running = True
        try:
            with open(links_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception as e:
            self.log(f"❌ Ошибка файла: {e}")
            self.finish()
            return

        session = requests.Session()
        session.headers.update(HEADERS)
        total = len(lines)
        
        self.log(f"🚀 Старт! Файлов: {total}")
        self.log(f"📂 Папка: {save_dir}")

        for i, raw_link in enumerate(lines, 1):
            if not self.is_running: break

            url = self.clean_url(raw_link)
            if not url: continue

            self.log(f"\n[{i}/{total}] {url}")
            self.update_progress(i, total)

            # 1. Данные галереи
            res = self.get_gallery_id(url, session)
            if not res: continue
            gal_id, gal_name = res
            
            # 2. Автор
            author = self.extract_author(url)

            # 3. Ссылка
            dl_link = self.get_download_link(url, gal_id, version, session)
            
            if dl_link:
                # Очистка имени галереи от плохих символов
                safe_gal_name = re.sub(r'[<>:"/\\|?*]', '_', gal_name)
                
                # Формируем имя: [author]_Name_ver.zip
                base_filename = f"[{author}]_{safe_gal_name}_{version}.zip"
                
                self.download_file(dl_link, save_dir, base_filename)
            else:
                self.log("❌ Ссылка на архив не получена")

        self.log("\n🏁 Готово!")
        self.finish()


# --- GUI ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("VIGBO Downloader v3.0 (Auto-Naming)")
        self.root.geometry("600x550")

        self.file_path = tk.StringVar()
        self.save_dir = tk.StringVar(value=os.getcwd())
        self.version_var = tk.StringVar(value="web")

        self.logic = DownloaderLogic(self.log_msg, self.update_bar, self.on_finish)

        # UI Elements
        frame_main = tk.Frame(root, padx=10, pady=10)
        frame_main.pack(fill="both", expand=True)

        # 1. Файл
        fr1 = tk.LabelFrame(frame_main, text="1. Список ссылок (.txt)")
        fr1.pack(fill="x", pady=5)
        tk.Entry(fr1, textvariable=self.file_path).pack(side="left", fill="x", expand=True, padx=5, pady=5)
        tk.Button(fr1, text="...", width=3, command=self.browse_file).pack(side="right", padx=5)

        # 2. Папка
        fr2 = tk.LabelFrame(frame_main, text="2. Куда сохранять")
        fr2.pack(fill="x", pady=5)
        tk.Entry(fr2, textvariable=self.save_dir).pack(side="left", fill="x", expand=True, padx=5, pady=5)
        tk.Button(fr2, text="...", width=3, command=self.browse_dir).pack(side="right", padx=5)

        # 3. Версия
        fr3 = tk.LabelFrame(frame_main, text="3. Режим")
        fr3.pack(fill="x", pady=5)
        tk.Radiobutton(fr3, text="WEB (Легкий)", variable=self.version_var, value="web").pack(side="left", padx=10)
        tk.Radiobutton(fr3, text="ORIGINAL (Тяжелый)", variable=self.version_var, value="original").pack(side="left", padx=10)

        # Кнопки
        fr_btns = tk.Frame(frame_main)
        fr_btns.pack(fill="x", pady=10)
        self.btn_start = tk.Button(fr_btns, text="START", bg="#27ae60", fg="white", font=("Arial", 10, "bold"), command=self.start_thread)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=2)
        self.btn_stop = tk.Button(fr_btns, text="STOP", bg="#c0392b", fg="white", font=("Arial", 10, "bold"), command=self.stop_process, state="disabled")
        self.btn_stop.pack(side="right", padx=2)

        # Прогресс
        self.progress = ttk.Progressbar(frame_main, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=5)

        # Лог
        tk.Label(frame_main, text="Лог:").pack(anchor="w")
        self.text_log = tk.Text(frame_main, height=12, font=("Consolas", 9))
        self.text_log.pack(fill="both", expand=True)
        sb = tk.Scrollbar(self.text_log, command=self.text_log.yview)
        sb.pack(side="right", fill="y")
        self.text_log.config(yscrollcommand=sb.set)

    def browse_file(self):
        f = filedialog.askopenfilename(filetypes=[("Text", "*.txt")])
        if f: self.file_path.set(f)

    def browse_dir(self):
        d = filedialog.askdirectory()
        if d: self.save_dir.set(d)

    def log_msg(self, msg):
        self.text_log.insert(tk.END, msg + "\n")
        self.text_log.see(tk.END)

    def update_bar(self, cur, tot):
        self.progress["maximum"] = tot
        self.progress["value"] = cur

    def start_thread(self):
        f, d = self.file_path.get(), self.save_dir.get()
        if not f: return messagebox.showerror("Error", "Выберите файл!")
        self.btn_start["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self.text_log.delete(1.0, tk.END)
        self.progress["value"] = 0
        threading.Thread(target=self.logic.start_download, args=(f, d, self.version_var.get()), daemon=True).start()

    def stop_process(self):
        self.logic.is_running = False
        self.log_msg("\n🛑 Остановка...")

    def on_finish(self):
        self.btn_start["state"] = "normal"
        self.btn_stop["state"] = "disabled"
        messagebox.showinfo("Done", "Готово!")

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
