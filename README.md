# 共享数据集工具

一个基于 Windows 目录链接机制的桌面工具，用来把统一的数据集目录共享给多个项目使用，避免重复拷贝数据集。

## 功能

- 图形化界面，橙色主题
- 选择数据集源路径
- 选择目标项目路径
- 自定义共享目录名称，默认是 `data`
- 同盘自动创建 `Junction`
- 跨盘自动创建目录符号链接 `Symlink`
- 显示当前数据集已共享的次数与位置
- 内置应用图标和 exe 图标

## 运行环境

- Windows 10 / Windows 11
- Python 3.10+
- `tkinter`

打包时需要：

- `PyInstaller`

## 项目结构

```text
SharedDatasteProject/
├─ assets/
│  ├─ shared_dataset_icon.ico
│  └─ shared_dataset_icon.png
├─ .gitignore
├─ build_exe.bat
├─ README.md
├─ SharedDatasetTool.spec
├─ shared_dataset_tool.py
└─ 启动共享数据集工具.bat
```

说明：

- `shared_dataset_tool.py`：主程序
- `build_exe.bat`：固定打包为 `dist/SharedDatasetTool.exe`
- `SharedDatasetTool.spec`：PyInstaller 配置
- `assets/`：图标资源
- `.gitignore`：忽略构建产物、缓存和本地记录

## 本地运行

命令行运行：

```bat
python shared_dataset_tool.py
```

或双击：

- [启动共享数据集工具.bat](D:\Workplace\Project\Python\SharedDatasteProject\启动共享数据集工具.bat)

## 打包 exe

安装依赖：

```bat
python -m pip install pyinstaller
```

执行打包：

```bat
cmd /c build_exe.bat
```

打包输出：

- [SharedDatasetTool.exe](D:\Workplace\Project\Python\SharedDatasteProject\dist\SharedDatasetTool.exe)

说明：

- 打包文件名始终为 `SharedDatasetTool.exe`
- 如果 `dist` 中已有同名文件，脚本会先尝试删除再重新生成
- 如果旧 exe 正在运行，Windows 会拒绝替换，此时先关闭程序再打包

## 使用流程

1. 选择数据集源路径
2. 选择目标项目路径
3. 按需修改链接目录名称
4. 点击“创建共享”
5. 在右侧查看共享次数和共享位置

## 权限说明

- 同盘共享通常使用 `Junction`
- 跨盘共享通常使用 `Symlink`
- 跨盘创建失败时，通常需要管理员权限或启用 Windows 开发者模式

## Git 上传建议

建议只上传源码和资源文件，不上传以下内容：

- `build/`
- `dist/`
- `__pycache__/`
- `.shared_dataset_links.json`

这些内容已经通过 `.gitignore` 忽略。
