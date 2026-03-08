# Koto 便携版发布指南

目标：生成一个可直接发给其他 Windows 用户的目录版 Koto。对方解压后，先安装本地模型，再启动 Koto 即可使用。

## 发布流程

1. 构建主程序：运行 `Build Koto Portable` 任务，生成 `dist/Koto/`。
2. 构建本地模型安装器：运行 `pyinstaller local_model_installer.spec --clean -y`，生成 `LocalModelInstaller.exe`。
3. 组装便携目录：运行 `python deploy_portable.py`。
4. 分发 `dist/Koto_Portable/` 整个目录，建议压缩成 zip。

## 分发目录内容

- `Koto.exe`：主程序。
- `Start_Koto.bat`：推荐启动入口。
- `Stop_Koto.bat`：停止程序。
- `Install_Local_Model.bat`：独立本地模型安装入口。
- `LocalModelInstaller.exe`：Ollama + 模型安装器。
- `_internal/`：PyInstaller 运行时依赖。

## 收件方使用步骤

1. 解压 `Koto_Portable.zip` 到本地任意目录。
2. 双击 `Install_Local_Model.bat`，按硬件推荐安装本地模型。
3. 安装完成后，双击 `Start_Koto.bat` 或 `Koto.exe`。
4. 首次进入时填写 Gemini API Key。
5. 后续直接运行 `Start_Koto.bat` 即可。

## 当前设计约束

- 便携版采用 `onedir` 分发，不做系统级安装，不写注册表。
- 用户数据保存在程序目录旁的 `config/`、`workspace/`、`chats/`、`logs/`。
- 本地模型本体由 Ollama 管理，不随 Koto 包体分发。
- 如果未安装本地模型，Koto 仍可走云模型模式，但不满足“本地模型开箱可用”的目标。

## 验收清单

- `dist/Koto_Portable/Koto.exe` 存在。
- `dist/Koto_Portable/_internal/` 存在。
- `dist/Koto_Portable/LocalModelInstaller.exe` 存在。
- `dist/Koto_Portable/Start_Koto.bat` 可启动程序。
- `dist/Koto_Portable/Install_Local_Model.bat` 可打开安装器。
- 首次启动后 `config/user_settings.json` 会在安装模型后写入 `model_mode` 和 `local_model`。