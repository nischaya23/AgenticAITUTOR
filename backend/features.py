"""Memory controls and preparation jobs."""
import asyncio
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from . import utils, memory, context, knowledge, research, websearch, operations
from .db import Operation

router = APIRouter()

class Correction(utils.Schema):
    expected_revision: int = Field(ge=0)
    action: Literal['edit','dispute','add_goal']
    item_id: str | None = None
    text: str = Field(min_length=1,max_length=2000)

    @field_validator('text')
    @classmethod
    def nonempty(cls,value):
        if not value.strip(): raise ValueError('Enter a correction.')
        return value.strip()


@router.get('/api/sessions/{sid}/memory')
def read_memory(sid: int):
    from .main import SessionLocal
    with SessionLocal.begin() as db:
        return memory.memory_snapshot(db,sid)


@router.post('/api/sessions/{sid}/memory/corrections')
async def correct(sid: int, body: Correction):
    from .main import SessionLocal, memory_changed
    try:
        with SessionLocal.begin() as db:
            result=memory.correct_memory(db,sid,body)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    await memory_changed(sid)
    return result


class TopicRequest(utils.Schema):
    description: str = Field(min_length=2,max_length=10000)

    @field_validator('description')
    @classmethod
    def trimmed(cls,value):
        if len(value.strip())<2: raise ValueError('Describe what you want to learn.')
        return value.strip()


async def prepare_topic(user_id, description, meaning=None):
    from .main import SessionLocal, build_material
    with SessionLocal() as db:
        learning_context=context.preparation(db,user_id,description)
    if meaning is None:
        await operations.progress('interpreting_topic', message='Interpreting your topic.')
        meanings=await websearch.interpret_topic(description, learning_context=learning_context)
        if len(meanings)>1:
            return dict(type='choices',meanings=[m.model_dump() for m in meanings])
        meaning=meanings[0]
    pages=await research.investigate({**learning_context,'subject':meaning.model_dump()},required=True,budget=3)
    if not pages:
        raise ValueError('No usable sources found for this topic. Your description is saved; retry or upload notes.')
    documents=[(p['title'],p['text'].encode(),p['text'],p['url']) for p in pages]
    return await build_material(user_id,documents,dict(name=meaning.label,origin='topic',topic=meaning.label,
        topic_description=meaning.description,learning_request=description,web_enhance=True,
        web_sources=[dict(title=p['title'],url=p['url']) for p in pages]),
        focus=f'{meaning.label}: {meaning.description}',do_research=False)


@router.post('/api/material-jobs/topic',status_code=202)
async def topic_job(request: Request, body: TopicRequest):
    from .main import SessionLocal
    uid=request.state.user_id
    oid=operations.create(SessionLocal,uid,'topic',dict(description=body.description))
    operations.spawn(SessionLocal,oid,lambda:prepare_topic(uid,body.description))
    return dict(id=oid)


@router.post('/api/material-jobs',status_code=202)
async def upload_job(request: Request, files: list[UploadFile] = File(...)):
    from .main import SessionLocal,build_material
    from . import config
    if not 1<=len(files)<=config.MAX_UPLOAD_FILES:
        raise HTTPException(400,'Choose between 1 and 10 files.')
    raw=[]
    for upload in files:
        body=await upload.read(config.MAX_UPLOAD_BYTES+1)
        if len(body)>config.MAX_UPLOAD_BYTES: raise HTTPException(413,'Upload limit is 8 MiB per file.')
        raw.append((Path(upload.filename or 'notes.txt').name,body))
    uid=request.state.user_id
    oid=operations.create(SessionLocal,uid,'upload')
    async def work():
        await operations.progress('reading',message='Reading your uploaded material.')
        documents=[]
        for name,body in raw:
            text=await asyncio.to_thread(knowledge.extract_text,name,body)
            documents.append((name,body,text))
        if sum(len(d[2]) for d in documents)>600000:
            raise ValueError('Selected materials are too long together.')
        return await build_material(uid,documents)
    operations.spawn(SessionLocal,oid,work)
    return dict(id=oid)


@router.get('/api/material-jobs/{oid}')
@router.get('/api/operations/{oid}')
def operation(request: Request, oid: str):
    from .main import SessionLocal
    data=operations.snapshot(SessionLocal,oid,request.state.user_id)
    if data is None: raise HTTPException(404,'Operation not found.')
    return data


@router.get('/api/material-jobs/{oid}/events')
@router.get('/api/operations/{oid}/events')
async def operation_events(request: Request,oid: str,after: int=0,workflow_after: int | None=None):
    from .main import SessionLocal
    if operations.snapshot(SessionLocal,oid,request.state.user_id) is None:
        raise HTTPException(404,'Operation not found.')
    try: after=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError: raise HTTPException(400,'Invalid event cursor.')
    return StreamingResponse(operations.events(SessionLocal,oid,request.state.user_id,after,workflow_after),
        media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-cache'})


class MeaningChoice(utils.Schema):
    index: int = Field(ge=0)

@router.post('/api/material-jobs/{oid}/meaning',status_code=202)
async def choose_meaning(request: Request,oid: str,body: MeaningChoice):
    from .main import SessionLocal
    with SessionLocal.begin() as db:
        row=db.get(Operation,oid)
        if row is None or row.user_id!=request.state.user_id: raise HTTPException(404,'Operation not found.')
        if row.status!='needs_clarification': raise HTTPException(409,'This topic is no longer awaiting a choice.')
        meanings=row.data['result']['meanings']
        if body.index>=len(meanings): raise HTTPException(422,'Select one of the displayed meanings.')
        meaning=websearch.Meaning.model_validate(meanings[body.index])
        description=row.data['description']; uid=row.user_id
        row.status='running'
    operations.spawn(SessionLocal,oid,lambda:prepare_topic(uid,description,meaning))
    return dict(id=oid)


@router.post('/api/material-jobs/{oid}/retry',status_code=202)
async def retry_topic(request: Request,oid: str):
    from .main import SessionLocal
    with SessionLocal.begin() as db:
        row=db.get(Operation,oid)
        if row is None or row.user_id!=request.state.user_id: raise HTTPException(404,'Operation not found.')
        if row.kind!='topic' or row.status not in ('failed','interrupted'):
            raise HTTPException(409,'Reselect files or wait for the current preparation.')
        uid=row.user_id; description=row.data['description']; row.status='running'
    operations.spawn(SessionLocal,oid,lambda:prepare_topic(uid,description))
    return dict(id=oid)
