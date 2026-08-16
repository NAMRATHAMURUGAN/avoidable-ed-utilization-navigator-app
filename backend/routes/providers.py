"""Public API routes for provider directory endpoints."""

from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from backend.services import provider_service

providers_blueprint = Blueprint("providers", __name__, url_prefix="/api/providers")
VALID_PROVIDER_TYPES = {"ALL", "TELEHEALTH", "URGENT_CARE", "RETAIL_CLINIC", "PRIMARY_CARE"}
MAX_PROVIDER_DISTANCE_MILES = 500.0


@providers_blueprint.get("", strict_slashes=False)
def get_providers():
    """GET /api/providers - Return provider directory filtered by care type and max distance."""
    provider_type = request.args.get("type")
    max_dist_str = request.args.get("maxDistance")

    if provider_type is not None:
        if provider_type not in VALID_PROVIDER_TYPES:
            return jsonify({"error": "type must be one of: " + ", ".join(sorted(VALID_PROVIDER_TYPES))}), 400

    max_distance: float | None = None
    if max_dist_str is not None:
        try:
            max_distance = float(max_dist_str)
        except (TypeError, ValueError):
            return jsonify({"error": "maxDistance must be a finite number"}), 400
        if not math.isfinite(max_distance) or max_distance < 0 or max_distance > MAX_PROVIDER_DISTANCE_MILES:
            return jsonify({"error": f"maxDistance must be between 0 and {MAX_PROVIDER_DISTANCE_MILES:g}"}), 400

    results = provider_service.get_providers(
        provider_type=provider_type,
        max_distance=max_distance,
    )
    return jsonify(results)
