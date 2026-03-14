import json
import locale
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "共享数据集工具"
DEFAULT_LINK_NAME = "data"
APP_DIR = Path(__file__).resolve().parent
RECORD_FILE = APP_DIR / ".shared_dataset_links.json"
ICON_FILE = Path("assets") / "shared_dataset_icon.ico"

ORANGE = "#F28C28"
ORANGE_DARK = "#D96A0B"
ORANGE_SOFT = "#FFF2E4"
CREAM = "#FFF9F3"
TEXT = "#3F2A18"
MUTED = "#8C6540"
BORDER = "#F4C8A1"
WHITE = "#FFFFFF"


@dataclass
class ShareRecord:
    dataset_path: str
    link_path: str
    link_type: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "dataset_path": self.dataset_path,
            "link_path": self.link_path,
            "link_type": self.link_type,
            "created_at": self.created_at,
        }


def normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def resource_path(relative_path: str | Path) -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", APP_DIR))
    return base_dir / Path(relative_path)


def ensure_record_file() -> None:
    if not RECORD_FILE.exists():
        RECORD_FILE.write_text("[]", encoding="utf-8")


def load_records() -> list[ShareRecord]:
    ensure_record_file()
    try:
        raw = json.loads(RECORD_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw = []
    records: list[ShareRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        records.append(
            ShareRecord(
                dataset_path=str(item.get("dataset_path", "")),
                link_path=str(item.get("link_path", "")),
                link_type=str(item.get("link_type", "")),
                created_at=str(item.get("created_at", "")),
            )
        )
    return records


def save_records(records: list[ShareRecord]) -> None:
    ensure_record_file()
    RECORD_FILE.write_text(
        json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_existing_target(link_path: str) -> str | None:
    path = Path(link_path)
    if not path.exists():
        return None
    try:
        return normalize_path(str(path.resolve()))
    except OSError:
        return None


def list_dataset_shares(dataset_path: str) -> list[ShareRecord]:
    dataset_norm = normalize_path(dataset_path)
    valid_records: list[ShareRecord] = []
    for record in load_records():
        if normalize_path(record.dataset_path) != dataset_norm:
            continue
        target = resolve_existing_target(record.link_path)
        if target == dataset_norm:
            valid_records.append(record)
    return valid_records


def decode_windows_output(raw: bytes) -> str:
    if not raw:
        return ""
    encodings = []
    preferred = locale.getpreferredencoding(False)
    if preferred:
        encodings.append(preferred)
    encodings.extend(["gbk", "utf-8", "mbcs"])
    for encoding in encodings:
        try:
            return raw.decode(encoding).strip()
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace").strip()


def explain_link_error(raw_error: str, link_type: str) -> str:
    error = raw_error.lower()
    if "cannot create a file when that file already exists" in error or "已存在" in raw_error:
        return "目标位置已存在同名文件或目录，请先删除或更换目标路径。"
    if "cannot find the path specified" in error or "找不到路径" in raw_error:
        return "源路径或目标路径不存在，请重新检查所选目录。"
    if "access is denied" in error or "拒绝访问" in raw_error:
        if link_type == "symlink":
            return "创建符号链接被系统拒绝。请尝试以管理员身份运行该工具，或启用 Windows 开发者模式。"
        return "创建目录链接被系统拒绝，请确认当前目录有写入权限。"
    if "privilege" in error or "1314" in error or "客户端没有所需的特权" in raw_error:
        return "当前权限不足，无法创建符号链接。请以管理员身份运行该工具，或启用 Windows 开发者模式。"
    if "the device does not support symbolic links" in error:
        return "当前磁盘或文件系统不支持符号链接。"
    if raw_error:
        return f"创建共享失败：{raw_error}"
    return "创建共享失败，请检查路径、权限和磁盘类型后重试。"


def create_directory_link(source_path: str, link_path: str) -> tuple[bool, str, str]:
    source_drive = Path(source_path).drive.lower()
    target_drive = Path(link_path).drive.lower()
    if source_drive and target_drive and source_drive == target_drive:
        link_type = "junction"
        command = f'mklink /J "{link_path}" "{source_path}"'
        result = subprocess.run(
            f"cmd /c {command}",
            capture_output=True,
            text=False,
            shell=True,
        )
        if result.returncode == 0 and os.path.exists(link_path):
            detail = decode_windows_output(result.stdout) or "已成功创建 Junction 目录链接。"
            return True, link_type, detail

        raw_error = decode_windows_output(result.stderr) or decode_windows_output(result.stdout)
        return False, link_type, explain_link_error(raw_error, link_type)
    else:
        link_type = "symlink"
        try:
            os.symlink(source_path, link_path, target_is_directory=True)
            return True, link_type, "已成功创建目录符号链接。"
        except OSError as exc:
            raw_error = str(exc)
            if getattr(exc, "winerror", None) == 1314:
                raw_error = "1314 权限不足"
            return False, link_type, explain_link_error(raw_error, link_type)


class SharedDatasetTool:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1280x860")
        self.root.minsize(1180, 800)
        self.root.configure(bg=CREAM)
        self.apply_window_icon()

        self.dataset_path_var = tk.StringVar()
        self.target_path_var = tk.StringVar()
        self.link_name_var = tk.StringVar(value=DEFAULT_LINK_NAME)
        self.summary_var = tk.StringVar(value="请选择一个数据集源路径以检测共享情况。")
        self.status_var = tk.StringVar(value="准备就绪")

        self.configure_styles()
        self.build_ui()
        self.refresh_records()

    def apply_window_icon(self) -> None:
        icon_path = resource_path(ICON_FILE)
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass

    def configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Root.TFrame", background=CREAM)
        style.configure("Card.TFrame", background=WHITE, relief="flat")
        style.configure("Header.TLabel", background=CREAM, foreground=TEXT, font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Sub.TLabel", background=CREAM, foreground=MUTED, font=("Microsoft YaHei UI", 11))
        style.configure("CardTitle.TLabel", background=WHITE, foreground=TEXT, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("CardText.TLabel", background=WHITE, foreground=MUTED, font=("Microsoft YaHei UI", 10))
        style.configure(
            "Orange.TButton",
            background=ORANGE,
            foreground=WHITE,
            borderwidth=0,
            focusthickness=0,
            focuscolor=ORANGE,
            padding=(12, 8),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Orange.TButton", background=[("active", ORANGE_DARK)])
        style.configure(
            "Ghost.TButton",
            background=WHITE,
            foreground=ORANGE_DARK,
            borderwidth=1,
            focusthickness=0,
            focuscolor=WHITE,
            padding=(12, 8),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Ghost.TButton", background=[("active", ORANGE_SOFT)])
        style.configure("TLabelframe", background=WHITE, foreground=TEXT, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=WHITE, foreground=TEXT, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TEntry", fieldbackground=WHITE, foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER)

    def build_ui(self) -> None:
        container = ttk.Frame(self.root, style="Root.TFrame", padding=24)
        container.pack(fill="both", expand=True)

        self.build_header(container)

        body = ttk.Frame(container, style="Root.TFrame")
        body.pack(fill="both", expand=True, pady=(18, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        left = ttk.Frame(body, style="Card.TFrame", padding=20)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        right = ttk.Frame(body, style="Card.TFrame", padding=20)
        right.grid(row=0, column=1, sticky="nsew")

        self.build_form(left)
        self.build_records_panel(right)

        footer = ttk.Frame(container, style="Root.TFrame")
        footer.pack(fill="x", pady=(14, 0))
        ttk.Label(footer, textvariable=self.status_var, style="Sub.TLabel").pack(side="left")

    def build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, style="Root.TFrame")
        header.pack(fill="x")

        logo = tk.Canvas(header, width=88, height=88, bg=CREAM, highlightthickness=0)
        logo.pack(side="left")
        self.draw_logo(logo)

        title_wrap = ttk.Frame(header, style="Root.TFrame")
        title_wrap.pack(side="left", padx=(14, 0), fill="x", expand=True)
        ttk.Label(title_wrap, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            title_wrap,
            text="选择统一数据集路径与目标项目路径，一键创建共享目录链接，并查看该数据集的已共享记录。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def draw_logo(self, canvas: tk.Canvas) -> None:
        canvas.create_oval(12, 12, 74, 30, fill=ORANGE, outline=ORANGE_DARK, width=2)
        canvas.create_rectangle(12, 22, 74, 62, fill=ORANGE, outline=ORANGE_DARK, width=2)
        canvas.create_oval(12, 54, 74, 76, fill=ORANGE, outline=ORANGE_DARK, width=2)
        canvas.create_oval(22, 30, 64, 44, fill="#FDBA74", outline="")
        canvas.create_text(65, 64, text="S", font=("Arial", 28, "bold"), fill=WHITE)

    def build_form(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="创建共享链接", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            parent,
            text="默认会在目标项目路径下创建一个名为 data 的目录链接。同盘优先使用 Junction，跨盘自动使用符号链接。",
            style="CardText.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(6, 18))

        self.build_path_selector(
            parent,
            title="数据集源路径",
            variable=self.dataset_path_var,
            button_text="选择数据集",
            command=self.choose_dataset_path,
        )
        self.build_path_selector(
            parent,
            title="目标项目路径",
            variable=self.target_path_var,
            button_text="选择目标路径",
            command=self.choose_target_path,
        )

        name_frame = ttk.Frame(parent, style="Card.TFrame")
        name_frame.pack(fill="x", pady=(6, 14))
        ttk.Label(name_frame, text="链接目录名称", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Entry(name_frame, textvariable=self.link_name_var).pack(fill="x", pady=(8, 0), ipady=5)

        note = tk.Text(
            parent,
            height=8,
            bg=ORANGE_SOFT,
            fg=TEXT,
            relief="flat",
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            padx=12,
            pady=10,
        )
        note.insert(
            "1.0",
            "使用说明\n\n"
            "1. 先选择统一存放的数据集目录。\n"
            "2. 再选择目标项目目录，工具会在其中创建链接目录。\n"
            "3. 如果源路径与目标路径不在同一磁盘，Windows 可能要求管理员权限来创建符号链接。\n"
            "4. 右侧面板会显示当前数据集已通过本工具创建且仍然有效的共享位置。",
        )
        note.configure(state="disabled")
        note.pack(fill="x", pady=(4, 16))

        button_row = ttk.Frame(parent, style="Card.TFrame")
        button_row.pack(fill="x")
        ttk.Button(button_row, text="创建共享", style="Orange.TButton", command=self.create_share).pack(side="left")
        ttk.Button(button_row, text="刷新检测", style="Ghost.TButton", command=self.refresh_records).pack(side="left", padx=(10, 0))

    def build_path_selector(
        self,
        parent: ttk.Frame,
        title: str,
        variable: tk.StringVar,
        button_text: str,
        command,
    ) -> None:
        block = ttk.Frame(parent, style="Card.TFrame")
        block.pack(fill="x", pady=(0, 14))
        ttk.Label(block, text=title, style="CardTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(block, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(row, text=button_text, style="Ghost.TButton", command=command).pack(side="left", padx=(10, 0))

    def build_records_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="共享检测详情", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            parent,
            textvariable=self.summary_var,
            style="CardText.TLabel",
            wraplength=320,
            justify="left",
        ).pack(anchor="w", pady=(6, 12))

        columns = ("link_path", "link_type", "created_at")
        tree_frame = ttk.Frame(parent, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=18)
        self.tree.heading("link_path", text="共享位置")
        self.tree.heading("link_type", text="类型")
        self.tree.heading("created_at", text="创建时间")
        self.tree.column("link_path", width=260, anchor="w")
        self.tree.column("link_type", width=70, anchor="center")
        self.tree.column("created_at", width=120, anchor="center")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        tips = tk.Text(
            parent,
            height=7,
            bg=WHITE,
            fg=MUTED,
            relief="flat",
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            padx=2,
            pady=8,
        )
        tips.insert(
            "1.0",
            "检测说明：\n"
            "此处统计的是本工具创建过、且当前仍然指向所选数据集源路径的共享目录链接。\n"
            "如果你手动删除了链接，刷新后会自动从统计中消失。",
        )
        tips.configure(state="disabled")
        tips.pack(fill="x", pady=(12, 0))

    def choose_dataset_path(self) -> None:
        path = filedialog.askdirectory(title="选择数据集源路径")
        if path:
            self.dataset_path_var.set(path)
            self.refresh_records()

    def choose_target_path(self) -> None:
        path = filedialog.askdirectory(title="选择目标项目路径")
        if path:
            self.target_path_var.set(path)

    def refresh_records(self) -> None:
        dataset_path = self.dataset_path_var.get().strip()
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not dataset_path:
            self.summary_var.set("请选择一个数据集源路径以检测共享情况。")
            self.status_var.set("准备就绪")
            return

        if not os.path.isdir(dataset_path):
            self.summary_var.set("当前数据集路径不存在，请重新选择。")
            self.status_var.set("数据集路径无效")
            return

        records = list_dataset_shares(dataset_path)
        for record in records:
            link_type_label = "Junction" if record.link_type == "junction" else "Symlink"
            self.tree.insert("", "end", values=(record.link_path, link_type_label, record.created_at))

        self.summary_var.set(f"已检测到该数据集共有 {len(records)} 次有效共享，下面展示对应位置。")
        self.status_var.set("共享记录已刷新")

    def create_share(self) -> None:
        dataset_path = self.dataset_path_var.get().strip()
        target_path = self.target_path_var.get().strip()
        link_name = self.link_name_var.get().strip() or DEFAULT_LINK_NAME

        if not dataset_path or not target_path:
            messagebox.showwarning(APP_TITLE, "请先选择数据集源路径和目标项目路径。")
            return
        if not os.path.isdir(dataset_path):
            messagebox.showerror(APP_TITLE, "数据集源路径不存在。")
            return
        if not os.path.isdir(target_path):
            messagebox.showerror(APP_TITLE, "目标项目路径不存在。")
            return
        if any(char in link_name for char in '<>:"/\\|?*'):
            messagebox.showerror(APP_TITLE, "链接目录名称包含非法字符。")
            return

        link_path = os.path.join(target_path, link_name)
        if os.path.exists(link_path):
            messagebox.showerror(APP_TITLE, f"目标位置已存在同名目录或文件：\n{link_path}")
            return

        self.status_var.set("正在创建共享链接...")
        self.root.update_idletasks()

        ok, link_type, detail = create_directory_link(dataset_path, link_path)
        if not ok:
            extra = ""
            if link_type == "symlink":
                extra = "\n\n提示：跨磁盘创建符号链接时，建议使用管理员权限运行本工具。"
            messagebox.showerror(APP_TITLE, f"{detail}{extra}")
            self.status_var.set("共享创建失败")
            return

        records = load_records()
        records.append(
            ShareRecord(
                dataset_path=normalize_path(dataset_path),
                link_path=normalize_path(link_path),
                link_type=link_type,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        save_records(records)

        self.status_var.set("共享创建成功")
        self.refresh_records()
        messagebox.showinfo(APP_TITLE, f"共享创建成功。\n\n链接位置：{link_path}\n类型：{link_type}\n\n{detail}")


def main() -> None:
    ensure_record_file()
    root = tk.Tk()
    SharedDatasetTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
