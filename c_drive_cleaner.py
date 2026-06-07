import os
import queue
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import BooleanVar, Tk, messagebox, ttk
import tkinter as tk


APP_NAME = "C 盘安全清理工具"
BLOCK_SIZE = 64 * 1024
FONT_FAMILY = "Microsoft YaHei UI"
APP_BG = "#f4f6f8"
PANEL_BG = "#ffffff"
SOFT_BG = "#f8fafc"
BORDER = "#d9e0e7"
TEXT = "#1f2933"
MUTED = "#667085"
PRIMARY = "#2563eb"
DANGER = "#b42318"


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def path_from_env(name: str, *parts: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).joinpath(*parts)


@dataclass(frozen=True)
class CleanupRule:
    key: str
    name: str
    description: str
    paths: tuple[Path, ...]
    browser_related: bool = False


@dataclass
class ScanResult:
    rule: CleanupRule
    files: list[Path] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)
    size: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class CleanResult:
    removed_files: int = 0
    removed_dirs: int = 0
    removed_size: int = 0
    failures: list[str] = field(default_factory=list)


def unique_existing_paths(paths: list[Path | None]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        if path is None:
            continue
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path.expanduser().absolute()
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        result.append(resolved)
    return tuple(remove_nested_paths(result))


def remove_nested_paths(paths: list[Path]) -> list[Path]:
    ordered = sorted(paths, key=lambda candidate: len(candidate.parts))
    kept: list[Path] = []
    for path in ordered:
        if any(path == parent or path.is_relative_to(parent) for parent in kept):
            continue
        kept.append(path)
    return kept


def build_rules() -> list[CleanupRule]:
    home = Path.home()
    local_app_data = path_from_env("LOCALAPPDATA")
    app_data = path_from_env("APPDATA")
    temp = Path(os.environ.get("TEMP", home / "AppData" / "Local" / "Temp"))

    edge_base = path_from_env("LOCALAPPDATA", "Microsoft", "Edge", "User Data")
    chrome_base = path_from_env("LOCALAPPDATA", "Google", "Chrome", "User Data")
    firefox_base = path_from_env("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles")

    rules = [
        CleanupRule(
            key="user_temp",
            name="用户临时文件",
            description="当前用户 TEMP 目录中的临时文件。",
            paths=unique_existing_paths([temp]),
        ),
        CleanupRule(
            key="windows_user_cache",
            name="Windows 用户缓存",
            description="INetCache、WER 报告、Delivery Optimization 等用户级缓存。",
            paths=unique_existing_paths(
                [
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "INetCache"),
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "WebCache"),
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "WER", "ReportArchive"),
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "WER", "ReportQueue"),
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "DeliveryOptimization", "Cache"),
                ]
            ),
        ),
        CleanupRule(
            key="thumbnail_cache",
            name="缩略图缓存",
            description="资源管理器缩略图数据库缓存。",
            paths=unique_existing_paths(
                [
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "Explorer"),
                ]
            ),
        ),
        CleanupRule(
            key="log_cache",
            name="应用日志缓存",
            description="常见用户级应用日志和崩溃报告缓存。",
            paths=unique_existing_paths(
                [
                    path_from_env("LOCALAPPDATA", "CrashDumps"),
                    path_from_env("LOCALAPPDATA", "Microsoft", "Windows", "PowerShell", "StartupProfileData-NonInteractive"),
                    app_data.joinpath("Microsoft", "Windows", "Recent") if app_data else None,
                ]
            ),
        ),
        CleanupRule(
            key="edge_cache",
            name="Microsoft Edge 缓存",
            description="Edge 浏览器缓存。建议清理前关闭浏览器。",
            paths=browser_cache_paths(edge_base),
            browser_related=True,
        ),
        CleanupRule(
            key="chrome_cache",
            name="Google Chrome 缓存",
            description="Chrome 浏览器缓存。建议清理前关闭浏览器。",
            paths=browser_cache_paths(chrome_base),
            browser_related=True,
        ),
        CleanupRule(
            key="firefox_cache",
            name="Mozilla Firefox 缓存",
            description="Firefox 浏览器缓存。建议清理前关闭浏览器。",
            paths=firefox_cache_paths(firefox_base),
            browser_related=True,
        ),
    ]
    return [rule for rule in rules if rule.paths]


