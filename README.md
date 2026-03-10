# Koto — AI Assistant

[![CI](https://github.com/Loganwon/Koto/actions/workflows/ci.yml/badge.svg)](https://github.com/Loganwon/Koto/actions/workflows/ci.yml)
[![Build](https://github.com/Loganwon/Koto/actions/workflows/build.yml/badge.svg)](https://github.com/Loganwon/Koto/actions/workflows/build.yml)
[![Docker Build](https://github.com/Loganwon/Koto/actions/workflows/docker.yml/badge.svg)](https://github.com/Loganwon/Koto/actions/workflows/docker.yml)
[![Release](https://github.com/Loganwon/Koto/actions/workflows/release.yml/badge.svg)](https://github.com/Loganwon/Koto/actions/workflows/release.yml)

Koto 是一个基于多模型 AI 的桌面 / 云端智能助手，支持多轮对话、长期记忆、知识库、文件分析、语音交互和工作流自动化。

## 快速开始（本地运行）

### 环境要求
- Python 3.11+
- （可选）本地语音：见 `config/requirements_voice.txt`

### 1. 克隆仓库并安装依赖

```bash
git clone https://github.com/<your-username>/Koto.git
cd Koto
python -m venv .venv
# Windows
.venv\Scripts\pip install -r config/requirements.txt
# macOS / Linux
.venv/bin/pip install -r config/requirements.txt
```

### 2. 配置 API Key

```bash
# 复制模板
copy config\gemini_config.env.example config\gemini_config.env
# 用文本编辑器打开，填入你的 Gemini API Key
```

在 `config/gemini_config.env` 中填入：

```
GEMINI_API_KEY=your_api_key_here
```

> 免费申请 Gemini API Key：https://aistudio.google.com/app/apikey

### 3. 启动

```bash
# 浏览器访问模式（推荐首次体验）
python server.py

# 桌面应用模式（独立窗口，需安装 pywebview）
python koto_app.py
```

打开浏览器访问 `http://localhost:5000`

---

## 云端部署（让他人通过网址使用）

项目已内置 Docker 支持，可一键部署到 Railway / Render / Fly.io 等平台。

### Railway（推荐，免费额度够用）

1. Fork 本仓库到你的 GitHub 账号
2. 登录 [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. 选择 Dockerfile：`deploy/Dockerfile`
4. 在 Railway 环境变量中添加：
   - `GEMINI_API_KEY` = 你的 API Key
   - `KOTO_AUTH_ENABLED` = `true`（开启访问保护）
5. 部署完成后 Railway 会提供一个公网 URL，分享给需要使用的人即可

### Docker 本地部署

```bash
cd deploy
docker build -f Dockerfile -t koto ..
docker run -p 5000:5000 \
  -e GEMINI_API_KEY=your_key_here \
  -e KOTO_AUTH_ENABLED=true \
  koto
```

---

## 主要功能

| 功能 | 说明 |
|------|------|
| 多轮对话 | 支持 Gemini / 本地模型 |
| 长期记忆 | 跨会话记忆，自动注入上下文 |
| 知识库 RAG | 上传 TXT / MD / PDF / DOCX，语义检索 |
| Excel 分析 | 上传表格，自然语言提问 |
| 语音交互 | 语音输入 / TTS 朗读 |
| 工作流 | 可视化任务编排 |
| 代码执行 | 沙箱内安全执行 Python |

完整文档见 [docs/README.md](docs/README.md)

---

## 项目结构

```
Koto/
├── server.py           # Flask 后端入口
├── koto_app.py         # 桌面应用入口（pywebview）
├── koto.spec           # PyInstaller 打包配置
├── app/                # 核心业务逻辑
├── web/                # 功能模块（记忆、知识库、工具等）
├── src/                # 辅助模块
├── config/             # 配置文件（gitignored 的敏感文件已排除）
├── deploy/             # Docker / Railway 部署文件
└── docs/               # 详细文档
```

## License

MIT
