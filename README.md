# 共享数据集工具

一个基于 Windows 目录链接机制的数据集共享桌面工具。  
它用于把统一的数据集目录共享到多个项目目录中，避免重复拷贝数据集，并提供图形化界面查看共享次数和共享位置。

本项目基于 Windows 目录链接机制实现，核心方式是：

- 同盘符时创建 `Junction`
- 跨盘符时创建目录符号链接 `Symlink`

## 功能特性

- 图形化界面，主色调为橙色
- 支持选择数据集源路径
- 支持选择目标项目路径
- 支持自定义共享目录名称，默认是 `data`
- 自动判断使用 `Junction` 或 `Symlink`
- 显示当前数据集已共享的次数与位置
- 记录本工具创建过的共享信息，并在刷新时校验是否仍然有效
- 内置数据库风格 `S` logo，并已用于窗口图标和 exe 图标

## 运行环境

- Windows 10 / Windows 11
- Python 3.10 及以上
- 标准库 `tkinter`

打包 exe 时还需要：

- `PyInstaller`

## 项目结构

```text
SharedDatasteProject/
├─ assets/
│  ├─ shared_dataset_icon.ico
│  └─ shared_dataset_icon.png
├─ dist/
│  ├─ SharedDatasetTool.exe
│  └─ SharedDatasetTool_fixed.exe
├─ build_exe.bat
├─ shared_dataset_tool.py
├─ 启动共享数据集工具.bat
└─ .shared_dataset_links.json
```

说明：

- `shared_dataset_tool.py`：主程序源码
- `启动共享数据集工具.bat`：本地直接启动 Python 版本
- `build_exe.bat`：使用 PyInstaller 打包 exe
- `.shared_dataset_links.json`：共享记录缓存文件
- `assets/`：图标资源
- `dist/`：打包输出目录

## 使用方式

### 方式一：直接运行 Python 程序

在项目根目录执行：

```bat
python shared_dataset_tool.py
```

也可以直接双击：

- [启动共享数据集工具.bat](D:\Workplace\Project\Python\SharedDatasteProject\启动共享数据集工具.bat)

### 方式二：运行 exe

直接双击：

- [SharedDatasetTool_fixed.exe](D:\Workplace\Project\Python\SharedDatasteProject\dist\SharedDatasetTool_fixed.exe)

如果旧版 exe 没被占用，也可以使用：

- [SharedDatasetTool.exe](D:\Workplace\Project\Python\SharedDatasteProject\dist\SharedDatasetTool.exe)

## 界面使用流程

1. 选择“数据集源路径”
2. 选择“目标项目路径”
3. 如有需要，修改“链接目录名称”
4. 点击“创建共享”
5. 在右侧查看该数据集当前已共享的次数与位置

默认会在目标项目目录下创建一个名为 `data` 的共享目录。

## 共享逻辑说明

### 同盘共享

当数据集源路径与目标项目路径位于同一磁盘时，工具会创建：

```text
Junction
```

优点：

- 兼容性好
- 不需要开发者模式
- 适合同机多个项目共享数据集

### 跨盘共享

当数据集源路径与目标项目路径位于不同磁盘时，工具会创建：

```text
Directory Symlink
```

这类操作在 Windows 上通常需要更高权限。

## 权限与常见问题

### 1. 创建共享失败

常见原因：

- 目标位置已经存在同名目录或文件
- 目标目录没有写入权限
- 跨盘创建符号链接时权限不足
- 选择的源路径或目标路径不存在

### 2. 跨盘创建失败

如果跨盘创建 `Symlink` 失败，请尝试：

- 以管理员身份运行程序
- 或启用 Windows 开发者模式

### 3. 共享次数和位置是如何统计的

右侧列表统计的是：

- 本工具创建过
- 且当前仍然有效
- 且仍然指向当前所选数据集源路径

如果某个链接后来被手动删除，点击“刷新检测”后它会从统计中消失。

## 打包为 exe

### 安装打包依赖

```bat
python -m pip install pyinstaller
```

### 执行打包

```bat
cmd /c build_exe.bat
```

或直接双击：

- [build_exe.bat](D:\Workplace\Project\Python\SharedDatasteProject\build_exe.bat)

### 打包输出

默认输出到：

- [dist](D:\Workplace\Project\Python\SharedDatasteProject\dist)

主要产物：

- [SharedDatasetTool.exe](D:\Workplace\Project\Python\SharedDatasteProject\dist\SharedDatasetTool.exe)

如果旧 exe 正在运行，Windows 可能会阻止覆盖。此时请先关闭旧程序后再重新打包。

## 开发说明

主程序使用 `tkinter` 实现界面，不依赖额外 GUI 框架。  
图标资源位于：

- [shared_dataset_icon.ico](D:\Workplace\Project\Python\SharedDatasteProject\assets\shared_dataset_icon.ico)
- [shared_dataset_icon.png](D:\Workplace\Project\Python\SharedDatasteProject\assets\shared_dataset_icon.png)

如果要修改界面样式、共享逻辑或图标，优先修改：

- [shared_dataset_tool.py](D:\Workplace\Project\Python\SharedDatasteProject\shared_dataset_tool.py)

## 后续可扩展方向

- 删除共享链接
- 打开共享位置
- 导出共享清单
- 增加管理员权限检测
- 增加打包时的版本信息和发布者信息

## 备注

项目中可能存在 `build/`、`dist/`、`__pycache__/` 等构建产物目录，这些属于正常打包或运行缓存。
