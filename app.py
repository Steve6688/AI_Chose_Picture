from __future__ import annotations

import csv
import json
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from scoring import PhotoScore, analyze_photo, find_jpgs, mark_duplicates


DEFAULT_FOLDER = r"D:\File\摄影记录\深圳的奇妙之旅\老婆\101_FUJI_2609"
DB_NAME = ".travel_photo_ratings.json"
BG = "#F5F2EA"
PANEL = "#FFFFFF"
INK = "#20211D"
MUTED = "#77766E"
ACCENT = "#E56B3E"
GREEN = "#315B4A"
LINE = "#E4DFD3"


class TravelPhotoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("旅途拾光 · JPG 智能筛选")
        self.geometry("1400x860")
        self.minsize(1080, 700)
        self.configure(bg=BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.folder = tk.StringVar(value=DEFAULT_FOLDER)
        self.search = tk.StringVar()
        self.filter_mode = tk.StringVar(value="全部照片")
        self.sort_mode = tk.StringVar(value="评分从高到低")
        self.items: list[PhotoScore] = []
        self.cards: list[tk.Widget] = []
        self.thumb_refs: list[ImageTk.PhotoImage] = []
        self.preview_ref: ImageTk.PhotoImage | None = None
        self.selected: PhotoScore | None = None
        self.worker_queue: queue.Queue = queue.Queue()
        self.is_analyzing = False
        self._resize_job = None
        self._build_styles()
        self._build_ui()
        self.search.trace_add("write", lambda *_: self.render_gallery())
        self.after(120, self.load_existing)
        self.after(100, self._poll_worker)

    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor="#E8E2D7", background=ACCENT, borderwidth=0)
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, bordercolor=LINE,
                        arrowcolor=INK, padding=6)

    def _build_ui(self):
        header = tk.Frame(self, bg=BG, padx=26, pady=18)
        header.pack(fill="x")
        titlebox = tk.Frame(header, bg=BG)
        titlebox.pack(side="left")
        tk.Label(titlebox, text="旅途拾光", font=("Microsoft YaHei UI", 23, "bold"), bg=BG, fg=INK).pack(anchor="w")
        tk.Label(titlebox, text="让每一次快门，都找到它应有的位置", bg=BG, fg=MUTED).pack(anchor="w")

        tk.Button(header, text="导出精选", command=self.export_selected, bg=GREEN, fg="white",
                  activebackground="#244639", activeforeground="white", relief="flat", padx=20, pady=10,
                  cursor="hand2").pack(side="right", padx=(10, 0))
        self.analyze_btn = tk.Button(header, text="✦  开始 AI 分析", command=self.start_analysis,
                                     bg=ACCENT, fg="white", activebackground="#C95731",
                                     activeforeground="white", relief="flat", padx=20, pady=10,
                                     cursor="hand2")
        self.analyze_btn.pack(side="right")

        folderbar = tk.Frame(self, bg=PANEL, padx=24, pady=12, highlightbackground=LINE, highlightthickness=1)
        folderbar.pack(fill="x", padx=26, pady=(0, 14))
        tk.Label(folderbar, text="照片目录", bg=PANEL, fg=MUTED).pack(side="left")
        tk.Entry(folderbar, textvariable=self.folder, relief="flat", bg="#F8F6F0", fg=INK,
                 readonlybackground="#F8F6F0", font=("Microsoft YaHei UI", 10), bd=0).pack(
                     side="left", fill="x", expand=True, padx=14, ipady=8)
        tk.Button(folderbar, text="选择文件夹", command=self.choose_folder, bg=PANEL, fg=GREEN,
                  activebackground=BG, relief="flat", cursor="hand2").pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=26, pady=(0, 24))
        self._build_sidebar(body)
        self._build_content(body)
        self._build_details(body)

    def _build_sidebar(self, parent):
        side = tk.Frame(parent, bg=PANEL, width=205, padx=16, pady=18, highlightbackground=LINE, highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="照片库", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 12))
        self.filter_buttons = {}
        for label in ["全部照片", "★★★★★", "★★★★", "★★★", "低于 3 星", "❤  我的收藏", "相似/重复"]:
            btn = tk.Button(side, text=label, command=lambda x=label: self.set_filter(x), anchor="w",
                            bg=PANEL, fg=INK, activebackground="#F1EAE0", activeforeground=INK,
                            relief="flat", padx=10, pady=8, cursor="hand2")
            btn.pack(fill="x", pady=1)
            self.filter_buttons[label] = btn
        tk.Frame(side, height=1, bg=LINE).pack(fill="x", pady=16)
        tk.Label(side, text="评分概览", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.stat_labels = {}
        for star in range(5, 0, -1):
            row = tk.Frame(side, bg=PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(row, text="★" * star, bg=PANEL, fg="#E7A23B", font=("Segoe UI Symbol", 8)).pack(side="left")
            label = tk.Label(row, text="0", bg=PANEL, fg=MUTED)
            label.pack(side="right")
            self.stat_labels[star] = label
        self.total_label = tk.Label(side, text="尚未分析照片", bg=PANEL, fg=MUTED, wraplength=170, justify="left")
        self.total_label.pack(side="bottom", anchor="w")

    def _build_content(self, parent):
        center = tk.Frame(parent, bg=BG)
        center.pack(side="left", fill="both", expand=True, padx=14)
        toolbar = tk.Frame(center, bg=BG)
        toolbar.pack(fill="x", pady=(0, 10))
        search_entry = tk.Entry(toolbar, textvariable=self.search, bg=PANEL, fg=INK, relief="flat",
                                highlightbackground=LINE, highlightthickness=1)
        search_entry.pack(side="left", fill="x", expand=True, ipady=8)
        search_entry.insert(0, "")
        sort = ttk.Combobox(toolbar, textvariable=self.sort_mode, values=["评分从高到低", "评分从低到高", "文件名", "拍摄时间"],
                            state="readonly", width=17)
        sort.pack(side="right", padx=(10, 0))
        sort.bind("<<ComboboxSelected>>", lambda _e: self.render_gallery())

        canvas_frame = tk.Frame(center, bg=BG)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.gallery = tk.Frame(self.canvas, bg=BG)
        self.gallery_window = self.canvas.create_window((0, 0), window=self.gallery, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.gallery.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.empty = tk.Label(self.gallery, text="这里将展示你的旅途照片\n\n选择目录后点击「开始 AI 分析」",
                              bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 13), justify="center")
        self.empty.grid(row=0, column=0, pady=160)
        self.progress_frame = tk.Frame(center, bg=BG)
        self.progress_label = tk.Label(self.progress_frame, text="", bg=BG, fg=MUTED)
        self.progress_label.pack(anchor="w")
        self.progress = ttk.Progressbar(self.progress_frame, mode="determinate")
        self.progress.pack(fill="x", pady=(5, 0))

    def _build_details(self, parent):
        self.detail = tk.Frame(parent, bg=PANEL, width=270, padx=18, pady=18, highlightbackground=LINE, highlightthickness=1)
        self.detail.pack(side="right", fill="y")
        self.detail.pack_propagate(False)
        tk.Label(self.detail, text="照片详情", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self.preview = tk.Label(self.detail, text="点击照片查看详情", bg="#EEEAE1", fg=MUTED, width=28, height=10)
        self.preview.pack(fill="x", pady=16)
        self.detail_name = tk.Label(self.detail, text="—", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 11, "bold"),
                                    wraplength=230, justify="left")
        self.detail_name.pack(anchor="w")
        self.detail_meta = tk.Label(self.detail, text="", bg=PANEL, fg=MUTED, justify="left")
        self.detail_meta.pack(anchor="w", pady=(4, 12))
        self.manual_stars = tk.Frame(self.detail, bg=PANEL)
        self.manual_stars.pack(anchor="w")
        self.star_buttons = []
        for star in range(1, 6):
            b = tk.Button(self.manual_stars, text="☆", font=("Segoe UI Symbol", 18), bg=PANEL, fg="#D79A37",
                          relief="flat", padx=1, command=lambda n=star: self.set_manual_star(n), cursor="hand2")
            b.pack(side="left")
            self.star_buttons.append(b)
        self.favorite_btn = tk.Button(self.detail, text="♡  加入收藏", command=self.toggle_favorite, bg="#F5F1E9", fg=INK,
                                      relief="flat", pady=8, cursor="hand2", state="disabled")
        self.favorite_btn.pack(fill="x", pady=12)
        tk.Label(self.detail, text="AI 评估", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(4, 4))
        self.metric_frame = tk.Frame(self.detail, bg=PANEL)
        self.metric_frame.pack(fill="x")
        self.metric_labels = {}
        for key, label in [("sharpness", "清晰度"), ("exposure", "曝光"), ("contrast", "层次"), ("color", "色彩"), ("composition", "构图")]:
            row = tk.Frame(self.metric_frame, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=PANEL, fg=MUTED).pack(side="left")
            value = tk.Label(row, text="—", bg=PANEL, fg=INK)
            value.pack(side="right")
            self.metric_labels[key] = value
        self.note_label = tk.Label(self.detail, text="", bg=PANEL, fg=GREEN, wraplength=230, justify="left")
        self.note_label.pack(anchor="w", pady=12)
        tk.Button(self.detail, text="在资源管理器中查看", command=self.reveal_selected, bg=PANEL, fg=GREEN,
                  relief="flat", cursor="hand2").pack(side="bottom")

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.folder.get() if Path(self.folder.get()).exists() else None)
        if selected:
            self.folder.set(selected)
            self.load_existing()

    def database_path(self) -> Path:
        return Path(self.folder.get()) / DB_NAME

    def load_existing(self):
        self.items = []
        db = self.database_path()
        if db.exists():
            try:
                payload = json.loads(db.read_text(encoding="utf-8"))
                self.items = [PhotoScore(**item) for item in payload.get("photos", []) if Path(item["path"]).exists()]
            except (OSError, ValueError, TypeError):
                messagebox.showwarning("评分记录", "已有评分记录无法读取，可以重新分析生成。")
        self.render_gallery()
        self.update_stats()

    def save_database(self):
        try:
            payload = {"version": 1, "photos": [item.to_dict() for item in self.items]}
            self.database_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showwarning("保存失败", f"评分完成，但无法保存记录：\n{exc}")

    def start_analysis(self):
        folder = Path(self.folder.get())
        if not folder.is_dir():
            messagebox.showerror("目录不存在", "当前照片目录不存在。请点击“选择文件夹”，选择 JPG 所在目录。")
            return
        jpgs = find_jpgs(folder)
        if not jpgs:
            messagebox.showinfo("没有 JPG", "该目录及子目录中没有找到 .jpg 或 .jpeg 文件。视频及其他格式不会被分析。")
            return
        if self.is_analyzing:
            return
        self.is_analyzing = True
        self.analyze_btn.configure(state="disabled", text="正在分析…")
        self.progress_frame.pack(fill="x", pady=(10, 0))
        self.progress.configure(maximum=len(jpgs), value=0)
        old = {item.path: item for item in self.items}
        threading.Thread(target=self._analyze_worker, args=(jpgs, old), daemon=True).start()

    def _analyze_worker(self, paths: list[Path], old: dict[str, PhotoScore]):
        results = []
        errors = []
        for index, path in enumerate(paths, 1):
            try:
                cached = old.get(str(path))
                if cached and abs(cached.mtime - path.stat().st_mtime) < 0.001:
                    result = cached
                else:
                    result = analyze_photo(path)
                    if cached:
                        result.favorite = cached.favorite
                results.append(result)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
            self.worker_queue.put(("progress", index, len(paths), path.name))
        mark_duplicates(results)
        self.worker_queue.put(("done", results, errors))

    def _poll_worker(self):
        try:
            while True:
                message = self.worker_queue.get_nowait()
                if message[0] == "progress":
                    _, index, total, name = message
                    self.progress.configure(value=index)
                    self.progress_label.configure(text=f"正在分析 {index}/{total} · {name}")
                elif message[0] == "done":
                    _, self.items, errors = message
                    self.is_analyzing = False
                    self.analyze_btn.configure(state="normal", text="✦  重新 AI 分析")
                    self.progress_frame.pack_forget()
                    self.save_database()
                    self.render_gallery()
                    self.update_stats()
                    if errors:
                        messagebox.showwarning("分析完成", f"已完成 {len(self.items)} 张，另有 {len(errors)} 张无法读取。")
        except queue.Empty:
            pass
        self.after(100, self._poll_worker)

    def set_filter(self, mode: str):
        self.filter_mode.set(mode)
        for name, button in self.filter_buttons.items():
            button.configure(bg="#F1E6D9" if name == mode else PANEL, fg=ACCENT if name == mode else INK)
        self.render_gallery()

    def filtered_items(self) -> list[PhotoScore]:
        mode = self.filter_mode.get()
        query = self.search.get().strip().lower()
        items = [item for item in self.items if query in Path(item.path).name.lower()]
        if mode == "★★★★★": items = [x for x in items if x.stars == 5]
        elif mode == "★★★★": items = [x for x in items if x.stars == 4]
        elif mode == "★★★": items = [x for x in items if x.stars == 3]
        elif mode == "低于 3 星": items = [x for x in items if x.stars < 3]
        elif mode == "❤  我的收藏": items = [x for x in items if x.favorite]
        elif mode == "相似/重复": items = [x for x in items if x.duplicate]
        sort = self.sort_mode.get()
        if sort == "评分从高到低": items.sort(key=lambda x: x.score, reverse=True)
        elif sort == "评分从低到高": items.sort(key=lambda x: x.score)
        elif sort == "文件名": items.sort(key=lambda x: Path(x.path).name.lower())
        else: items.sort(key=lambda x: x.mtime, reverse=True)
        return items

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.gallery_window, width=event.width)
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(180, self.render_gallery)

    def render_gallery(self):
        for child in self.gallery.winfo_children():
            child.destroy()
        self.thumb_refs.clear()
        items = self.filtered_items()
        if not items:
            text = "暂无符合筛选条件的照片" if self.items else "这里将展示你的旅途照片\n\n选择目录后点击「开始 AI 分析」"
            tk.Label(self.gallery, text=text, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 13), justify="center").grid(
                row=0, column=0, pady=160, sticky="nsew")
            self.gallery.columnconfigure(0, weight=1)
            return
        width = max(self.canvas.winfo_width(), 500)
        columns = max(2, min(4, width // 230))
        for col in range(columns):
            self.gallery.columnconfigure(col, weight=1, uniform="cards")
        for index, item in enumerate(items):
            self._photo_card(item, index // columns, index % columns)

    def _photo_card(self, item: PhotoScore, row: int, col: int):
        card = tk.Frame(self.gallery, bg=PANEL, padx=8, pady=8, highlightbackground=LINE, highlightthickness=1, cursor="hand2")
        card.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
        try:
            with Image.open(item.path) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
                image.thumbnail((220, 138), Image.Resampling.LANCZOS)
                backdrop = Image.new("RGB", (220, 138), "#ECE9E1")
                backdrop.paste(image, ((220 - image.width) // 2, (138 - image.height) // 2))
            thumb = ImageTk.PhotoImage(backdrop)
            self.thumb_refs.append(thumb)
            photo = tk.Label(card, image=thumb, bg="#ECE9E1", cursor="hand2")
        except OSError:
            photo = tk.Label(card, text="无法预览", width=25, height=8, bg="#ECE9E1", fg=MUTED)
        photo.pack(fill="x")
        info = tk.Frame(card, bg=PANEL)
        info.pack(fill="x", pady=(7, 0))
        stars = "★" * item.stars + "☆" * (5 - item.stars)
        tk.Label(info, text=stars, bg=PANEL, fg="#E5A138", font=("Segoe UI Symbol", 10)).pack(side="left")
        flags = ("❤ " if item.favorite else "") + ("相似 " if item.duplicate else "") + f"{item.score:.0f}分"
        tk.Label(info, text=flags, bg=PANEL, fg=ACCENT if item.duplicate else MUTED, font=("Microsoft YaHei UI", 8)).pack(side="right")
        tk.Label(card, text=Path(item.path).name, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(3, 0))
        for widget in (card, photo, info):
            widget.bind("<Button-1>", lambda _e, selected=item: self.show_details(selected))

    def show_details(self, item: PhotoScore):
        self.selected = item
        try:
            with Image.open(item.path) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
                image.thumbnail((232, 190), Image.Resampling.LANCZOS)
            self.preview_ref = ImageTk.PhotoImage(image)
            self.preview.configure(image=self.preview_ref, text="", width=232, height=190)
        except OSError:
            self.preview.configure(image="", text="无法预览")
        self.detail_name.configure(text=Path(item.path).name)
        self.detail_meta.configure(text=f"{item.width} × {item.height}  ·  AI {item.score:.1f} 分")
        self.favorite_btn.configure(state="normal", text="♥  已收藏" if item.favorite else "♡  加入收藏",
                                    fg=ACCENT if item.favorite else INK)
        for i, button in enumerate(self.star_buttons, 1):
            button.configure(text="★" if i <= item.stars else "☆")
        for key, label in self.metric_labels.items():
            label.configure(text=f"{getattr(item, key):.0f}")
        self.note_label.configure(text=("相似照片中建议优先保留更高分的一张 · " if item.duplicate else "") + item.note)

    def set_manual_star(self, stars: int):
        if not self.selected:
            return
        self.selected.stars = stars
        self.selected.note = "已人工调整星级"
        self.save_database()
        self.show_details(self.selected)
        self.render_gallery()
        self.update_stats()

    def toggle_favorite(self):
        if not self.selected:
            return
        self.selected.favorite = not self.selected.favorite
        self.save_database()
        self.show_details(self.selected)
        self.render_gallery()

    def update_stats(self):
        for star, label in self.stat_labels.items():
            label.configure(text=str(sum(x.stars == star for x in self.items)))
        duplicate_count = sum(x.duplicate for x in self.items)
        self.total_label.configure(text=f"共 {len(self.items)} 张 JPG\n发现 {duplicate_count} 张相似照片")

    def export_selected(self):
        if not self.items:
            messagebox.showinfo("还没有结果", "请先完成照片分析。")
            return
        chosen = [x for x in self.items if x.favorite or x.stars >= 4]
        if not chosen:
            messagebox.showinfo("没有精选", "请收藏照片或把照片设为 4 星以上。")
            return
        destination = filedialog.askdirectory(title="选择精选照片的导出目录")
        if not destination:
            return
        target = Path(destination) / "旅途精选"
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        rows = []
        for item in chosen:
            source = Path(item.path)
            output = target / source.name
            counter = 2
            while output.exists() and output.stat().st_size != source.stat().st_size:
                output = target / f"{source.stem}_{counter}{source.suffix}"
                counter += 1
            if not output.exists():
                shutil.copy2(source, output)
            copied += 1
            rows.append([source.name, item.stars, item.score, "是" if item.favorite else "否", item.note])
        with (target / "评分清单.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["文件名", "星级", "AI分数", "收藏", "评语"])
            writer.writerows(rows)
        messagebox.showinfo("导出完成", f"已复制 {copied} 张照片到：\n{target}\n\n原照片未作改动。")

    def reveal_selected(self):
        if not self.selected:
            return
        path = Path(self.selected.path)
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            messagebox.showinfo("照片位置", str(path))


if __name__ == "__main__":
    TravelPhotoApp().mainloop()
