#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 11 - User Authentication & Authorization
RBAC (Role-Based Access Control), user management, and permission enforcement

This module provides:
1. User account management
2. Role-based access control (RBAC)
3. Permission management
4. User session management
5. Multi-factor authentication (MFA) framework
6. Audit logging
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class UserRole(Enum):
    """User roles"""

    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"
    GUEST = "guest"


class Permission(Enum):
    """System permissions"""

    # User management
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # Data management
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"

    # Admin operations
    ADMIN_ACCESS = "admin:access"
    SYSTEM_CONFIG = "system:config"
    AUDIT_LOG = "audit:log"


@dataclass
class User:
    """User account"""

    user_id: str
    username: str
    email: str
    password_hash: str
    roles: List[UserRole] = field(default_factory=lambda: [UserRole.USER])
    is_active: bool = True
    is_verified: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_login: Optional[str] = None
    mfa_enabled: bool = False


@dataclass
class Role:
    """User role with permissions"""

    role_id: str
    name: UserRole
    permissions: Set[Permission] = field(default_factory=set)
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Session:
    """User session"""

    session_id: str
    user_id: str
    created_at: str
    expires_at: str
    ip_address: str
    user_agent: str = ""
    is_active: bool = True

    def is_expired(self) -> bool:
        """Check if session is expired"""
        expiry = datetime.fromisoformat(self.expires_at)
        return datetime.now() > expiry


@dataclass
class AuditLogEntry:
    """Audit log entry"""

    entry_id: str
    user_id: str
    action: str
    resource: str
    old_value: Optional[Dict] = None
    new_value: Optional[Dict] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "success"
    ip_address: str = ""


class RoleManager:
    """Manage roles and permissions"""

    def __init__(self):
        self.roles: Dict[str, Role] = {}
        self._initialize_default_roles()

    def _initialize_default_roles(self):
        """Initialize default roles"""
        # Admin role
        admin_role = Role(
            role_id="admin",
            name=UserRole.ADMIN,
            description="Administrator with full access",
            permissions={perm for perm in Permission},
        )
        self.roles["admin"] = admin_role

        # Moderator role
        moderator_role = Role(
            role_id="moderator",
            name=UserRole.MODERATOR,
            description="Moderator with data management rights",
            permissions={
                Permission.USER_READ,
                Permission.DATA_READ,
                Permission.DATA_WRITE,
                Permission.AUDIT_LOG,
            },
        )
        self.roles["moderator"] = moderator_role

        # User role
        user_role = Role(
            role_id="user",
            name=UserRole.USER,
            description="Regular user",
            permissions={
                Permission.USER_READ,
                Permission.DATA_READ,
                Permission.DATA_WRITE,
            },
        )
        self.roles["user"] = user_role

        # Guest role
        guest_role = Role(
            role_id="guest",
            name=UserRole.GUEST,
            description="Guest user with read-only access",
            permissions={Permission.USER_READ, Permission.DATA_READ},
        )
        self.roles["guest"] = guest_role

    def create_role(
        self, role_id: str, name: str, permissions: Set[Permission]
    ) -> Role:
        """Create custom role"""
        role = Role(
            role_id=role_id,
            name=UserRole.USER,  # Custom name
            description=name,
            permissions=permissions,
        )
        self.roles[role_id] = role
        return role

    def get_role_permissions(self, role_name: UserRole) -> Set[Permission]:
        """Get permissions for role"""
        role = self.roles.get(role_name.value)
        return role.permissions if role else set()

    def add_permission_to_role(self, role_id: str, permission: Permission) -> bool:
        """Add permission to role"""
        if role_id in self.roles:
            self.roles[role_id].permissions.add(permission)
            return True
        return False


class UserManager:
    """Manage user accounts"""

    def __init__(self):
        self.users: Dict[str, User] = {}
        self.email_index: Dict[str, str] = {}
        self.role_manager = RoleManager()

    def create_user(
        self,
        user_id: str,
        username: str,
        email: str,
        password_hash: str,
        roles: List[UserRole] = None,
    ) -> User:
        """Create new user"""
        user = User(
            user_id=user_id,
            username=username,
            email=email,
            password_hash=password_hash,
            roles=roles or [UserRole.USER],
        )
        self.users[user_id] = user
        self.email_index[email] = user_id
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID"""
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email"""
        user_id = self.email_index.get(email)
        return self.users.get(user_id) if user_id else None

    def update_user(self, user_id: str, **kwargs) -> bool:
        """Update user"""
        if user_id not in self.users:
            return False

        user = self.users[user_id]
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        return True

    def deactivate_user(self, user_id: str) -> bool:
        """Deactivate user"""
        if user_id in self.users:
            self.users[user_id].is_active = False
            return True
        return False

    def add_role_to_user(self, user_id: str, role: UserRole) -> bool:
        """Add role to user"""
        if user_id in self.users:
            if role not in self.users[user_id].roles:
                self.users[user_id].roles.append(role)
            return True
        return False

    def has_permission(self, user_id: str, permission: Permission) -> bool:
        """Check if user has permission"""
        user = self.users.get(user_id)
        if not user or not user.is_active:
            return False

        for role in user.roles:
            permissions = self.role_manager.get_role_permissions(role)
            if permission in permissions:
                return True

        return False

    def get_user_stats(self) -> Dict[str, Any]:
        """Get user statistics"""
        active_users = sum(1 for u in self.users.values() if u.is_active)
        verified_users = sum(1 for u in self.users.values() if u.is_verified)
        mfa_enabled = sum(1 for u in self.users.values() if u.mfa_enabled)

        return {
            "total_users": len(self.users),
            "active_users": active_users,
            "verified_users": verified_users,
            "mfa_enabled": mfa_enabled,
        }


