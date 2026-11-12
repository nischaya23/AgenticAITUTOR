"""Regressions for selected TF-IDF sources followed by provider rejection."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from backend import config, knowledge, operations, research, utils, websearch
from conftest import REAL_CALL_LLM
from test_agentic_features import wait_job


async def test_gemini_research_roundtrip_preserves_thought_signature(monkeypatch):
    monkeypatch.setattr(config,'LLM_PROVIDER','gemini')
    monkeypatch.setattr(config,'GEMINI_MODEL','test-model')
    calls=[]
    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            call=SimpleNamespace(id='search-1',type='function',extra_content={'google':{'thought_signature':'opaque-test-signature'}},
                function=SimpleNamespace(name='web_search',arguments=json.dumps(dict(query='TF-IDF',subject='Information retrieval',purpose='Find a learning source'))))
        else:
            assistant=kwargs['messages'][2]
            assert assistant['tool_calls'][0]['extra_content']['google']['thought_signature']=='opaque-test-signature'
            assert kwargs['messages'][3]['tool_call_id']=='search-1'
            call=SimpleNamespace(id='finish-1',type='function',function=SimpleNamespace(name='finish_research',arguments='{"reason":"Sufficient evidence"}'))
        return SimpleNamespace(usage=None,choices=[SimpleNamespace(finish_reason='tool_calls',message=SimpleNamespace(tool_calls=[call],content=None))])
    monkeypatch.setattr(utils,'get_client',lambda:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(utils,'call_llm',REAL_CALL_LLM)
    page=dict(title='TF-IDF',url='https://example.org/tfidf',text='TF-IDF measures the importance of a term in a document.')
    monkeypatch.setattr(websearch,'search',AsyncMock(return_value=[page]))
    monkeypatch.setattr(websearch,'relevant_pages',AsyncMock(return_value=[page]))
    assert await research.investigate({'subject':'Information retrieval'},required=True)==[page]
    assert len(calls)==2


def test_selected_sources_survive_followup_rejection_and_upload_builds_graph(app_env,monkeypatch):
    client,_,_=app_env
    monkeypatch.setattr(config, 'WEB_SEARCH_ON_UPLOAD', True)
    original=utils.call_llm
    count=0
    async def model(instructions,data,**kwargs):
        nonlocal count
        if kwargs.get('operation')=='RESEARCH_TOOL':
            count+=1
            if count==2:
                raise httpx.HTTPStatusError('Follow-up request rejected.',request=httpx.Request('POST','https://example.org/model'),response=httpx.Response(400))
            return SimpleNamespace(output=[SimpleNamespace(type='function_call',call_id='search-1',name='web_search',arguments=json.dumps(dict(query='TF-IDF information retrieval',subject='Information retrieval',purpose='Explain term weighting')))])
        return await original(instructions,data,**kwargs)
    monkeypatch.setattr(utils,'call_llm',model)
    page=dict(title='Understanding TF-IDF',url='https://example.org/tfidf',text='# TF-IDF\nTF-IDF measures term importance in a document relative to a collection.')
    monkeypatch.setattr(websearch,'search',AsyncMock(return_value=[page]))
    oid=client.post('/api/material-jobs',files={'files':('notes.md',b'# Information Retrieval\nRank documents by relevance.','text/markdown')}).json()['id']
    result=wait_job(client,oid)
    assert result['status']=='ready',result
    assert any(e['kind']=='selected' and e['url']==page['url'] for e in result['events'])
    assert any(e['kind']=='research_partial' and 'retaining 1' in e['message'] for e in result['events'])
    assert not any(e['kind']=='research_unavailable' for e in result['events'])
    material=knowledge.get_material(result['result']['id'])
    assert material['web_sources']==[dict(title=page['title'],url=page['url'])]
    assert any(chunk.get('url')==page['url'] for chunk in material['chunks'])
    assert material['graph']['evidence']


async def test_free_web_provider_fetches_wikipedia_and_actual_tfidf_page(monkeypatch):
    real_search=websearch.search
    monkeypatch.setattr(config,'WEB_SEARCH_PROVIDER','free')
    requests=[]
    text='TF-IDF scores term importance relative to a document collection. '*12
    async def transport(request):
        requests.append(str(request.url))
        if request.url.host=='en.wikipedia.org':
            if request.url.params.get('list')=='search':
                return httpx.Response(200,json={'query':{'search':[{'title':'Information retrieval'}]}})
            return httpx.Response(200,json={'query':{'pages':{'1':{'title':'Information retrieval','fullurl':'https://en.wikipedia.org/wiki/Information_retrieval','extract':text}}}})
        if request.url.host=='html.duckduckgo.com':
            return httpx.Response(200,text='<a class="result__a" href="https://example.org/tfidf">Understanding TF-IDF</a>')
        if request.url.host=='example.org':
            return httpx.Response(200,headers={'Content-Type':'text/html'},text='<html><title>Understanding TF-IDF</title><main><p>'+text+'</p></main></html>')
        raise AssertionError('Unexpected request')
    original=httpx.AsyncClient
    monkeypatch.setattr(websearch.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    monkeypatch.setattr(websearch,'public_url',lambda url:url.startswith('https://example.org/'))
    pages=await real_search('TF-IDF information retrieval',max_pages=4)
    assert {p['url'] for p in pages}=={'https://en.wikipedia.org/wiki/Information_retrieval','https://example.org/tfidf'}
    assert all('TF-IDF scores term importance' in p['text'] for p in pages)
    assert 'https://example.org/tfidf' in requests


@pytest.mark.parametrize('invalid',[None,'json','source','quote'])
async def test_gemini_graph_uses_flat_transport_and_validates_evidence_locally(monkeypatch,invalid):
    from backend import knowledge
    monkeypatch.setattr(config,'LLM_PROVIDER','gemini')
    monkeypatch.setattr(config,'GEMINI_MODEL','test-model')
    text='TF-IDF measures term importance in a document.'
    graph=dict(concepts=['TF-IDF'],prerequisites=[],evidence=[dict(concept='TF-IDF',prerequisite='',source_id='D1',quote=text)])
    if invalid=='source': graph['evidence'][0]['source_id']='invented'
    if invalid=='quote': graph['evidence'][0]['quote']='Not in the document'
    async def parse(**kwargs):
        schema=kwargs['response_format']
        assert set(schema.model_json_schema()['properties'])=={'graph_json'}
        assert '$defs' not in schema.model_json_schema()
        wire=schema(graph_json='not JSON' if invalid=='json' else json.dumps(graph))
        return SimpleNamespace(usage=None,choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(parsed=wire))])
    monkeypatch.setattr(utils,'call_llm',REAL_CALL_LLM)
    monkeypatch.setattr(utils,'get_client',lambda:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))))
    if invalid:
        with pytest.raises(ValueError): await knowledge.extract_concepts([('TF-IDF notes',text)],focus='TF-IDF')
    else:
        result=await knowledge.extract_concepts([('TF-IDF notes',text)],focus='TF-IDF')
        assert result.concepts==['TF-IDF'] and result.evidence[0].source_id=='D1'


async def test_bad_selection_quote_rejects_only_that_page(monkeypatch):
    pages=[dict(title='Good source',url='https://example.org/good',text='TF-IDF uses document frequency.'),
           dict(title='Bad quote',url='https://example.org/bad',text='This page has different wording.')]
    model=websearch.Relevance(relevant=[0,1],decisions=[
        dict(index=0,reason='Explains TF-IDF.',quote='TF-IDF uses document frequency.'),
        dict(index=1,reason='Looks related.',quote='An invented quote.')])
    monkeypatch.setattr(utils,'call_llm',AsyncMock(return_value=model))
    assert await websearch.relevant_pages(pages,'TF-IDF')==[pages[0]]


async def test_graph_repairs_paraphrased_quotes_against_original_sources(monkeypatch):
    from backend import knowledge
    monkeypatch.setattr(config,'LLM_PROVIDER','openai')
    text='TF-IDF measures term importance in a document.'
    bad=knowledge.ConceptGraph(concepts=['TF-IDF'],prerequisites=[],evidence=[dict(concept='TF-IDF',prerequisite='',source_id='D1',quote='Words get weighted by importance.')])
    good=knowledge.ConceptGraph(concepts=['TF-IDF'],prerequisites=[],evidence=[dict(concept='TF-IDF',prerequisite='',source_id='D1',quote=text)])
    model=AsyncMock(side_effect=[bad,good]);monkeypatch.setattr(utils,'call_llm',model)
    graph=await knowledge.extract_concepts([('TF-IDF',text)])
    assert graph.evidence[0].quote==text
    assert model.await_count==2
    assert model.call_args.kwargs['operation']=='REPAIR_GRAPH_EVIDENCE'
    assert model.call_args.args[1]['documents'][0]['text']==text


def test_graph_quote_normalization_returns_real_source_substring():
    from backend.knowledge import resolve_quote
    source='TF–IDF measures\n\nterm importance.'
    assert resolve_quote(source,'TF-IDF measures term importance.')==source
    assert resolve_quote(source,'TF-IDF always gives perfect rankings.') is None


async def test_gemini_graph_preserves_literal_source_newlines_in_transport(monkeypatch):
    from backend import knowledge
    monkeypatch.setattr(config,'LLM_PROVIDER','gemini')
    text='TF-IDF measures\nterm importance.'
    raw=json.dumps(dict(concepts=['TF-IDF'],prerequisites=[],evidence=[dict(concept='TF-IDF',prerequisite='',source_id='D1',quote=text)]))
    raw=raw.replace('\\n','\n')
    monkeypatch.setattr(utils,'call_llm',AsyncMock(return_value=knowledge.GeminiGraph(graph_json=raw)))
    graph=await knowledge.extract_concepts([('TF-IDF',text)])
    assert graph.evidence[0].quote==text


async def test_redundant_bad_citations_do_not_discard_fully_grounded_tfidf_graph(monkeypatch):
    from backend import knowledge
    monkeypatch.setattr(config,'LLM_PROVIDER','openai')
    text='Term frequency counts occurrences. TF-IDF combines term frequency and inverse document frequency.'
    graph=knowledge.ConceptGraph(concepts=['Term frequency','TF-IDF'],prerequisites=[dict(prerequisite='Term frequency',concept='TF-IDF')],evidence=[
        dict(concept='Term frequency',prerequisite='',source_id='D1',quote='Term frequency counts occurrences.'),
        dict(concept='TF-IDF',prerequisite='Term frequency',source_id='D1',quote='TF-IDF combines... term counts...'),
        dict(concept='TF-IDF',prerequisite='Term frequency',source_id='D1',quote='TF-IDF combines term frequency and inverse document frequency.')])
    model=AsyncMock(return_value=graph);monkeypatch.setattr(utils,'call_llm',model)
    result=await knowledge.extract_concepts([('TF-IDF',text)])
    assert model.await_count==1
    assert len(result.evidence)==2
    assert all(e.quote in text for e in result.evidence)


async def test_selected_sources_survive_malformed_followup_tool_arguments(monkeypatch):
    count=0
    async def model(*args,**kwargs):
        nonlocal count
        count+=1
        arguments=json.dumps(dict(query='TF-IDF',subject='Information retrieval',purpose='Explain term weights')) if count==1 else 'not JSON'
        return SimpleNamespace(output=[SimpleNamespace(type='function_call',call_id=f'call-{count}',name='web_search',arguments=arguments)])
    page=dict(title='TF-IDF',url='https://example.org/tfidf',text='TF-IDF combines term and inverse document frequency.')
    monkeypatch.setattr(utils,'call_llm',model)
    monkeypatch.setattr(websearch,'search',AsyncMock(return_value=[page]))
    monkeypatch.setattr(websearch,'relevant_pages',AsyncMock(return_value=[page]))
    assert await research.investigate({'subject':'TF-IDF'},required=True)==[page]


def test_shortened_quote_is_resolved_to_complete_original_passage():
    from backend.knowledge import resolve_quote
    source=('TF-IDF combines two components: Term Frequency counts occurrences. '
        'A higher frequency suggests greater importance. '
        'Inverse Document Frequency reduces common-word weights.')
    shortened=('TF-IDF combines two components: Term Frequency counts occurrences. '
        'Inverse Document Frequency reduces common-word weights.')
    assert resolve_quote(source,shortened)==source
    assert 'A higher frequency' in resolve_quote(source,shortened)
    assert resolve_quote(source,shortened.replace('reduces','guarantees')) is None