def browser_cache_paths(base: Path | None) -> tuple[Path, ...]:
    if not base or not base.exists():
        return tuple()

    candidates: list[Path] = []
    profile_names = [
        "Default",
        "Profile 1",
        "Profile 2",
        "Profile 3",
        "Guest Profile",
        "System Profile",
    ]
    cache_parts = [
        ("Cache",),
        ("Cache", "Cache_Data"),
        ("Code Cache",),
        ("GPUCache",),
        ("GrShaderCache",),
        ("ShaderCache",),
        ("Service Worker", "CacheStorage"),
        ("Service Worker", "ScriptCache"),
        ("Media Cache",),
    ]

    for profile in profile_names:
        profile_path = base / profile
        for parts in cache_parts:
            candidates.append(profile_path.joinpath(*parts))

    return unique_existing_paths(candidates)


def firefox_cache_paths(base: Path | None) -> tuple[Path, ...]:
    if not base or not base.exists():
        return tuple()

    candidates: list[Path] = []
    for profile in base.iterdir():
        if profile.is_dir():
            candidates.extend(
                [
                    profile / "cache2",
                    profile / "startupCache",
                    profile / "jumpListCache",
                    profile / "thumbnails",
                ]
            )
    return unique_existing_paths(candidates)


def should_skip_file(path: Path) -> bool:
    name = path.name.lower()
    if name in {"desktop.ini", "thumbs.db"}:
        return False
    return False


def scan_rule(rule: CleanupRule) -> ScanResult:
    result = ScanResult(rule=rule)

    for root_path in rule.paths:
        if not root_path.exists():
            continue

        if root_path.is_file():
            try:
                if not should_skip_file(root_path):
                    result.size += root_path.stat().st_size
                    result.files.append(root_path)
            except OSError as exc:
                result.errors.append(f"{root_path}: {exc}")
            continue

        for current_root, dir_names, file_names in os.walk(root_path, topdown=True, onerror=None):
            current = Path(current_root)
            result.directories.append(current)

            accessible_dirs: list[str] = []
            for dir_name in dir_names:
                child = current / dir_name
                try:
                    child.stat()
                except OSError as exc:
                    result.errors.append(f"{child}: {exc}")
                    continue
                accessible_dirs.append(dir_name)
            dir_names[:] = accessible_dirs

            for file_name in file_names:
                file_path = current / file_name
                if should_skip_file(file_path):
                    continue
                try:
                    result.size += file_path.stat().st_size
                    result.files.append(file_path)
                except OSError as exc:
                    result.errors.append(f"{file_path}: {exc}")

    result.directories = sorted(set(result.directories), key=lambda p: len(str(p)), reverse=True)
    return result


def scan_all_rules(rules: list[CleanupRule], progress_callback=None) -> dict[str, ScanResult]:
    results: dict[str, ScanResult] = {}
    for index, rule in enumerate(rules, start=1):
        if progress_callback:
            progress_callback(f"正在扫描 {rule.name} ({index}/{len(rules)})...")
        results[rule.key] = scan_rule(rule)
    if progress_callback:
        progress_callback("扫描完成。")
    return results


def remove_file(path: Path) -> int:
    size = 0
    try:
        size = path.stat().st_size
    except OSError:
        pass
    path.unlink()
    return size


