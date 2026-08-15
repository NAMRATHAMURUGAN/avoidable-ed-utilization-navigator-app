"""Application factory for the Flask API and static frontend."""

from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from backend.routes.analytics import analytics_blueprint
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
    app.register_blueprint(ml_results_blueprint)
    app.register_blueprint(patients_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(providers_blueprint)
    app.register_blueprint(triage_blueprint)
    app.register_blueprint(navigation_blueprint)

    @app.errorhandler(HTTPException)
    def json_http_error(error: HTTPException):
        if error.code in {400, 404, 405, 415}:
            return jsonify({"error": error.description}), error.code
        return jsonify({"error": "Request could not be completed"}), error.code

    @app.errorhandler(SQLAlchemyError)
    def json_database_error(_error: SQLAlchemyError):
        return jsonify({"error": "Database service is unavailable"}), 503

    @app.errorhandler(RuntimeError)
    def json_runtime_error(_error: RuntimeError):
        return jsonify({"error": "Service configuration is unavailable"}), 503

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
