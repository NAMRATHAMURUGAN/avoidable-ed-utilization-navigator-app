"""Application factory for the Flask API and static frontend."""

from pathlib import Path

from flask import Flask, abort, send_from_directory

from backend.config import get_security_settings
from backend.routes.analytics import analytics_blueprint
from backend.routes.auth import auth_blueprint
from backend.routes.ml_results import ml_results_blueprint
from backend.routes.navigation import navigation_blueprint
from backend.routes.patients import patients_blueprint
from backend.routes.providers import providers_blueprint
from backend.routes.triage import triage_blueprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRECTORY = PROJECT_ROOT / "public"


def create_app() -> Flask:
    """Create an application that exposes API routes and the existing frontend."""
    app = Flask(
        __name__,
        static_folder=None,
    )

    # Required to sign Flask session cookies (see backend/routes/auth.py).
    # Fails fast if unset rather than falling back to an insecure default.
    app.config["SECRET_KEY"] = get_security_settings().secret_key

    # Session cookie hardening appropriate for this same-origin, local-HTTP
    # development architecture. SESSION_COOKIE_SECURE is left False so local
    # HTTP development keeps working; set it to True (and serve over HTTPS)
    # in production.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False

    app.register_blueprint(ml_results_blueprint)
    app.register_blueprint(patients_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(providers_blueprint)
    app.register_blueprint(triage_blueprint)
    app.register_blueprint(navigation_blueprint)
    app.register_blueprint(auth_blueprint)

    @app.after_request
    def set_security_headers(response):
        """Minimal, non-breaking security headers.

        No Content-Security-Policy (would risk breaking the existing
        frontend) and no Strict-Transport-Security (inappropriate for local
        HTTP development; add it only once the app is served over HTTPS).
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.get("/")
    def frontend_index():
        return send_from_directory(PUBLIC_DIRECTORY, "index.html")

    @app.get("/<path:frontend_path>")
    def frontend_fallback(frontend_path: str):
        """Return the frontend shell without masking unmatched API requests."""
        if frontend_path == "api" or frontend_path.startswith("api/"):
            abort(404)
        if (PUBLIC_DIRECTORY / frontend_path).is_file():
            return send_from_directory(PUBLIC_DIRECTORY, frontend_path)
        return send_from_directory(PUBLIC_DIRECTORY, "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
