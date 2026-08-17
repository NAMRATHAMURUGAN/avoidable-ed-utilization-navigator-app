"""API routes for the authenticated caller's own self-entered patient profile.

Both routes are scoped entirely to ``g.current_user.id`` (set by
@login_required) -- there is no request parameter carrying a user id, so a
caller can never read or write another user's profile. Blueprint prefix:
/api/profile
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from backend.auth.decorators import login_required
from backend.services.patient_profile_service import (
    ProfileValidationError,
    get_profile,
    save_profile,
)

profile_blueprint = Blueprint("profile", __name__, url_prefix="/api/profile")


@profile_blueprint.get("", strict_slashes=False)
@login_required
def get_my_profile():
    """GET /api/profile - Return the authenticated caller's own profile."""
    return jsonify(get_profile(g.current_user.id)), 200


@profile_blueprint.put("", strict_slashes=False)
@login_required
def update_my_profile():
    """PUT /api/profile - Create or update the authenticated caller's own profile."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Missing JSON request payload"}), 400

    try:
        result = save_profile(g.current_user.id, data)
    except ProfileValidationError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify(result), 200
