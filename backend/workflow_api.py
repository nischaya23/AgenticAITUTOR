"""Read-only, account-scoped workflow inspectors."""
from fastapi import APIRouter, HTTPException, Query, Request
from . import db as database, workflow
from .db import WorkflowRun, WorkflowArtifact, Operation

router = APIRouter()


@router.get('/api/sessions/{sid}/workflow')
def session_history(sid: int, request: Request, before: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100)):
    with database.SessionLocal() as db:
        return workflow.history(db, request.state.user_id, session_id=sid, before=before, limit=limit)


@router.get('/api/sessions/{sid}/workflow/events')
def session_events(sid: int, request: Request, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    with database.SessionLocal() as db:
        return workflow.replay(db, request.state.user_id, session_id=sid, after=after, limit=limit)


@router.get('/api/workflow/runs/{rid}')
def run_detail(rid: str, request: Request):
    with database.SessionLocal() as db:
        row = db.get(WorkflowRun, rid)
        if row is None or row.user_id != request.state.user_id:
            raise HTTPException(404, 'Workflow run not found.')
        return workflow.detail(db, row)


@router.get('/api/workflow/artifacts/{aid}')
def artifact_detail(aid: str, request: Request):
    with database.SessionLocal() as db:
        row = db.get(WorkflowArtifact, aid)
        if row is None or row.user_id != request.state.user_id:
            raise HTTPException(404, 'Workflow artifact not found.')
        return dict(id=row.id, kind=row.kind, data=row.data, created_at=workflow.iso(row.created_at))


@router.get('/api/operations/{oid}/workflow')
def operation_history(oid: str, request: Request, before: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100), after: int = Query(0, ge=0)):
    with database.SessionLocal() as db:
        row = db.get(Operation, oid)
        if row is None or row.user_id != request.state.user_id:
            raise HTTPException(404, 'Operation not found.')
        return {**workflow.history(db, request.state.user_id, operation_id=oid, before=before, limit=limit),
                'replay': workflow.replay(db, request.state.user_id, operation_id=oid, after=after)}
