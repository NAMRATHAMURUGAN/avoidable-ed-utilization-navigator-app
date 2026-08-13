"""Application factory for the isolated ML-results Flask service."""

from flask import Flask

from backend.routes.ml_results import ml_results_blueprint


def create_app() -> Flask:
    """Create an application that exposes generated ML results only."""
    app = Flask(__name__)
    app.register_blueprint(ml_results_blueprint)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