class SessionManager:
    """Manage user sessions"""

    def __init__(self, session_timeout_hours: int = 24):
        self.sessions: Dict[str, Session] = {}
        self.session_timeout_hours = session_timeout_hours
        self.user_sessions: Dict[str, List[str]] = {}  # user_id -> [session_ids]

    def create_session(self, session_id: str, user_id: str, ip_address: str) -> Session:
        """Create new session"""
        now = datetime.now()
        expires_at = now + timedelta(hours=self.session_timeout_hours)

        session = Session(
            session_id=session_id,
            user_id=user_id,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            ip_address=ip_address,
        )

        self.sessions[session_id] = session

        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = []
        self.user_sessions[user_id].append(session_id)

        return session

    def validate_session(self, session_id: str) -> bool:
        """Validate session"""
        if session_id not in self.sessions:
            return False

        session = self.sessions[session_id]
        if session.is_expired():
            session.is_active = False
            return False

        return session.is_active

    def terminate_session(self, session_id: str) -> bool:
        """Terminate session"""
        if session_id in self.sessions:
            self.sessions[session_id].is_active = False
            return True
        return False

    def terminate_user_sessions(self, user_id: str) -> int:
        """Terminate all sessions for user"""
        count = 0
        if user_id in self.user_sessions:
            for session_id in self.user_sessions[user_id]:
                if self.terminate_session(session_id):
                    count += 1
        return count

    def get_active_sessions_count(self) -> int:
        """Get count of active sessions"""
        return sum(
            1 for s in self.sessions.values() if s.is_active and not s.is_expired()
        )


class AuditLogger:
    """Audit logging system"""

    def __init__(self):
        self.logs: Dict[str, AuditLogEntry] = {}
        self.user_logs: Dict[str, List[str]] = {}  # user_id -> [log_ids]

    def log_action(
        self,
        entry_id: str,
        user_id: str,
        action: str,
        resource: str,
        old_value: Dict = None,
        new_value: Dict = None,
        status: str = "success",
        ip_address: str = "",
    ) -> AuditLogEntry:
        """Log action"""
        entry = AuditLogEntry(
            entry_id=entry_id,
            user_id=user_id,
            action=action,
            resource=resource,
            old_value=old_value,
            new_value=new_value,
            status=status,
            ip_address=ip_address,
        )

        self.logs[entry_id] = entry

        if user_id not in self.user_logs:
            self.user_logs[user_id] = []
        self.user_logs[user_id].append(entry_id)

        return entry

    def get_user_log(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all logs for user"""
        if user_id not in self.user_logs:
            return []

        return [asdict(self.logs[log_id]) for log_id in self.user_logs[user_id]]

    def get_audit_stats(self) -> Dict[str, Any]:
        """Get audit statistics"""
        success_count = sum(1 for l in self.logs.values() if l.status == "success")
        failure_count = sum(1 for l in self.logs.values() if l.status == "failure")

        return {
            "total_entries": len(self.logs),
            "successful_actions": success_count,
            "failed_actions": failure_count,
            "tracked_users": len(self.user_logs),
        }


class AuthenticationManager:
    """Central authentication manager"""

    def __init__(self):
        self.user_manager = UserManager()
        self.session_manager = SessionManager()
        self.audit_logger = AuditLogger()

    def get_auth_status(self) -> Dict[str, Any]:
        """Get authentication status"""
        return {
            "users": self.user_manager.get_user_stats(),
            "sessions": self.session_manager.get_active_sessions_count(),
            "audit": self.audit_logger.get_audit_stats(),
        }


# Example usage
if __name__ == "__main__":
    auth = AuthenticationManager()

    # Create user
    user = auth.user_manager.create_user(
        "usr_001", "john_doe", "john@example.com", "hashed_pass", [UserRole.USER]
    )
    logger.info(f"User created: {user.username}")

    # Create session
    session = auth.session_manager.create_session("sess_001", "usr_001", "192.168.1.1")
    logger.info(f"Session created: {session.session_id}")

    # Check permission
    has_perm = auth.user_manager.has_permission("usr_001", Permission.DATA_READ)
    logger.info(f"Has DATA_READ permission: {has_perm}")

    # Log action
    auth.audit_logger.log_action(
        "audit_001",
        "usr_001",
        "login",
        "user_session",
        status="success",
        ip_address="192.168.1.1",
    )

    # Get status
    logger.info(json.dumps(auth.get_auth_status(), indent=2))
