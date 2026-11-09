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
    assert calls == [('Tree rotations', 2)]
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
