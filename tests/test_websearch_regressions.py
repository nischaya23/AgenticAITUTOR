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
