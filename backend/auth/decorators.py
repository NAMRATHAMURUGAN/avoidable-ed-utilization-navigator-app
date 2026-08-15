"""Flask view decorators enforcing session-based authentication and role checks.

These decorators only gate access to application functionality. They never
call, alter, or otherwise influence backend/safety/engine.py, and they carry
no clinical meaning whatsoever.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import g, jsonify

from backend.services.auth_service import get_current_user


def _unauthenticated_response():
    return jsonify({"error": "Authentication required."}), 401


def _forbidden_response():
    return jsonify({"error": "You do not have permission to access this resource."}), 403


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Require a valid Flask session; attach the user to ``g.current_user``.

    Returns 401 JSON when no authenticated session is present.
    """

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if user is None:
            return _unauthenticated_response()
        g.current_user = user
        return view(*args, **kwargs)

    return wrapped


def role_required(required_role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Require a valid session AND that the user's role matches ``required_role``.

    Returns 401 JSON when unauthenticated, 403 JSON when authenticated but the
    role does not match.
    """

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = get_current_user()
            if user is None:
                return _unauthenticated_response()
            if user.role != required_role:
                return _forbidden_response()
            g.current_user = user
            return view(*args, **kwargs)

        return wrapped

    return decorator
