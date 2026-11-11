import asyncio

from backend import config, knowledge, main, websearch

REAL_SEARCH = websearch.search

PAGES = [
    dict(title='Binary search tree', url='https://en.wikipedia.org/wiki/Binary_search_tree',
         text='# Binary search trees\nA binary search tree keeps smaller keys on the left. ' * 20),
    dict(title='BST tutorial', url='https://example.org/bst',
         text='# Tree rotations\nRotations rebalance a binary search tree after insertion. ' * 20),
]


def test_html_to_text_skips_scripts_and_menus():
    title, text = websearch.html_to_text(
        '<html><head><title>Trees</title><script>steal()</script></head><body><nav>Home About</nav>'
        '<p>A binary search tree stores keys so that an in-order walk visits them sorted.</p><p>Menu</p></body></html>')
    assert title == 'Trees'
    assert 'in-order walk' in text and 'steal' not in text and 'Home About' not in text and 'Menu' not in text


def test_duckduckgo_links_decode_redirects_and_skip_unwanted_hosts():
    html = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbst&amp;rut=x">BST</a>'
            '<a class="result__a" href="https://www.youtube.com/watch?v=1">Video</a>'
            '<a class="result__a" href="https://en.wikipedia.org/wiki/Tree">Wiki</a>'
            '<a class="result__a" href="https://example.org/bst">Duplicate</a>')
    assert websearch.duckduckgo_links(html) == ['https://example.org/bst']


def test_public_url_rejects_local_and_non_http_targets():
    for url in ('http://127.0.0.1:8000/api', 'http://localhost/', 'http://10.0.0.5/', 'file:///etc/passwd',
                'http://169.254.169.254/latest/meta-data', 'ftp://example.org/'):
        assert not websearch.public_url(url), url


def test_study_topic_from_web_builds_graph_and_session(app_env, monkeypatch):
    client, _, _ = app_env
    queries = []

    async def fake_search(query, max_pages=None):
        queries.append(query)
        return PAGES
    monkeypatch.setattr(websearch, 'search', fake_search)
    response = client.post('/api/materials/web', json={'topic': '  binary   search trees '})
    assert response.status_code == 200, response.text
    body = response.json()
    assert queries == ['binary search trees']
    assert body['name'] == 'Web: binary search trees'
    assert [s['url'] for s in body['web_sources']] == [p['url'] for p in PAGES]
    material = knowledge.get_material(body['id'])
    assert material['origin'] == 'web'
    assert {c['url'] for c in material['chunks']} == {p['url'] for p in PAGES}
    assert any(m['id'] == body['id'] for m in client.get('/api/materials').json())
    sid = client.post('/api/sessions').json()['id']
    study = client.post(f'/api/sessions/{sid}/study', json={'material_id': body['id']})
    assert study.status_code == 200, study.text
    assert study.json()['material']['web_sources'][0]['title'] == 'Binary search tree'


def test_web_topic_errors_are_reported(app_env, monkeypatch):
    client, _, _ = app_env

    async def no_results(query, max_pages=None):
        raise websearch.WebSearchError('Web search found no readable pages for that topic.')
    monkeypatch.setattr(websearch, 'search', no_results)
    response = client.post('/api/materials/web', json={'topic': 'zzzz'})
    assert response.status_code == 400 and 'no readable pages' in response.json()['detail']
    assert client.post('/api/materials/web', json={'topic': 'x'}).status_code == 422
    monkeypatch.setattr(config, 'WEB_SEARCH_PROVIDER', 'off')
    assert not websearch.enabled()
    try:
        asyncio.run(REAL_SEARCH('binary trees'))
        raise AssertionError('search ran while turned off')
    except websearch.WebSearchError as exc:
        assert 'turned off' in str(exc)

def test_agent_searches_web_once_when_material_lacks_concept(app_env, monkeypatch):
    calls = []

    async def fake_search(query, max_pages=None):
        calls.append((query, max_pages))
        return [PAGES[1]]
    monkeypatch.setattr(websearch, 'search', fake_search)
    record = knowledge.save_materials([('queues.md', b'# Queues\nFIFO order.', '# Queues\nFIFO order.')],
                                      {'concepts': ['Queues', 'Tree rotations'], 'prerequisites': []})
    sources = asyncio.run(main.study_sources(knowledge.get_material(record['id']), 'Tree rotations'))
    assert calls == [('Tree rotations', 4)]
    assert sources and all(s['url'] == 'https://example.org/bst' for s in sources)
    updated = knowledge.get_material(record['id'])
    assert updated['web_lookups'] == ['Tree rotations']
    assert updated['web_sources'][0]['concept'] == 'Tree rotations'
    # Matching material, and concepts already looked up, never trigger another search.
    asyncio.run(main.study_sources(updated, 'Queues'))
    asyncio.run(main.study_sources(updated, 'Tree rotations'))
    assert len(calls) == 1


def test_agent_lookup_failure_falls_back_to_material(app_env, monkeypatch):
    async def failing(query, max_pages=None):
        raise websearch.WebSearchError('offline')
    monkeypatch.setattr(websearch, 'search', failing)
    record = knowledge.save_materials([('queues.md', b'# Queues\nFIFO order.', '# Queues\nFIFO order.')],
                                      {'concepts': ['Queues'], 'prerequisites': []})
    sources = asyncio.run(main.study_sources(knowledge.get_material(record['id']), 'Graph coloring'))
    assert sources[0]['source'] == 'queues.md'


