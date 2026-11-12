"""Memory controls, preparation jobs and video artifacts."""
import asyncio
import re
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import Field, field_validator
from . import workflow, utils, memory, context, knowledge, research, websearch, operations, video
from .db import Session, Operation, append_event
from .response import content_key, response_for

router = APIRouter()
VIDEO_TASKS = {}

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


@router.get('/api/videos/{key}')
def serve_video(request: Request,key: str):
    from .main import SessionLocal
    if not re.fullmatch('[a-f0-9]{64}',key): raise HTTPException(404,'Video not found.')
    path=video.directory(request.state.user_id)/key/'video.mp4'
    if not path.is_file(): raise HTTPException(404,'Video not found.')
    return FileResponse(path,media_type='video/mp4')


def cancel_videos(sid):
    for task in list(VIDEO_TASKS.get(sid,())): task.cancel()


async def queue_video(job,result,learning_context):
    from .main import SessionLocal,broadcast,memory_changed
    sid=job['session_id']
    spec=video.VideoScript.model_validate(result['metadata'].pop('video_script'))
    ids={p['id'] for p in learning_context.get('knowledge',[])} | {p['id'] for p in learning_context.get('graph_sources',[])}
    if any(ref not in ids for ref in spec.source_ids): raise ValueError('Video must cite supplied sources.')
    with SessionLocal.begin() as db:
        row=db.get(Session,sid); uid=row.user_id
        state=memory.get_short_term_memory(db,sid)
        if state.get('ended') or state.get('current_problem',{}).get('id')!=job['problem'].get('id'):
            return None
        if not job['explicit'] and (state['revision']!=job['revision'] or not state.get('proactive_enabled',True)):
            return None
        if not state.get('plan',{}).get('steps'):
            state['plan']=learning_context['plan']; state['plan_version']=state.get('plan_version',0)+1
        expected_plan=state.get('plan_version',0)
        memory.update_short_term_memory(db,sid,state)
    oid=operations.create(SessionLocal,uid,'video',dict(session_id=sid,title=spec.title,content=spec.content,retry_of=job.get('retry_operation_id'),
        retry_data=dict(job=job,spec=spec.model_dump(),context=learning_context)))
    with SessionLocal.begin() as db:
        memory.update_short_term_memory(db,sid,{'pending_video':oid,'last_video_operation':oid})
    async def work():
        nonlocal spec
        started=utils.now()
        for attempt in range(2):
            try:
                key=await video.render(spec,uid)
                break
            except ValueError as exc:
                if attempt or 'unavailable' in str(exc).lower() or 'setup' in str(exc).lower(): raise
                await operations.progress('repairing',message='Repairing the animation script.')
                spec=await utils.call_llm(video.SCRIPT_INSTRUCTIONS,
                    dict(context=learning_context,previous=spec.model_dump(),error=str(exc)),
                    schema=video.VideoScript,operation='REPAIR_VIDEO',max_output_tokens=6000)
        payload=dict(url=f'/api/videos/{key}',title=spec.title,chapters=[c.model_dump() for c in spec.chapters],
            duration=spec.duration,render_seconds=round(utils.now()-started,2))
        if any(ref not in ids for ref in spec.source_ids): raise ValueError('Repaired animation cited an unknown source.')
        with SessionLocal.begin() as db:
            state=memory.get_short_term_memory(db,sid)
            changed=state.get('current_problem',{}).get('id')!=job['problem'].get('id')
            student=memory.get_long_term_memory(db,uid)
            memory_changed_since=student.get('memory_revision',0)!=learning_context.get('snapshot',{}).get('memory_revision',student.get('memory_revision',0))
            stale=state.get('plan_version',0)!=expected_plan or (not job['explicit'] and (state['revision']!=job['revision'] or not state.get('proactive_enabled') or memory_changed_since))
            if state.get('ended') or changed or stale or state.get('pending_video')!=oid:
                await operations.progress('discarded',message='The question or response changed; this animation was not delivered.')
                return dict(discarded=True)
            final=dict(type='visualization',content=spec.content,metadata=dict(concept=spec.concept,video=payload,source_ids=spec.source_ids,
                retrieved_sources=[dict(id=p['id'],source=p['source'],url=p.get('url')) for p in learning_context['knowledge']]))
            event=append_event(db,sid,'intervention',dict(**final,trigger_event_id=job['event_id'],proactive=not job['explicit']))
            o=state['observer']; o.pop('pending_wake',None)
            old=o.pop('pending_intervention',None) or o.pop('pending_decision',None)
            if old: append_event(db,sid,'intervention_closed',dict(intervention_id=old.get('event_id'),reward=None,evidence='Superseded by delivered video.'))
            o['last_intervention_at']=utils.now(); o['intervention_response_key']=content_key(response_for(state))
            if not job['explicit']:
                o['pending_intervention']=dict(action='WAKE',event_id=event.id,features=o.get('wake_features',{}),tool='visualization',at=utils.now())
            memory.update_short_term_memory(db,sid,{**state,'pending_video':None})
            delivered=dict(type='intervention',event_id=event.id,proactive=not job['explicit'],result=final,revision=state['revision'])
        delivered['workflow_run_id'] = workflow.CURRENT.get().run_id if workflow.CURRENT.get() else None
        await broadcast(sid,delivered)
        workflow.record('delivery.video', event_id=event.id)
        await memory_changed(sid)
        return payload
    async def guarded():
        try: return await work()
        finally:
            with SessionLocal.begin() as db:
                state=memory.get_short_term_memory(db,sid)
                if state.get('pending_video')==oid: memory.update_short_term_memory(db,sid,{'pending_video':None})
    task=operations.spawn(SessionLocal,oid,guarded)
    VIDEO_TASKS.setdefault(sid,set()).add(task)
    task.add_done_callback(VIDEO_TASKS[sid].discard)
    return dict(type='video_pending',operation_id=oid,title=spec.title)


@router.post('/api/operations/{oid}/retry-video',status_code=202)
async def retry_video(request: Request,oid: str):
    from .main import SessionLocal
    data=operations.snapshot(SessionLocal,oid,request.state.user_id)
    if not data or data['kind']!='video': raise HTTPException(404,'Animation not found.')
    if data['status'] not in ('failed','interrupted'): raise HTTPException(409,'Animation is already running or complete.')
    with SessionLocal() as db:
        saved=db.get(Operation,oid).data['retry_data']
    job={**saved['job'],'retry_operation_id':oid};sid=job['session_id']
    with SessionLocal() as db:
        row=db.get(Session,sid)
        if row.user_id!=request.state.user_id or row.state.get('ended') or row.state.get('current_problem',{}).get('id')!=job['problem'].get('id'):
            raise HTTPException(409,'The question changed. Request a new visualization instead.')
    return await queue_video(job,dict(type='visualization',metadata=dict(video_script=saved['spec'])),saved['context'])


@router.get('/api/videos/{key}/captions')
def video_captions(request: Request,key: str):
    if not re.fullmatch('[a-f0-9]{64}',key): raise HTTPException(404,'Video not found.')
    folder=video.directory(request.state.user_id)/key
    if not (folder/'video.mp4').is_file(): raise HTTPException(404,'Video not found.')
    import json
    chapters=json.loads((folder/'script.json').read_text())['chapters']
    def stamp(seconds):
        ms=round(seconds*1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'
    cues=['WEBVTT','']
    for chapter in chapters:
        cues.extend([f"{stamp(chapter['start'])} --> {stamp(chapter['end'])}",chapter['text'].replace('-->','—'), ''])
    return Response('\n'.join(cues),media_type='text/vtt')
