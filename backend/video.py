"""Local Pillow/FFmpeg rendering with bounded interpreted drawing scripts."""
import asyncio
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from pydantic import Field, model_validator
from . import workflow, config, operations, utils

SCRIPT_INSTRUCTIONS = '''
Write executable Python with exactly one public entry point def draw_frame(canvas, t):.
t is seconds. Canvas is 960x540. The wrapper clears each frame. Allowed helpers:
canvas.text(x,y,text,size=24,color='#ffffff'); canvas.rect(x,y,w,h,color='#365c37');
canvas.circle(x,y,r,color='#aacb88'); canvas.line(x1,y1,x2,y2,color='#aacb88',width=3);
canvas.arrow(x1,y1,x2,y2,color='#aacb88'); canvas.plot(points,color='#aacb88');
canvas.lerp(a,b,u); canvas.ease(u). Only import math. No files, networking, external packages,
subprocesses, private attributes, random values or clocks. Use only assignments, for/range loops, if statements, arithmetic and calls to these helpers or math; no while loops, comprehensions, classes, helper functions or mutations of collections. Use readable labels and margins.
Animate a compact learning objective in about 15 seconds, never over 30. Include title, duration,
content, concept, chapters with start/end/text, source_ids from context. Ground the explanation
in supplied sources and the selected strategy. The script is code, not a JSON scene description.
'''


class Chapter(utils.Schema):
    start: float = Field(ge=0)
    end: float = Field(gt=0, le=30)
    text: str = Field(min_length=1, max_length=400)


class VideoScript(utils.Schema):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=3000)
    concept: str = Field(max_length=200)
    duration: float = Field(default=15, ge=2, le=30)
    code: str = Field(min_length=1, max_length=32000)
    chapters: list[Chapter] = Field(min_length=1, max_length=12)
    source_ids: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def valid_script(self):
        validate_code(self.code)
        if any(c.end <= c.start or c.end > self.duration for c in self.chapters):
            raise ValueError('Chapters must be within the video duration.')
        return self


def validate_code(code):
    from .drawing_script import parse
    return parse(code)


def directory(user_id):
    path = config.DATA_DIR / 'videos' / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


_LOCKS = {}


def _ffmpeg_executable():
    import importlib.util
    if importlib.util.find_spec('imageio_ffmpeg') is None:
        raise ValueError('Video renderer unavailable. Install the project requirements.')
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@workflow.stage('Visualization · Render video', 'tools')
async def render(script: VideoScript, user_id):
    identity=(asyncio.get_running_loop(),str(config.DATA_DIR),user_id,script.model_dump_json())
    lock=_LOCKS.setdefault(identity,asyncio.Lock())
    async with lock:
        return await _render(script,user_id)

async def _render(script: VideoScript, user_id):
    import importlib.util
    if importlib.util.find_spec('PIL') is None:
        raise ValueError('Video renderer unavailable. Install Pillow.')
    ffmpeg_executable = _ffmpeg_executable()
    key = hashlib.sha256(json.dumps(dict(script=script.model_dump(), renderer='pillow-v1',
        width=960, height=540, fps=15), sort_keys=True).encode()).hexdigest()
    folder = directory(user_id) / key
    folder.mkdir(exist_ok=True)
    output = folder / 'video.mp4'
    if output.is_file() and output.stat().st_size:
        await operations.progress('cache_hit', message='Reused the rendered video from cache.')
        return key
    (folder / 'script.json').write_text(script.model_dump_json())
    command = [sys.executable, '-I', str(Path(__file__).with_name('renderer_worker.py')),
        str(folder.resolve()), ffmpeg_executable]
    await operations.progress('rendering', message='Rendering animation frames and encoding video.')
    started = time.monotonic()
    process_options = {'start_new_session': True} if os.name != 'nt' else {}
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        **process_options,
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), 60)
        if process.returncode or not output.is_file() or not output.stat().st_size:
            diagnostic=(stderr or b'').decode(errors='replace').strip().splitlines()
            detail=diagnostic[-1][:250] if diagnostic else 'Frame rendering failed.'
            raise ValueError('Animation rendering failed: '+detail)
        if output.stat().st_size > 20*1024*1024:
            output.unlink()
            raise ValueError('Animation exceeded the video size limit.')
        utils.METRICS['video_render_ms'] += round((time.monotonic()-started)*1000)
        utils.METRICS['video_renders'] += 1
        return key
    except (asyncio.CancelledError, TimeoutError):
        if os.name == 'nt':
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()
        raise
