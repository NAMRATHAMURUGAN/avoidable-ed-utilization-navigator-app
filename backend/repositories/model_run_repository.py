"""Framework-independent model-run metadata access."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.model_run import ModelRun


class ModelRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, model_run_id: int) -> ModelRun | None:
        return self._session.scalar(
            select(ModelRun).where(ModelRun.model_run_id == model_run_id)
        )

    def add(self, model_run: ModelRun) -> ModelRun:
        self._session.add(model_run)
        return model_run
