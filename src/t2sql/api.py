"""FastAPI service.

POST /ask                     {"question": "...", "session_id": "optional"} -> SQL, rows, summary, chart
GET  /sessions/{session_id}   conversation history
DELETE /sessions/{session_id} clear history
GET  /schema                  tables, columns and descriptions
GET  /health
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .service import TextToSQLService


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


def create_app(service: TextToSQLService | None = None) -> FastAPI:
    app = FastAPI(title="Text-to-SQL Analytics Agent", version=__version__)
    svc = service or TextToSQLService()

    def check_key(x_api_key: str | None = Header(default=None)) -> None:
        if svc.settings.api_key and x_api_key != svc.settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "engine": svc.engine_name, "dialect": svc.dialect, "tables": len(svc.tables)}

    @app.post("/ask", dependencies=[Depends(check_key)])
    def ask(req: AskRequest) -> dict:
        return svc.ask(req.question, req.session_id).to_dict()

    @app.get("/sessions/{session_id}", dependencies=[Depends(check_key)])
    def history(session_id: str) -> dict:
        turns = svc.memory.history(session_id, limit=50)
        return {"session_id": session_id, "turns": [{k: (str(v) if k == "created_at" else v) for k, v in t.items()} for t in turns]}

    @app.delete("/sessions/{session_id}", dependencies=[Depends(check_key)])
    def clear(session_id: str) -> dict:
        svc.memory.clear(session_id)
        return {"cleared": session_id}

    @app.get("/schema", dependencies=[Depends(check_key)])
    def schema() -> dict:
        return svc.schema()

    return app


def app_factory() -> FastAPI:
    return create_app()