def clean_results(results: list[ScanResult], progress_callback=None) -> CleanResult:
    outcome = CleanResult()
    total_files = sum(len(result.files) for result in results)
    processed = 0

    for result in results:
        protected_roots = {os.path.normcase(str(path)) for path in result.rule.paths}
        if progress_callback:
            progress_callback(f"正在清理 {result.rule.name}...")

        for file_path in result.files:
            processed += 1
            try:
                removed_size = remove_file(file_path)
                outcome.removed_files += 1
                outcome.removed_size += removed_size
            except FileNotFoundError:
                continue
            except OSError as exc:
                outcome.failures.append(f"{file_path}: {exc}")

            if progress_callback and processed % 200 == 0:
                progress_callback(f"已处理 {processed}/{total_files} 个文件...")

        for directory in result.directories:
            if os.path.normcase(str(directory)) in protected_roots:
                continue
            try:
                directory.rmdir()
                outcome.removed_dirs += 1
            except FileNotFoundError:
                continue
            except OSError:
                pass

    if progress_callback:
        progress_callback("清理完成。")
    return outcome


def disk_status() -> str:
    usage = shutil.disk_usage("C:\\")
    return (
        f"C 盘剩余：{format_size(usage.free)} / 总容量：{format_size(usage.total)} "
        f"(已用 {format_size(usage.used)})"
    )


class CleanerApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1080x720")
        self.root.minsize(940, 620)
        self.root.configure(bg=APP_BG)

        self.rules = build_rules()
        self.results: dict[str, ScanResult] = {}
        self.check_vars: dict[str, BooleanVar] = {}
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.status_var = tk.StringVar(value=disk_status())
        self.total_var = tk.StringVar(value="可清理：等待扫描")
        self.summary_size_var = tk.StringVar(value="等待扫描")
        self.summary_files_var = tk.StringVar(value="-")
        self.summary_status_var = tk.StringVar(value="准备就绪")

        self.configure_style()
        self.build_ui()
        self.set_busy(False)
        self.root.after(100, self.process_worker_queue)

    def configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.option_add("*Font", (FONT_FAMILY, 10))
        style.configure(".", background=APP_BG, foreground=TEXT, font=(FONT_FAMILY, 10))
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Panel.TFrame", background=PANEL_BG, relief="solid", borderwidth=1)
        style.configure("Soft.TFrame", background=SOFT_BG)
        style.configure("Title.TLabel", background=APP_BG, foreground=TEXT, font=(FONT_FAMILY, 18, "bold"))
        style.configure("Subtitle.TLabel", background=APP_BG, foreground=MUTED, font=(FONT_FAMILY, 10))
        style.configure("PanelTitle.TLabel", background=PANEL_BG, foreground=TEXT, font=(FONT_FAMILY, 11, "bold"))
        style.configure("Hint.TLabel", background=PANEL_BG, foreground=MUTED, font=(FONT_FAMILY, 9))
        style.configure("Metric.TLabel", background=PANEL_BG, foreground=TEXT, font=(FONT_FAMILY, 16, "bold"))
        style.configure("MetricHint.TLabel", background=PANEL_BG, foreground=MUTED, font=(FONT_FAMILY, 9))
        style.configure("Status.TLabel", background=SOFT_BG, foreground=TEXT, font=(FONT_FAMILY, 10))
        style.configure("TButton", padding=(14, 7), font=(FONT_FAMILY, 10))
        style.configure("Primary.TButton", padding=(16, 8), foreground="#ffffff", background=PRIMARY)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#9ca3af")])
        style.configure("Danger.TButton", padding=(16, 8), foreground=DANGER)
        style.configure(
            "Treeview",
            background=PANEL_BG,
            fieldbackground=PANEL_BG,
            foreground=TEXT,
            rowheight=34,
            borderwidth=0,
            font=(FONT_FAMILY, 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#edf2f7",
            foreground=TEXT,
            font=(FONT_FAMILY, 10, "bold"),
            padding=(8, 8),
        )
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", TEXT)])
        style.configure("Horizontal.TProgressbar", troughcolor="#e5e7eb", background=PRIMARY)

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(22, 18, 22, 10), style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="先扫描、再预览、最后确认清理。默认只处理用户级缓存和临时文件。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(header, style="App.TFrame")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self.scan_button = ttk.Button(actions, text="扫描", command=self.start_scan, style="Primary.TButton")
        self.scan_button.grid(row=0, column=0, padx=(0, 8))
        self.clean_button = ttk.Button(actions, text="清理所选", command=self.confirm_clean, style="Danger.TButton")
        self.clean_button.grid(row=0, column=1)

        main = ttk.Frame(self.root, padding=(22, 8, 22, 18), style="App.TFrame")
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=5)
        main.columnconfigure(1, weight=3)
        main.rowconfigure(1, weight=1)

        summary = ttk.Frame(main, style="App.TFrame")
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        for column in range(3):
            summary.columnconfigure(column, weight=1, uniform="summary")

        self.create_metric_card(summary, 0, "预计可清理", self.summary_size_var, self.total_var)
        self.create_metric_card(summary, 1, "发现文件", self.summary_files_var, self.summary_status_var)
        self.create_metric_card(summary, 2, "磁盘状态", self.status_var, tk.StringVar(value="清理后会自动刷新"))

        list_frame = self.create_panel(main, "清理项目", "勾选要处理的分类，点击左侧列可切换选中状态。")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)

        columns = ("selected", "name", "size", "files", "paths")
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=15,
        )
        self.tree.heading("selected", text="选中")
        self.tree.heading("name", text="分类")
        self.tree.heading("size", text="大小")
        self.tree.heading("files", text="文件数")
        self.tree.heading("paths", text="路径数")
        self.tree.column("selected", width=70, minwidth=62, anchor="center", stretch=False)
        self.tree.column("name", width=240, minwidth=180)
        self.tree.column("size", width=120, minwidth=100, anchor="e", stretch=False)
        self.tree.column("files", width=92, minwidth=78, anchor="e", stretch=False)
        self.tree.column("paths", width=92, minwidth=78, anchor="e", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Button-1>", self.handle_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_details)
        self.tree.tag_configure("odd", background="#fbfdff")
        self.tree.tag_configure("even", background=PANEL_BG)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.tree.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(list_frame, style="Panel.TFrame")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.total_var, style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="全选", command=lambda: self.set_all_checked(True)).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(footer, text="全不选", command=lambda: self.set_all_checked(False)).grid(row=0, column=2, padx=(8, 0))

        side = ttk.Frame(main, style="App.TFrame")
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)

        detail_frame = self.create_panel(side, "路径预览", "显示当前分类会扫描的目录和统计结果。")
        detail_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        self.detail_text = self.create_text_area(detail_frame)
        self.detail_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        log_frame = self.create_panel(side, "运行日志", "扫描和清理过程中的提示会显示在这里。")
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        self.log_text = self.create_text_area(log_frame)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 12))

        if not self.rules:
            self.log("未发现可扫描的用户级缓存目录。")
        else:
            self.populate_empty_rules()
            self.log("准备就绪。点击“扫描”开始统计可清理内容。")

    def create_panel(self, parent, title: str, hint: str) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=(14, 12), style="Panel.TFrame")
        ttk.Label(frame, text=title, style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=hint, style="Hint.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))
        frame.columnconfigure(0, weight=1)
        return frame

    def create_metric_card(self, parent, column: int, label: str, value_var, hint_var) -> None:
        card = ttk.Frame(parent, padding=(14, 12), style="Panel.TFrame")
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=label, style="MetricHint.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, textvariable=value_var, style="Metric.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 2))
        ttk.Label(card, textvariable=hint_var, style="Hint.TLabel").grid(row=2, column=0, sticky="w")

    def create_text_area(self, parent) -> tk.Text:
        text = tk.Text(
            parent,
            height=10,
            wrap="word",
            state="disabled",
            bg=SOFT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=10,
            pady=8,
            font=(FONT_FAMILY, 9),
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        text.configure(yscrollcommand=scrollbar.set)
        return text

    def populate_empty_rules(self) -> None:
        for index, rule in enumerate(self.rules):
            self.check_vars[rule.key] = BooleanVar(value=True)
            self.tree.insert(
                "",
                "end",
                iid=rule.key,
                values=("✓", rule.name, "未扫描", "-", str(len(rule.paths))),
                tags=("even" if index % 2 == 0 else "odd",),
            )

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.scan_button.configure(state=state)
        self.clean_button.configure(state=state if self.results else "disabled")
        if busy:
            self.summary_status_var.set("任务运行中")
            self.progress.start(12)
        else:
            self.progress.stop()

    def start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.results.clear()
        self.detail_text_set("")
        self.summary_size_var.set("扫描中")
        self.summary_files_var.set("-")
        self.summary_status_var.set("正在统计缓存")
        self.total_var.set("可清理：扫描中")
        self.log("开始扫描。")
        self.set_busy(True)
        self.worker = threading.Thread(target=self.scan_worker, daemon=True)
        self.worker.start()

    def scan_worker(self) -> None:
        try:
            results = scan_all_rules(self.rules, self.queue_log)
            self.worker_queue.put(("scan_done", results))
        except Exception as exc:
            self.worker_queue.put(("error", f"扫描失败：{exc}"))

    def confirm_clean(self) -> None:
        selected = self.selected_results()
        if not selected:
            messagebox.showinfo(APP_NAME, "没有选择可清理项目。")
            return

        has_browser = any(result.rule.browser_related for result in selected)
        total_size = sum(result.size for result in selected)
        total_files = sum(result.file_count for result in selected)
        browser_hint = "\n\n包含浏览器缓存，建议先关闭 Edge/Chrome/Firefox。" if has_browser else ""
        confirmed = messagebox.askyesno(
            APP_NAME,
            (
                f"将清理 {len(selected)} 个分类，共 {format_size(total_size)}，"
                f"{total_files} 个文件。{browser_hint}\n\n确定继续吗？"
            ),
        )
        if not confirmed:
            return

        self.log("开始清理所选项目。")
        self.set_busy(True)
        self.worker = threading.Thread(target=self.clean_worker, args=(selected,), daemon=True)
        self.worker.start()

    def clean_worker(self, selected: list[ScanResult]) -> None:
        try:
            outcome = clean_results(selected, self.queue_log)
            self.worker_queue.put(("clean_done", outcome))
        except Exception as exc:
            self.worker_queue.put(("error", f"清理失败：{exc}"))

    def selected_results(self) -> list[ScanResult]:
        selected: list[ScanResult] = []
        for key, result in self.results.items():
            if self.check_vars.get(key, BooleanVar(value=False)).get() and result.file_count > 0:
                selected.append(result)
        return selected

    def process_worker_queue(self) -> None:
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "log":
                    self.log(str(payload))
                elif kind == "scan_done":
                    self.handle_scan_done(payload)  # type: ignore[arg-type]
                elif kind == "clean_done":
                    self.handle_clean_done(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self.log(str(payload))
                    messagebox.showerror(APP_NAME, str(payload))
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self.process_worker_queue)

    def queue_log(self, message: str) -> None:
        self.worker_queue.put(("log", message))

    def handle_scan_done(self, results: dict[str, ScanResult]) -> None:
        self.results = results
        total_size = sum(result.size for result in results.values())
        total_files = sum(result.file_count for result in results.values())
        self.total_var.set(f"可清理：{format_size(total_size)}，{total_files} 个文件")
        self.summary_size_var.set(format_size(total_size))
        self.summary_files_var.set(f"{total_files} 个")
        self.summary_status_var.set(f"{len(results)} 个分类已扫描")

        for rule in self.rules:
            result = results.get(rule.key)
            if not result:
                continue
            self.tree.item(
                rule.key,
                values=(
                    "✓" if self.check_vars[rule.key].get() else "",
                    rule.name,
                    format_size(result.size),
                    str(result.file_count),
                    str(len(rule.paths)),
                ),
            )
            if result.errors:
                self.log(f"{rule.name} 有 {len(result.errors)} 个扫描失败项。")

        self.status_var.set(disk_status())
        self.set_busy(False)
        self.clean_button.configure(state="normal" if self.selected_results() else "disabled")
        self.show_selected_details()
        self.log(f"扫描完成：共发现 {format_size(total_size)} 可清理内容。")

    def handle_clean_done(self, outcome: CleanResult) -> None:
        self.status_var.set(disk_status())
        self.set_busy(False)
        self.log(
            "清理完成："
            f"删除 {outcome.removed_files} 个文件、{outcome.removed_dirs} 个空目录，"
            f"释放 {format_size(outcome.removed_size)}。"
        )
        self.summary_size_var.set(format_size(outcome.removed_size))
        self.summary_files_var.set(f"{outcome.removed_files} 个")
        self.summary_status_var.set("刚刚完成清理")
        if outcome.failures:
            self.log(f"有 {len(outcome.failures)} 个文件未能删除，通常是权限不足或正在使用。")
            for failure in outcome.failures[:20]:
                self.log(f"失败：{failure}")
            if len(outcome.failures) > 20:
                self.log(f"还有 {len(outcome.failures) - 20} 个失败项未显示。")
        messagebox.showinfo(
            APP_NAME,
            (
                f"清理完成。\n\n释放空间：{format_size(outcome.removed_size)}\n"
                f"删除文件：{outcome.removed_files}\n失败文件：{len(outcome.failures)}"
            ),
        )
        self.start_scan()

    def handle_tree_click(self, event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)
        if region == "cell" and column == "#1" and item_id:
            var = self.check_vars.get(item_id)
            if var is not None:
                var.set(not var.get())
                values = list(self.tree.item(item_id, "values"))
                values[0] = "✓" if var.get() else ""
                self.tree.item(item_id, values=values)
                self.update_selected_total()
                self.clean_button.configure(state="normal" if self.selected_results() else "disabled")

    def set_all_checked(self, checked: bool) -> None:
        for key, var in self.check_vars.items():
            var.set(checked)
            values = list(self.tree.item(key, "values"))
            values[0] = "✓" if checked else ""
            self.tree.item(key, values=values)
        self.update_selected_total()
        self.clean_button.configure(state="normal" if self.selected_results() else "disabled")

    def update_selected_total(self) -> None:
        if not self.results:
            return
        selected = self.selected_results()
        total_size = sum(result.size for result in selected)
        total_files = sum(result.file_count for result in selected)
        self.total_var.set(f"已选：{format_size(total_size)}，{total_files} 个文件")
        self.summary_size_var.set(format_size(total_size))
        self.summary_files_var.set(f"{total_files} 个")
        self.summary_status_var.set(f"已选 {len(selected)} 个分类")

    def show_selected_details(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                selected = self.tree.selection()
        if not selected:
            return
        key = selected[0]
        rule = next((candidate for candidate in self.rules if candidate.key == key), None)
        if not rule:
            return

        result = self.results.get(key)
        lines = [rule.description, "", "扫描路径："]
        lines.extend(f"- {path}" for path in rule.paths)
        if result:
            lines.extend(
                [
                    "",
                    f"预计大小：{format_size(result.size)}",
                    f"文件数量：{result.file_count}",
                    f"扫描失败：{len(result.errors)}",
                ]
            )
            if result.errors:
                lines.append("")
                lines.append("前 10 个失败项：")
                lines.extend(f"- {error}" for error in result.errors[:10])
        self.detail_text_set("\n".join(lines))

    def detail_text_set(self, text: str) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> None:
    root = Tk()
    CleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
