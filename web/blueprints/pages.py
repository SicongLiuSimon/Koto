"""
HTML page-rendering blueprint.

Routes:
  GET /                        — index
  GET /app                     — app_main
  GET /file-network            — file_network
  GET /knowledge-graph         — knowledge_graph_page
  GET /test_upload             — test_upload
  GET /skills                  — skill_marketplace
  GET /skill-marketplace       — skill_marketplace
  GET /monitoring-dashboard    — monitoring_dashboard
  GET /edit-ppt/<session_id>   — edit_ppt
  GET /mini                    — mini_page
  GET /m                       — mobile_page
  GET /mobile                  — mobile_page
  GET /notebook                — notebook_ui
"""

import os

from flask import Blueprint, render_template, send_from_directory

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    # 云模式：未认证用户看到落地页
    deploy_mode = os.environ.get("KOTO_DEPLOY_MODE", "local")
    auth_enabled = os.environ.get("KOTO_AUTH_ENABLED", "false").lower() == "true"
    if deploy_mode == "cloud" and auth_enabled:
        return render_template("landing.html")
    return render_template("index.html")


@pages_bp.route("/app")
def app_main():
    """主应用页面（SaaS 模式下需认证后访问）"""
    return render_template("index.html")


@pages_bp.route("/file-network")
def file_network():
    """文件网络界面"""
    return render_template("file_network.html")


@pages_bp.route("/knowledge-graph")
def knowledge_graph_page():
    """知识图谱可视化界面"""
    return render_template("knowledge_graph.html")


@pages_bp.route("/test_upload")
def test_upload():
    return render_template("test_upload.html")


@pages_bp.route("/edit-ppt/<session_id>")
def edit_ppt(session_id):
    """PPT 生成后编辑页面（P1 功能）"""
    return render_template("edit_ppt.html")


@pages_bp.route("/skills")
@pages_bp.route("/skill-marketplace")
def skill_marketplace():
    """Koto Skill 库 — GitHub Extension Marketplace 风格管理界面"""
    return render_template("skill_marketplace.html")


@pages_bp.route("/monitoring-dashboard")
def monitoring_dashboard():
    """Phase 4 System Monitoring Dashboard"""
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), os.pardir, "static"),
        "monitoring_dashboard.html",
    )


@pages_bp.route("/mini")
def mini_page():
    """迷你模式页面（浏览器访问用）"""
    return render_template("mini_koto.html")


@pages_bp.route("/m")
@pages_bp.route("/mobile")
def mobile_page():
    """移动端优化页面"""
    return render_template("mobile.html")


@pages_bp.route("/notebook")
def notebook_ui():
    """NotebookLM 风格界面"""
    return render_template("notebook_lm.html")
