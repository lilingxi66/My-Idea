import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
import ctypes # 用于实现单实例检查

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

# 单实例检查使用的互斥体名称
MUTEX_NAME = "Global\\LstGeneratorSingleInstanceMutex"

def is_already_running():
    """
    检查程序是否已经在运行中 (仅限 Windows)。
    """
    if os.name != 'nt':
        return False
        
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    
    # ERROR_ALREADY_EXISTS = 183
    if last_error == 183:
        return True
    return False

def generate_list_file(image_dir, edge_dir, root_dir, save_dir, filename, recursive=False):
    """
    扫描目录并生成包含文件列表的 .lst 文件。
    
    :param image_dir: 图像所在的目录。
    :param edge_dir: 边缘图所在的目录。
    :param root_dir: 根目录（用于计算相对路径）。
    :param save_dir: 保存文件的目录路径。
    :param filename: 输出的 .lst 文件名。
    :param recursive: 是否递归扫描子目录。
    """
    if not all(os.path.exists(d) for d in [image_dir, edge_dir, root_dir]):
        return False, "错误: 图像、边缘或根目录不存在。"

    if not os.path.exists(save_dir):
        try:
            os.makedirs(save_dir)
        except Exception as e:
            return False, f"无法创建保存目录: {e}"

    output_path = os.path.join(save_dir, filename)
    
    # 获取图像文件列表
    image_files = {}
    if recursive:
        for root, _, files in os.walk(image_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_to_image = os.path.relpath(full_path, image_dir)
                image_files[rel_to_image] = full_path
    else:
        for file in os.listdir(image_dir):
            full_path = os.path.join(image_dir, file)
            if os.path.isfile(full_path):
                image_files[file] = full_path

    # 获取边缘文件列表并匹配
    results = []
    for rel_path, img_full_path in image_files.items():
        # 假设边缘文件具有相同的文件名（可能后缀不同，但通常一一对应）
        # 如果后缀不同，可能需要更复杂的匹配逻辑，目前先按相对路径匹配逻辑处理
        edge_full_path = os.path.join(edge_dir, rel_path)
        
        # 尝试匹配（支持 png/jpg 等常见格式）
        if not os.path.exists(edge_full_path):
            base, _ = os.path.splitext(edge_full_path)
            for ext in ['.png', '.jpg', '.jpeg']:
                if os.path.exists(base + ext):
                    edge_full_path = base + ext
                    break
        
        if os.path.exists(edge_full_path):
            # 计算相对于 root_dir 的路径
            rel_img = os.path.relpath(img_full_path, root_dir).replace('\\', '/')
            rel_edge = os.path.relpath(edge_full_path, root_dir).replace('\\', '/')
            results.append(f"{rel_img} {rel_edge}")

    results.sort()

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in results:
                f.write(line + '\n')
        return True, f"成功生成 '{output_path}'，共包含 {len(results)} 个条目。"
    except Exception as e:
        return False, f"写入输出文件时出错: {e}"

def run_gui():
    """
    运行 GUI 界面。
    """
    if ctk is None:
        # 如果未安装 customtkinter，回退到标准 tk
        run_standard_gui()
        return

    ctk.set_appearance_mode("System")  # Modes: "System" (standard), "Dark", "Light"
    ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

    def on_closing():
        """安全退出程序"""
        root.destroy()
        sys.exit(0)

    def select_image_directory():
        path = filedialog.askdirectory()
        if path:
            image_entry.delete(0, tk.END)
            image_entry.insert(0, path)

    def select_edge_directory():
        path = filedialog.askdirectory()
        if path:
            edge_entry.delete(0, tk.END)
            edge_entry.insert(0, path)

    def select_root_directory():
        path = filedialog.askdirectory()
        if path:
            root_entry.delete(0, tk.END)
            root_entry.insert(0, path)

    def generate():
        img_dir = image_entry.get()
        edg_dir = edge_entry.get()
        rot_dir = root_entry.get()
        save = rot_dir
        filename = filename_entry.get() or "file_list.lst"
        recursive = recursive_var.get()

        if not all([img_dir, edg_dir, rot_dir]):
            messagebox.showwarning("警告", "请确保图像、边缘和根目录都已选择！")
            return
        
        success, message = generate_list_file(img_dir, edg_dir, rot_dir, save, filename, recursive)
        if success:
            messagebox.showinfo("成功", message)
        else:
            messagebox.showerror("错误", message)

    root = ctk.CTk()
    root.title(".lst 文件生成器 (美化版)")
    root.geometry("650x650")
    
    # 设置关闭协议
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # 设置 Logo
    logo_path = "./logo/logo.ico"
    if os.path.exists(logo_path):
        try:
            root.iconbitmap(logo_path)
        except:
            pass

    # 样式配置 (浅绿色, 无阴影, 圆角)
    BTN_COLOR = "#A5D6A7"  # 浅绿色
    BTN_HOVER_COLOR = "#2E7D32"  # 绿叶色 (更深的绿色)
    BORDER_COLOR = "#CCCCCC" # 浅灰色描边
    TEXT_COLOR = "#2E7D32" # 标签文字深绿色
    LBL_FONT = ("Microsoft YaHei", 12) # 路径提示文字字体
    BG_COLOR = "#F1F8E9" # 浅绿白背景色
    
    # 辅助类：实现放大效果而不影响布局
    class AnimatedButton(ctk.CTkFrame):
        def __init__(self, master, text, command, width=140, height=32, 
                     fg_color=BTN_COLOR, hover_color=BTN_HOVER_COLOR, 
                     text_color="black", corner_radius=10, font=None, 
                     border_width=0, border_color=None, **kwargs):
            super().__init__(master, fg_color="transparent", width=width*1.1, height=height*1.1)
            self.pack_propagate(False) # 固定大小，防止子组件撑开
            
            self.original_width = width
            self.original_height = height
            self.hover_width = width * 1.08
            self.hover_height = height * 1.08
            
            self.original_border_width = border_width
            self.border_color = border_color
            self.hover_color = hover_color
            self.fg_color = fg_color
            
            self.btn = ctk.CTkButton(self, text=text, command=command, 
                                     width=self.original_width, height=self.original_height,
                                     fg_color=fg_color, hover_color=hover_color, 
                                     text_color=text_color, corner_radius=corner_radius, 
                                     font=font, border_width=border_width,
                                     border_color=border_color, **kwargs)
            self.btn.place(relx=0.5, rely=0.5, anchor="center")
            
            self.btn.bind("<Enter>", self.on_enter)
            self.btn.bind("<Leave>", self.on_leave)

        def on_enter(self, event):
            self.btn.configure(width=self.hover_width, height=self.hover_height, 
                               fg_color=self.hover_color, border_width=0)

        def on_leave(self, event):
            self.btn.configure(width=self.original_width, height=self.original_height, 
                               fg_color=self.fg_color, border_width=self.original_border_width)

    # 主框架
    main_frame = ctk.CTkFrame(root, corner_radius=20, fg_color=BG_COLOR)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    # 标题
    title_label = ctk.CTkLabel(main_frame, text=".lst 文件生成器", font=("Microsoft YaHei", 20, "bold"), text_color=TEXT_COLOR)
    title_label.pack(pady=10)

    # 图像路径
    ctk.CTkLabel(main_frame, text="1. 图像路径 (Image Directory):", anchor="w", font=LBL_FONT, text_color=TEXT_COLOR).pack(fill=tk.X, padx=30, pady=(5, 0))
    image_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
    image_frame.pack(fill=tk.X, padx=30, pady=2)
    image_entry = ctk.CTkEntry(image_frame, corner_radius=10)
    image_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    image_btn = AnimatedButton(image_frame, text="浏览...", width=80, height=32, command=select_image_directory, 
                               fg_color=BTN_COLOR, hover_color=BTN_HOVER_COLOR, text_color="white", 
                               corner_radius=10, border_width=1, border_color=BORDER_COLOR)
    image_btn.pack(side=tk.RIGHT, padx=(5, 0))

    # 边缘路径
    ctk.CTkLabel(main_frame, text="2. 边缘路径 (Edge Directory):", anchor="w", font=LBL_FONT, text_color=TEXT_COLOR).pack(fill=tk.X, padx=30, pady=(5, 0))
    edge_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
    edge_frame.pack(fill=tk.X, padx=30, pady=2)
    edge_entry = ctk.CTkEntry(edge_frame, corner_radius=10)
    edge_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    edge_btn = AnimatedButton(edge_frame, text="浏览...", width=80, height=32, command=select_edge_directory, 
                               fg_color=BTN_COLOR, hover_color=BTN_HOVER_COLOR, text_color="white", 
                               corner_radius=10, border_width=1, border_color=BORDER_COLOR)
    edge_btn.pack(side=tk.RIGHT, padx=(5, 0))

    # 根路径
    ctk.CTkLabel(main_frame, text="3. 根路径 (Root Directory - 用于计算相对路径):", anchor="w", font=LBL_FONT, text_color=TEXT_COLOR).pack(fill=tk.X, padx=30, pady=(5, 0))
    root_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
    root_frame.pack(fill=tk.X, padx=30, pady=2)
    root_entry = ctk.CTkEntry(root_frame, corner_radius=10)
    root_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    root_btn = AnimatedButton(root_frame, text="浏览...", width=80, height=32, command=select_root_directory, 
                               fg_color=BTN_COLOR, hover_color=BTN_HOVER_COLOR, text_color="white", 
                               corner_radius=10, border_width=1, border_color=BORDER_COLOR)
    root_btn.pack(side=tk.RIGHT, padx=(5, 0))

    # 输出文件名
    ctk.CTkLabel(main_frame, text="4. 输出文件名 (Filename):", anchor="w", font=LBL_FONT, text_color=TEXT_COLOR).pack(fill=tk.X, padx=30, pady=(5, 0))
    filename_entry = ctk.CTkEntry(main_frame, corner_radius=10)
    filename_entry.insert(0, "file_list.lst")
    filename_entry.pack(fill=tk.X, padx=30, pady=2)

    # 选项
    recursive_var = tk.BooleanVar()
    ctk.CTkCheckBox(main_frame, text="递归扫描子目录", variable=recursive_var, font=LBL_FONT, text_color=TEXT_COLOR,
                    hover_color=BTN_HOVER_COLOR, border_color=BTN_COLOR, checkmark_color=TEXT_COLOR).pack(pady=5)

    # 生成按钮
    generate_btn = AnimatedButton(main_frame, text="立即生成 .lst 文件", command=generate, 
                                 fg_color=BTN_COLOR, hover_color=BTN_HOVER_COLOR, text_color="white", 
                                 font=("Microsoft YaHei", 16, "bold"), width=280, height=55, corner_radius=25)
    generate_btn.pack(pady=(5, 10))

    root.mainloop()

def run_standard_gui():
    """
    运行标准 GUI 界面 (备用)。
    """
    def select_image_directory():
        path = filedialog.askdirectory()
        if path:
            image_entry.delete(0, tk.END)
            image_entry.insert(0, path)

    def select_edge_directory():
        path = filedialog.askdirectory()
        if path:
            edge_entry.delete(0, tk.END)
            edge_entry.insert(0, path)

    def select_root_directory():
        path = filedialog.askdirectory()
        if path:
            root_entry.delete(0, tk.END)
            root_entry.insert(0, path)

    def generate():
        img_dir = image_entry.get()
        edg_dir = edge_entry.get()
        rot_dir = root_entry.get()
        save = rot_dir
        filename = filename_entry.get() or "file_list.lst"
        recursive = recursive_var.get()

        if not all([img_dir, edg_dir, rot_dir]):
            messagebox.showwarning("警告", "请确保图像、边缘和根目录都已选择！")
            return
        
        success, message = generate_list_file(img_dir, edg_dir, rot_dir, save, filename, recursive)
        if success:
            messagebox.showinfo("成功", message)
        else:
            messagebox.showerror("错误", message)

    def on_closing():
        root.destroy()
        sys.exit(0)

    root = tk.Tk()
    root.title(".lst 文件生成器")
    root.geometry("600x550")
    
    # 设置关闭协议
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    logo_path = "./logo/logo.ico"
    if os.path.exists(logo_path):
        try:
            root.iconbitmap(logo_path)
        except:
            pass

    tk.Label(root, text="1. 图像路径 (Image Directory):").pack(pady=(10, 0), anchor="w", padx=20)
    image_frame = tk.Frame(root)
    image_frame.pack(fill=tk.X, padx=20)
    image_entry = tk.Entry(image_frame)
    image_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    tk.Button(image_frame, text="浏览...", command=select_image_directory).pack(side=tk.RIGHT, padx=(5, 0))

    tk.Label(root, text="2. 边缘路径 (Edge Directory):").pack(pady=(10, 0), anchor="w", padx=20)
    edge_frame = tk.Frame(root)
    edge_frame.pack(fill=tk.X, padx=20)
    edge_entry = tk.Entry(edge_frame)
    edge_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    tk.Button(edge_frame, text="浏览...", command=select_edge_directory).pack(side=tk.RIGHT, padx=(5, 0))

    tk.Label(root, text="3. 根路径 (Root Directory):").pack(pady=(10, 0), anchor="w", padx=20)
    root_frame = tk.Frame(root)
    root_frame.pack(fill=tk.X, padx=20)
    root_entry = tk.Entry(root_frame)
    root_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    tk.Button(root_frame, text="浏览...", command=select_root_directory).pack(side=tk.RIGHT, padx=(5, 0))

    tk.Label(root, text="4. 输出文件名 (Filename):").pack(pady=(10, 0), anchor="w", padx=20)
    filename_entry = tk.Entry(root)
    filename_entry.insert(0, "file_list.lst")
    filename_entry.pack(fill=tk.X, padx=20)

    options_frame = tk.Frame(root)
    options_frame.pack(pady=10)
    recursive_var = tk.BooleanVar()
    tk.Checkbutton(options_frame, text="递归扫描子目录", variable=recursive_var).pack(side=tk.LEFT)

    tk.Button(root, text="立即生成 .lst 文件", command=generate, bg="#4CAF50", fg="white", font=("Microsoft YaHei", 10, "bold"), height=2).pack(pady=20, fill=tk.X, padx=100)

    root.mainloop()

if __name__ == "__main__":
    if is_already_running():
        # 如果已在运行，弹出警告并退出
        root = tk.Tk()
        root.withdraw() # 隐藏主窗口
        messagebox.showwarning("LST已在运行", "请在任务栏里寻找LST图标")
        root.destroy()
        sys.exit(0)
        
    run_gui()
