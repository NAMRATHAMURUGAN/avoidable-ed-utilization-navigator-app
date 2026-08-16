"""Public API routes for registration, login, logout, and session identity.

A ``User`` here is a login identity, distinct from a CMS ``Member``/
beneficiary record used elsewhere in the application. Blueprint prefix:
/api/auth
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from backend.auth.decorators import login_required
from backend.services.auth_service import (
    AuthenticationError,
    AuthValidationError,
    authenticate_user,
    clear_session,
    create_session,
    register_user,
    user_to_safe_dict,
)

auth_blueprint = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_blueprint.post("/register", strict_slashes=False)
def register():
    """POST /api/auth/register - Create a new PATIENT login identity.

    Public self-registration only ever creates a PATIENT account. PAYER
    remains a valid stored role (see backend/models/user.py and
    role_required), but it is intentionally not self-service: a caller
    requesting anything other than PATIENT is rejected outright rather than
    silently downgraded, so an attempted role-escalation is never masked as
    an ordinary PATIENT signup.
    """
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    data = request.get_json(silent=True)
    if data is None:
        data = {}

    requested_role = data.get("role")
    if requested_role is not None and str(requested_role).strip().upper() != "PATIENT":
        return jsonify({"error": "Public registration is only available for the PATIENT role."}), 400

    try:
        user = register_user(
            email=data.get("email"),
            password=data.get("password"),
            role="PATIENT",
        )
    except AuthValidationError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify(user_to_safe_dict(user)), 201


@auth_blueprint.post("/login", strict_slashes=False)
def login():
    """POST /api/auth/login - Verify credentials and start a Flask session."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    data = request.get_json(silent=True)
    if data is None:
        data = {}

    try:
        user = authenticate_user(email=data.get("email"), password=data.get("password"))
    except (AuthenticationError, AuthValidationError):
        # Malformed/missing input (e.g. no "@" in the email) and genuinely
        # wrong credentials must be indistinguishable to the caller: both
        # collapse to the same generic 401 so login never leaks internal
        # validation detail or becomes an unhandled 500.
        return jsonify({"error": "Invalid email or password."}), 401

    create_session(user)
    return jsonify(user_to_safe_dict(user)), 200


@auth_blueprint.post("/logout", strict_slashes=False)
def logout():
    """POST /api/auth/logout - Clear the current Flask session."""
    clear_session()
    return jsonify({"status": "logged_out"}), 200


@auth_blueprint.get("/me", strict_slashes=False)
@login_required
def me():
    """GET /api/auth/me - Return the current authenticated user's safe identity."""
    return jsonify(user_to_safe_dict(g.current_user)), 200