def test_web_enhancement_mixes_material_and_web_passages(app_env, monkeypatch):
    client, _, _ = app_env
    calls = []

    async def fake_search(query, max_pages=None):
        calls.append(query)
        return [PAGES[0]]
    monkeypatch.setattr(websearch, 'search', fake_search)
    body = client.post('/api/materials', data={'web_enhance': 'true'},
                       files={'file': ('bst.md', b'# Binary search trees\nOur lecture: a binary search tree orders keys.', 'text/markdown')}).json()
    assert body['web_enhance'] is True
    material = knowledge.get_material(body['id'])
    sources = asyncio.run(main.study_sources(material, 'Binary search tree'))
    assert len(calls) == 1
    assert any(not s.get('url') for s in sources) and any(s.get('url') for s in sources)
    asyncio.run(main.study_sources(knowledge.get_material(body['id']), 'Binary search tree'))
    assert len(calls) == 1  # Each concept is looked up once.


def test_web_enhance_toggle_is_owner_only(app_env):
    client, _, _ = app_env
    body = client.post('/api/materials', files={'file': ('a.md', b'# A\ntext', 'text/markdown')}).json()
    assert body['web_enhance'] is False
    response = client.post(f"/api/materials/{body['id']}/web-enhance", json={'enabled': True})
    assert response.status_code == 200 and response.json()['web_enhance'] is True
    assert knowledge.get_material(body['id'])['web_enhance'] is True
    assert client.post('/api/materials/missing/web-enhance', json={'enabled': True}).status_code == 404


def test_web_query_adds_context_for_short_concept_names():
    material = {'graph': {'prerequisites': [{'prerequisite': 'Sets', 'concept': 'Groups'},
                                            {'prerequisite': 'Groups', 'concept': 'Cosets'}]}}
    assert main.web_query(material, 'Groups') == 'Groups Sets Cosets'
    assert main.web_query({'topic': 'abstract algebra'}, 'Groups') == 'Groups abstract algebra'


def test_ambiguous_topic_asks_which_meaning_then_drops_off_topic_pages(app_env, monkeypatch):
    client, _, _ = app_env
    meanings = [websearch.Meaning(label='Retrieval-augmented generation', query='retrieval-augmented generation LLM',
                                  description='Grounding language model answers in retrieved documents.'),
                websearch.Meaning(label='Rag (textile)', query='rag cloth textile', description='A scrap of cloth.')]

    async def interpret(topic):
        return meanings
    queries, focuses = [], []

    async def fake_search(query, max_pages=None):
        queries.append(query)
        return [dict(title='Durag', url='https://en.wikipedia.org/wiki/Durag', text='A durag is a cloth head covering. ' * 30),
                dict(title='Retrieval-augmented generation', url='https://en.wikipedia.org/wiki/Retrieval-augmented_generation',
                     text='# Retrieval\nRetrieval-augmented generation retrieves documents before generating. ' * 20)]
    real_extract = knowledge.extract_concepts

    async def spy_extract(documents, focus=None, context=None, **kwargs):
        focuses.append((focus, [name for name, _ in documents]))
        return await real_extract(documents, focus, context, **kwargs)
    monkeypatch.setattr(websearch, 'interpret_topic', interpret)
    monkeypatch.setattr(websearch, 'search', fake_search)
    monkeypatch.setattr(knowledge, 'extract_concepts', spy_extract)

    first = client.post('/api/materials/web', json={'topic': 'rag'}).json()
    assert first['type'] == 'choices' and [m['label'] for m in first['meanings']] == ['Retrieval-augmented generation', 'Rag (textile)']
    assert queries == []  # Nothing is searched until the student picks a meaning.

    chosen = client.post('/api/materials/web', json={'topic': 'rag', 'meaning': first['meanings'][0]})
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert queries == ['retrieval-augmented generation LLM']
    assert body['name'] == 'Web: Retrieval-augmented generation'
    assert [s['title'] for s in body['web_sources']] == ['Retrieval-augmented generation']  # Durag was dropped.
    assert focuses == [('Retrieval-augmented generation: Grounding language model answers in retrieved documents.',
                        ['Retrieval-augmented generation'])]
    material = knowledge.get_material(body['id'])
    assert material['topic'] == 'Retrieval-augmented generation' and material['topic_description']


def test_topic_with_only_off_topic_pages_reports_an_error(app_env, monkeypatch):
    client, _, _ = app_env

    async def fake_search(query, max_pages=None):
        return [dict(title='Durag', url='https://example.org/durag', text='Head covering. ' * 50)]
    monkeypatch.setattr(websearch, 'search', fake_search)
    response = client.post('/api/materials/web', json={'topic': 'rag'})
    assert response.status_code == 400 and 'No web pages found about rag' in response.json()['detail']


def test_relevant_pages_ignores_out_of_range_indices(monkeypatch):
    async def fake_llm(instructions, data, schema=None, **kwargs):
        return schema(relevant=[1, 7, -1])
    monkeypatch.setattr(websearch.utils, 'call_llm', fake_llm)
    pages = [dict(title=t, url=f'https://example.org/{t}', text='text') for t in ('a', 'b')]
    assert asyncio.run(websearch.relevant_pages(pages, 'subject')) == [pages[1]]
    assert asyncio.run(websearch.relevant_pages([], 'subject')) == []


def test_agent_lookup_drops_off_topic_pages(app_env, monkeypatch):
    async def fake_search(query, max_pages=None):
        return [dict(title='Durag', url='https://example.org/durag', text='Head covering. ' * 50), PAGES[1]]
    monkeypatch.setattr(websearch, 'search', fake_search)
    record = knowledge.save_materials([('notes.md', b'Queues.', 'Queues.')], {'concepts': ['Queues', 'Tree rotations'], 'prerequisites': []})
    asyncio.run(main.study_sources(knowledge.get_material(record['id']), 'Tree rotations'))
    assert [s['url'] for s in knowledge.get_material(record['id'])['web_sources']] == ['https://example.org/bst']
