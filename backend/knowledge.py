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

class GraphEvidence(utils.Schema):
    concept: str
    prerequisite: str = ''
    source_id: str
    quote: str = Field(min_length=1, max_length=500)

class ConceptGraph(utils.Schema):
    concepts: list[str] = Field(max_length=30)
    prerequisites: list[Prerequisite] = Field(max_length=60)
    evidence: list[GraphEvidence] = Field(default_factory=list, max_length=90)

class GeminiGraph(utils.Schema):
    """Shallow provider transport; validate the complete graph locally."""
    graph_json: str


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

def material_title(names: list[str]) -> str:
    if len(names) <= 2:
        return ' + '.join(names)
    return f'{names[0]} + {len(names) - 1} more'

def save_materials(documents: list[tuple], graph: dict | None=None, extra: dict | None=None) -> dict:
    """Store (filename, bytes, text[, url]) documents as one material with a shared concept graph."""
    directory = config.DATA_DIR / 'materials'
    directory.mkdir(parents=True, exist_ok=True)
    material_id = uuid.uuid4().hex
    chunks, names = [], []
    for index, document in enumerate(documents):
        filename, content, text = document[:3]
        url = document[3] if len(document) > 3 else None
        name = Path(filename).name
        names.append(name)
        suffix = Path(filename).suffix.lower()
        suffix = suffix if suffix in ('.pdf', '.txt', '.md') else '.txt'
        stored = f'{material_id}{suffix}' if len(documents) == 1 else f'{material_id}-{index}{suffix}'
        (directory / stored).write_bytes(content)
        chunks.extend(make_chunks(material_id, len(chunks), name, text, url))
    record = dict(id=material_id, name=material_title(names), sources=names, chunks=chunks, graph=graph)
    record.update(extra or {})
    temporary = directory / f'{material_id}.tmp'
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(directory / f'{material_id}.json')
    return record

def make_chunks(prefix: str, start_index: int, source: str, text: str, url: str | None=None) -> list[dict]:
    extra = {'url': url} if url else {}
    return [dict(id=f'{prefix}:{start_index + j}', source=source, text=text[start:start + 1400], **extra)
            for j, start in enumerate(range(0, len(text), 1100))]

def add_web_documents(material_id: str, documents: list[dict], concept: str) -> list[dict]:
    """Append agent web lookups to a material so later retrieval reuses them."""
    path = config.DATA_DIR / 'materials' / f'{material_id}.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    known = {s.get('url') for s in record.get('web_sources', [])}
    added = []
    for doc in documents:
        if doc['url'] in known:
            continue
        chunks = make_chunks(material_id, len(record['chunks']) + len(added), doc['title'], doc['text'][:12000], doc['url'])
        added.extend({**chunk, 'lookup': concept} for chunk in chunks)
        record.setdefault('web_sources', []).append(dict(title=doc['title'], url=doc['url'], concept=concept))
        known.add(doc['url'])
    record['chunks'].extend(added)
    record.setdefault('web_lookups', []).append(concept)
    _write_record(path, record)
    return added

def update_material(material_id: str, **fields) -> dict:
    path = config.DATA_DIR / 'materials' / f'{material_id}.json'
    record = {**json.loads(path.read_text(encoding='utf-8')), **fields}
    _write_record(path, record)
    return record

def _write_record(path: Path, record: dict):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(path)

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

EXTRACTION_BUDGET = 24000

def extraction_input(documents: list[tuple[str, str]]) -> str:
    """Give every document an equal share of the extraction budget, labelled by file name."""
    if len(documents) == 1:
        return documents[0][1][:EXTRACTION_BUDGET]
    share = EXTRACTION_BUDGET // len(documents)
    sections = (f'### Document {i}: {name}\n{text[:share]}' for i, (name, text) in enumerate(documents, 1))
    return '\n\n'.join(sections)

async def extract_concepts(documents: list[tuple[str, str]], focus: str | None=None, context: dict | None=None, primary_count: int | None=None) -> ConceptGraph:
    instructions = prompts.EXTRACT_CONCEPTS_MULTI + (
        ' Build the graph around the requested subject. Fill missing prerequisites without broadening the subject. '
        'For EVERY concept and prerequisite edge supply evidence: concept, prerequisite (empty for concept evidence), '
        'source_id and exact short quote from the supplied document. Student goals guide scope, never factual correctness.')
    from .context import bound
    primary_count = len(documents) if primary_count is None else primary_count
    def budget(index):
        if not primary_count or primary_count==len(documents): return EXTRACTION_BUDGET//max(1,len(documents))
        return 16000//primary_count if index<primary_count else 8000//max(1,len(documents)-primary_count)
    docs = [dict(id=f'D{i+1}', name=name, text=text[:budget(i)]) for i, (name,text) in enumerate(documents)]
    gemini=config.LLM_PROVIDER=='gemini'
    if gemini:
        instructions += (' Transport: return graph_json containing the complete graph as a JSON string, '
            'without Markdown fences. Its decoded object must follow this schema: '+json.dumps(ConceptGraph.model_json_schema()))
    result = await utils.call_llm(instructions, bound(dict(subject=focus or '', documents=docs,
        learning_context=context or {})), schema=GeminiGraph if gemini else ConceptGraph,
        operation='EXTRACT_CONCEPTS',max_output_tokens=max(config.MAX_OUTPUT_TOKENS,6000))
    graph=ConceptGraph.model_validate_json(result.graph_json) if gemini else result
    lookup = {d['id']:d['text'] for d in docs}
    for e in graph.evidence:
        if e.source_id not in lookup or e.quote not in lookup[e.source_id] or e.concept not in graph.concepts:
            raise ValueError('Graph evidence must reference an exact supplied source passage.')
    covered = {(e.prerequisite,e.concept) for e in graph.evidence}
    if any(('', name) not in covered for name in graph.concepts) or any(
        (e.prerequisite,e.concept) not in covered for e in graph.prerequisites):
        raise ValueError('The model omitted evidence for a graph concept or prerequisite. Please retry.')
    return graph

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
    return ConceptGraph(concepts=names, prerequisites=accepted, evidence=[e for e in graph.evidence if not e.prerequisite or any(x.concept == e.concept and x.prerequisite == e.prerequisite for x in accepted)])

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
