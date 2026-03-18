"""
Koto Auth - 用户认证与会话管理
支持 JWT token 认证，用于 SaaS 部署
"""

import hashlib
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Dict

# JWT 依赖（可选降级到简单 token）
try:
    import jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False

from flask import g, jsonify, request

logger = logging.getLogger(__name__)

# ── 配置 ──
AUTH_ENABLED = os.environ.get("KOTO_AUTH_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
DEPLOY_MODE = os.environ.get("KOTO_DEPLOY_MODE", "local")

if not AUTH_ENABLED:
    logger.warning(
        "⚠️ Authentication is DISABLED. Set KOTO_AUTH_ENABLED=true for production."
    )


def _validate_jwt_secret() -> str:
    """Read and validate KOTO_JWT_SECRET. Returns the secret to use.

    Raises:
        RuntimeError: In cloud mode when KOTO_JWT_SECRET is not set.
    """
    secret = os.environ.get("KOTO_JWT_SECRET", "")
    if not secret:
        if os.environ.get("KOTO_DEPLOY_MODE", "local") == "cloud":
            raise RuntimeError(
                "KOTO_JWT_SECRET environment variable must be set in cloud/production mode. "
                'Generate one with: python -c "import secrets; logger.info(secrets.token_hex(32))"'
            )
        # Local dev: generate ephemeral secret (tokens invalidate on restart — acceptable locally)
        logger.warning(
            "[auth] KOTO_JWT_SECRET not set — generating ephemeral secret. "
            "All tokens will invalidate on restart. Set KOTO_JWT_SECRET for persistent sessions."
        )
        secret = secrets.token_hex(32)
    return secret


JWT_SECRET = _validate_jwt_secret()
JWT_EXPIRY_HOURS = int(os.environ.get("KOTO_JWT_EXPIRY_HOURS", "72"))
USERS_FILE = os.environ.get("KOTO_USERS_FILE", "config/users.json")
MAX_DAILY_REQUESTS = int(os.environ.get("KOTO_MAX_DAILY_REQUESTS", "100"))
ADMIN_TOKEN = os.environ.get("KOTO_ADMIN_TOKEN", "")


def _hash_password(password: str, salt: str = None) -> tuple:
    """安全密码哈希"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return hashed.hex(), salt


def _load_users() -> dict:
    """加载用户数据"""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load users: %s", e)
        return {}


def _save_users(users: dict):
    """保存用户数据"""
    os.makedirs(os.path.dirname(USERS_FILE) or ".", exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _generate_token(user_id: str, email: str) -> str:
    """生成 JWT token"""
    payload = {
        "user_id": user_id,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    if HAS_JWT:
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    else:
        # 简单 token 降级
        import base64

        token_data = json.dumps(payload).encode()
        sig = hashlib.sha256(token_data + JWT_SECRET.encode()).hexdigest()[:16]
        return base64.urlsafe_b64encode(token_data).decode() + "." + sig


def _verify_token(token: str) -> dict:
    """验证 JWT token，返回 payload 或 None"""
    if not token:
        return None
    try:
        if HAS_JWT:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        else:
            import base64

            parts = token.rsplit(".", 1)
            if len(parts) != 2:
                return None
            token_data = base64.urlsafe_b64decode(parts[0])
            sig = hashlib.sha256(token_data + JWT_SECRET.encode()).hexdigest()[:16]
            if sig != parts[1]:
                return None
            payload = json.loads(token_data)
            if payload.get("exp", 0) < time.time():
                return None
            return payload
    except Exception as e:
        logger.debug("Token verification failed: %s", e)
        return None


# ── 3-tier sliding-window rate limiting ──
_rate_buckets: Dict[str, list] = {}  # { user_id: [timestamp, ...] }

_RATE_TIERS = {
    "strict": {"window": 60, "max_requests": 10},
    "standard": {"window": 60, "max_requests": 30},
    "relaxed": {"window": 60, "max_requests": 120},
}


def _check_rate(user_id: str, tier: str = "standard") -> bool:
    """Return True if the request is within rate limits for the given tier."""
    cfg = _RATE_TIERS.get(tier, _RATE_TIERS["standard"])
    now = time.time()
    window = cfg["window"]
    max_req = cfg["max_requests"]

    if user_id not in _rate_buckets:
        _rate_buckets[user_id] = []

    # Slide: drop entries older than the window
    _rate_buckets[user_id] = [t for t in _rate_buckets[user_id] if now - t < window]

    if len(_rate_buckets[user_id]) >= max_req:
        return False

    _rate_buckets[user_id].append(now)
    return True


def rate_limit(tier: str = "standard"):
    """Decorator that applies sliding-window rate limiting to a route."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            uid = getattr(g, "user_id", request.remote_addr or "anon")
            if not _check_rate(uid, tier):
                cfg = _RATE_TIERS.get(tier, _RATE_TIERS["standard"])
                return (
                    jsonify(
                        {
                            "error": f"Rate limit exceeded ({cfg['max_requests']} req/{cfg['window']}s)",
                            "code": "RATE_LIMIT",
                        }
                    ),
                    429,
                )
            return f(*args, **kwargs)

        return wrapper

    return decorator


# ── Flask 中间件 ──


def require_auth(f):
    """装饰器：需要认证的路由"""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_ENABLED:
            g.user_id = "local"
            g.user_email = "local@koto.ai"
            return f(*args, **kwargs)

        # 从 header 或 cookie 获取 token
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get("koto_token")

        payload = _verify_token(token)
        if not payload:
            return jsonify({"error": "未登录或登录已过期", "code": "UNAUTHORIZED"}), 401

        user_id = payload.get("user_id", "")

        # 频率限制
        if not _check_rate(user_id, "standard"):
            return (
                jsonify(
                    {
                        "error": "Rate limit exceeded",
                        "code": "RATE_LIMIT",
                    }
                ),
                429,
            )

        g.user_id = user_id
        g.user_email = payload.get("email", "")
        return f(*args, **kwargs)

    return decorated


def optional_auth(f):
    """装饰器：可选认证（本地模式不需要，云模式需要）"""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_ENABLED:
            g.user_id = "local"
            g.user_email = "local@koto.ai"
            return f(*args, **kwargs)
        return require_auth(f)(*args, **kwargs)

    return decorated


# ── Auth API 路由注册 ──


def register_auth_routes(app):
    """注册认证相关的 API 路由"""

    @app.route("/api/auth/register", methods=["POST"])
    def auth_register():
        """用户注册"""
        if not AUTH_ENABLED:
            return jsonify({"error": "本地模式无需注册"}), 400

        data = request.get_json(force=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password", "")
        name = data.get("name", "").strip()

        if not email or "@" not in email:
            return jsonify({"error": "请输入有效的邮箱地址"}), 400
        if len(password) < 6:
            return jsonify({"error": "密码至少6位"}), 400

        users = _load_users()
        if email in users:
            return jsonify({"error": "该邮箱已注册"}), 409

        hashed, salt = _hash_password(password)
        user_id = secrets.token_hex(8)
        users[email] = {
            "user_id": user_id,
            "name": name or email.split("@")[0],
            "password_hash": hashed,
            "salt": salt,
            "created_at": datetime.now().isoformat(),
            "plan": "free",
            "daily_limit": MAX_DAILY_REQUESTS,
        }
        _save_users(users)

        token = _generate_token(user_id, email)
        return jsonify(
            {
                "success": True,
                "token": token,
                "user": {
                    "user_id": user_id,
                    "email": email,
                    "name": users[email]["name"],
                    "plan": "free",
                },
            }
        )

    @app.route("/api/auth/login", methods=["POST"])
    def auth_login():
        """User login — returns JWT token.
        ---
        tags: [Auth]
        parameters:
          - in: body
            name: credentials
            schema:
              required: [email, password]
              properties:
                email: {type: string, format: email}
                password: {type: string, format: password}
        responses:
          200:
            description: Login successful
            schema:
              properties:
                success: {type: boolean}
                token: {type: string}
                user:
                  properties:
                    user_id: {type: string}
                    email: {type: string}
                    name: {type: string}
                    plan: {type: string}
          401:
            description: Invalid credentials
          429:
            description: Rate limit exceeded
        """
        if not AUTH_ENABLED:
            return jsonify(
                {
                    "success": True,
                    "token": "local",
                    "user": {
                        "user_id": "local",
                        "email": "local@koto.ai",
                        "name": "Local User",
                        "plan": "unlimited",
                    },
                }
            )

        data = request.get_json(force=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password", "")

        users = _load_users()
        user = users.get(email)
        if not user:
            return jsonify({"error": "邮箱或密码错误"}), 401

        hashed, _ = _hash_password(password, user["salt"])
        if hashed != user["password_hash"]:
            return jsonify({"error": "邮箱或密码错误"}), 401

        token = _generate_token(user["user_id"], email)
        return jsonify(
            {
                "success": True,
                "token": token,
                "user": {
                    "user_id": user["user_id"],
                    "email": email,
                    "name": user["name"],
                    "plan": user.get("plan", "free"),
                },
            }
        )

    @app.route("/api/auth/me", methods=["GET"])
    @require_auth
    def auth_me():
        """获取当前用户信息"""
        users = _load_users()
        for email, user in users.items():
            if user["user_id"] == g.user_id:
                used = len(_rate_buckets.get(g.user_id, []))
                return jsonify(
                    {
                        "user_id": g.user_id,
                        "email": email,
                        "name": user["name"],
                        "plan": user.get("plan", "free"),
                        "daily_limit": user.get("daily_limit", MAX_DAILY_REQUESTS),
                        "used_today": used,
                    }
                )
        return jsonify({"user_id": g.user_id, "email": g.user_email, "plan": "free"})

    @app.route("/api/auth/logout", methods=["POST"])
    def auth_logout():
        """登出（客户端清除 token 即可）"""
        return jsonify({"success": True})

    @app.route("/api/auth/status", methods=["GET"])
    def auth_status():
        """返回认证系统状态（供前端判断是否需要登录）"""
        return jsonify(
            {
                "auth_enabled": AUTH_ENABLED,
                "mode": "cloud" if AUTH_ENABLED else "local",
            }
        )

    logger.warning(
        f"[Auth] {'✅ 认证系统已启用' if AUTH_ENABLED else '⚠️ 本地模式（无认证）'}"
    )
