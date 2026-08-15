"""Public API routes for provider directory endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services import provider_service

providers_blueprint = Blueprint("providers", __name__, url_prefix="/api/providers")


@providers_blueprint.get("", strict_slashes=False)
def get_providers():
    """GET /api/providers - Return provider directory filtered by care type and max distance."""
    provider_type = request.args.get("type")
    max_dist_str = request.args.get("maxDistance")

    max_distance: float | None = None
    if max_dist_str is not None:
        try:
            max_distance = float(max_dist_str)
        except (TypeError, ValueError):
            max_distance = None

    results = provider_service.get_providers(
        provider_type=provider_type,
        max_distance=max_distance,
    )
    return jsonify(results)
