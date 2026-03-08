#!/usr/bin/env python3
"""
Koto Server Mode - 纯 Web 服务（无桌面窗口）
用于云部署 / Docker / Railway / VPS

用法:
  python server.py                    # 开发模式
  gunicorn -w 2 -b 0.0.0.0:5000 server:app  # 生产模式

环境变量:
  KOTO_PORT=5000               服务端口
  KOTO_AUTH_ENABLED=true       启用认证（SaaS 模式）
  KOTO_JWT_SECRET=xxx          JWT 签名密钥
  KOTO_MAX_DAILY_REQUESTS=100  每用户每日请求上限
  GEMINI_API_KEY=xxx           Gemini API 密钥

  # LangSmith 可观测性追踪（可选）
  LANGCHAIN_TRACING_V2=true
  LANGCHAIN_API_KEY=lsv2_...   # https://smith.langchain.com
  LANGCHAIN_PROJECT=Koto
"""
import os
import sys
from pathlib import Path

# 设置环境
here = Path(__file__).resolve().parent
APP_ROOT = here.parent if here.name == "src" else here
os.chdir(str(APP_ROOT))
# 项目根目录放在最前面，确保 app/ 包优先于 web/app.py
sys.path.insert(0, str(APP_ROOT))
# web/ 放在后面，用于 web 内部的相对导入
sys.path.append(str(APP_ROOT / "web"))

# 确保必要目录存在
for d in ["logs", "chats", "workspace", "config"]:
    os.makedirs(APP_ROOT / d, exist_ok=True)

# 加载 .env 配置
try:
    from dotenv import load_dotenv
    env_file = APP_ROOT / "config" / "gemini_config.env"
    if env_file.exists():
        load_dotenv(str(env_file))
except ImportError:
    pass

# LangSmith 可观测性初始化（可选，仅当环境变量已设置时激活）
try:
    from app.core.monitoring.langsmith_tracer import init_langsmith
    init_langsmith()
except Exception:
    pass

# 导入 Flask app
from web.app import app

# 运行模式检测
DEPLOY_MODE = os.environ.get("KOTO_DEPLOY_MODE", "local")  # local / cloud
PORT = int(os.environ.get("KOTO_PORT", os.environ.get("PORT", "5000")))

if __name__ == "__main__":
    print(f"""
╔═══════════════════════════════════════╗
║     Koto 言 - AI Assistant Server     ║
║  Mode: {"Cloud (SaaS)" if os.environ.get("KOTO_AUTH_ENABLED") == "true" else "Local (No Auth)":33s} ║
║  Port: {PORT:<33d} ║
╚═══════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
