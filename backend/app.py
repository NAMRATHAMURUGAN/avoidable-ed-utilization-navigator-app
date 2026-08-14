"""Application factory for the Flask API and static frontend."""

from pathlib import Path

from flask import Flask, abort, send_from_directory

from backend.routes.ml_results import ml_results_blueprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRECTORY = PROJECT_ROOT / "public"


def create_app() -> Flask:
    """Create an application that exposes API routes and the existing frontend."""
    app = Flask(
        __name__,
        static_folder=None,
    )
    app.register_blueprint(ml_results_blueprint)

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
