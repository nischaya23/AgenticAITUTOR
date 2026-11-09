"""Small disk-backed BM25 retrieval index and SQL prerequisite graph."""
import json
import math
import re
import uuid
from collections import Counter
from pathlib import Path
from sqlalchemy import select
from pydantic import Field
from . import db as database
from . import prompts, config, utils
from .db import Concept, ConceptEdge, Material

class Prerequisite(utils.Schema):
    prerequisite: str
    concept: str

class ConceptGraph(utils.Schema):
    concepts: list[str] = Field(max_length=30)
    prerequisites: list[Prerequisite] = Field(max_length=60)

def tokens(text: str) -> list[str]:
    return re.findall('[a-z0-9_]+', text.lower())

def extract_text(filename: str, content: bytes) -> str:
    if Path(filename).suffix.lower() == '.pdf':
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content))
        if len(reader.pages) > 150:
            raise ValueError('PDFs must contain at most 150 pages.')
        text = '\n\n'.join((p.extract_text() or '' for p in reader.pages))
    elif Path(filename).suffix.lower() in ('.txt', '.md'):
        text = content.decode('utf-8')
    else:
        raise ValueError('Upload a UTF-8 TXT, Markdown, or text-based PDF file.')
    if not text.strip():
        raise ValueError('No extractable text; scanned PDFs need OCR first.')
    if len(text) > 600000:
        raise ValueError('Material is too long; split it into smaller documents.')
    return text

def save_material(filename: str, content: bytes, text: str, graph: dict | None=None) -> dict:
    directory = config.DATA_DIR / 'materials'
    directory.mkdir(parents=True, exist_ok=True)
    material_id = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()
    (directory / f'{material_id}{suffix}').write_bytes(content)
    chunks = [dict(id=f'{material_id}:{i}', source=Path(filename).name, text=text[start:start + 1400]) for i, start in enumerate(range(0, len(text), 1100))]
    record = dict(id=material_id, name=Path(filename).name, chunks=chunks, graph=graph)
    temporary = directory / f'{material_id}.tmp'
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(directory / f'{material_id}.json')
    return record

def list_materials(user_id: int | None = None) -> list[dict]:
    directory = config.DATA_DIR / 'materials'
    if user_id is None:
        files = sorted(directory.glob('*.json'))
    else:
        with database.SessionLocal() as db:
            ids = db.scalars(select(Material.id).where(Material.user_id == user_id)).all()
        files = [directory / f'{material_id}.json' for material_id in ids]
    return [json.loads(p.read_text(encoding='utf-8')) for p in files if p.is_file()]

def get_material(material_id: str) -> dict:
    for material in list_materials():
        if material['id'] == material_id:
            return material
    raise ValueError('Material not found. Upload it again.')

def retrieve_knowledge(query: str, limit: int=4, material_id: str | None=None, user_id: int | None=None) -> list[dict]:
    records = [get_material(material_id)] if material_id else list_materials(user_id)
    chunks = [c for m in records for c in m['chunks']]
    if not chunks:
        return []
    counts = [Counter(tokens(c['text'])) for c in chunks]
    avg = sum((sum(c.values()) for c in counts)) / len(counts) or 1
    terms = set(tokens(query))
    df = {term: sum((term in c for c in counts)) for term in terms}
    scored = []
    for chunk, count in zip(chunks, counts):
        score = 0.0
        for term in terms:
            tf = count[term]
            score += math.log(1 + (len(chunks) - df[term] + 0.5) / (df[term] + 0.5)) * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * sum(count.values()) / avg))
        if score > 0:
            scored.append({**chunk, 'score': round(score, 4)})
    return sorted(scored, key=lambda c: c['score'], reverse=True)[:limit]

async def extract_concepts(text: str) -> ConceptGraph:
    return await utils.call_llm(prompts.EXTRACT_CONCEPTS, text[:24000], schema=ConceptGraph, operation='EXTRACT_CONCEPTS')

def store_graph(db, graph: ConceptGraph, isolated: bool = False):
    names = list(dict.fromkeys((n.strip()[:200] for n in graph.concepts if n.strip())))
    rows = {}
    for name in names:
        row = db.scalar(select(Concept).where(Concept.name == name))
        if not row:
            row = Concept(name=name, data={'origin': 'material extraction; review suggested'})
            db.add(row)
            db.flush()
        rows[name] = row.id
    stored_edges = {(e.from_id, e.to_id) for e in db.scalars(select(ConceptEdge))}
    edges = set() if isolated else set(stored_edges)
    accepted = []
    for edge in graph.prerequisites:
        a, b = (rows.get(edge.prerequisite), rows.get(edge.concept))
        if a is None or b is None or a == b:
            continue
        if (a, b) in edges:
            accepted.append(edge)
            continue
        frontier, seen = ([b], set())
        while frontier:
            node = frontier.pop()
            if node not in seen:
                seen.add(node)
                frontier.extend((y for x, y in edges if x == node))
        if a not in seen:
            if (a, b) not in stored_edges:
                db.add(ConceptEdge(from_id=a, to_id=b))
                stored_edges.add((a, b))
            edges.add((a, b))
            accepted.append(edge)
    return ConceptGraph(concepts=names, prerequisites=accepted)

def get_prerequisites(db, concept: str, material_id: str | None=None, user_id: int | None=None) -> list[str]:
    if material_id:
        graph = get_material(material_id).get('graph') or {}
        return [edge['prerequisite'] for edge in graph.get('prerequisites', []) if edge['concept'] == concept]
    if user_id is not None:
        return list(dict.fromkeys(edge['prerequisite'] for material in list_materials(user_id)
            for edge in (material.get('graph') or {}).get('prerequisites', []) if edge['concept'] == concept))
    row = db.scalar(select(Concept).where(Concept.name == concept))
    if row is None:
        return []
    ids = db.scalars(select(ConceptEdge.from_id).where(ConceptEdge.to_id == row.id)).all()
    return list(db.scalars(select(Concept.name).where(Concept.id.in_(ids)))) if ids else []
