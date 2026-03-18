"""
Session management blueprint.

Routes:
  GET    /api/sessions              — List all chat sessions
  POST   /api/sessions              — Create a new session
  GET    /api/sessions/<name>       — Get session with full history
  DELETE /api/sessions/<name>       — Delete a session
"""

import logging
import time

from flask import Blueprint, jsonify, request

_logger = logging.getLogger("koto.routes.sessions")

sessions_bp = Blueprint("sessions", __name__)


def _get_session_manager():
    """Lazy import to avoid circular dependency with app.py."""
    from web.app import session_manager

    return session_manager


@sessions_bp.route("/api/sessions", methods=["GET"])
def get_sessions():
    """List all chat sessions.
    ---
    tags:
      - Sessions
    responses:
      200:
        description: List of session names
        schema:
          type: object
          properties:
            sessions:
              type: array
              items:
                type: string
    """
    sessions = _get_session_manager().list_sessions()
    return jsonify({"sessions": [s.replace(".json", "") for s in sessions]})


@sessions_bp.route("/api/sessions", methods=["POST"])
def create_session():
    """Create a new chat session.
    ---
    tags:
      - Sessions
    parameters:
      - in: body
        name: body
        schema:
          properties:
            name:
              type: string
              description: Optional session name
    responses:
      200:
        description: Session created
        schema:
          type: object
          properties:
            success:
              type: boolean
            session:
              type: string
    """
    data = request.json
    name = data.get("name", f"chat_{int(time.time())}")
    filename = _get_session_manager().create(name)
    return jsonify({"success": True, "session": filename.replace(".json", "")})


@sessions_bp.route("/api/sessions/<session_name>", methods=["GET"])
def get_session(session_name):
    """Get a specific chat session with full history.
    ---
    tags:
      - Sessions
    parameters:
      - in: path
        name: session_name
        type: string
        required: true
    responses:
      200:
        description: Session data with conversation history
        schema:
          type: object
          properties:
            session:
              type: string
            history:
              type: array
              items:
                type: object
    """
    history = _get_session_manager().load_full(f"{session_name}.json")
    return jsonify({"session": session_name, "history": history})


@sessions_bp.route("/api/sessions/<session_name>", methods=["DELETE"])
def delete_session(session_name):
    """Delete a chat session.
    ---
    tags:
      - Sessions
    parameters:
      - in: path
        name: session_name
        type: string
        required: true
    responses:
      200:
        description: Deletion result
        schema:
          type: object
          properties:
            success:
              type: boolean
    """
    success = _get_session_manager().delete(f"{session_name}.json")
    return jsonify({"success": success})
