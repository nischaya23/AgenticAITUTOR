"""Web search for topic-only study and for agent lookups when material lacks a concept.

Providers (WEB_SEARCH_PROVIDER):
- "free"   (default) Wikipedia API plus DuckDuckGo results, no API key needed.
- "gemini" Gemini with Google Search grounding; falls back to "free" if it fails.
- "off"    disables web search.
Fetched pages are untrusted content: they are only ever passed to the model as source text.
"""
import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from pydantic import Field

from . import config, prompts, utils, operations, logging_setup as logs

# Wikimedia requires a descriptive User-Agent with a contact URL or address.
USER_AGENT = f'StudyroomTutor/1.0 ({config.WEB_SEARCH_CONTACT}) python-httpx'
BROWSER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) StudyroomTutor/1.0'
MAX_PAGE_BYTES = 1_500_000
MAX_DOC_CHARS = 40_000
# Wikipedia comes from its API instead; the rest are video or social sites.
SKIPPED_HOSTS = ('wikipedia.org', 'duckduckgo.com', 'youtube.com', 'youtu.be', 'facebook.com', 'instagram.com',
                 'tiktok.com', 'x.com', 'twitter.com', 'pinterest.com', 'reddit.com')


class WebSearchError(RuntimeError):
    pass


def enabled() -> bool:
    return config.WEB_SEARCH_PROVIDER != 'off'


def clean_title(title: str) -> str:
    title = re.sub(r'[\\/\x00-\x1f]+', '-', title or '').strip(' .-')
    return (title or 'Web page')[:120]


class _TextExtractor(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'aside', 'form', 'svg', 'iframe', 'template'}
    BLOCK = {'p', 'div', 'section', 'article', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'blockquote'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.depth, self.title, self._in_title = [], 0, '', False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag == 'title':
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag == 'title':
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (re.sub(r'[ \t\r\f\v]+', ' ', line).strip() for line in ''.join(parser.parts).split('\n'))
    # Keep lines that read like prose; menus and buttons are usually very short.
    text = '\n'.join(line for line in lines if len(line) >= 40)
    return parser.title.strip(), text


def public_url(url: str) -> bool:
    """Reject non-HTTP schemes and hosts that resolve to private, loopback or reserved addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            return False
    return True


def duckduckgo_links(html: str) -> list[str]:
    links = []
    for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', html):
        href = href.replace('&amp;', '&')
        if href.startswith('//'):
            href = 'https:' + href
        parsed = urlparse(href)
        if parsed.hostname and parsed.hostname.endswith('duckduckgo.com') and parsed.path.startswith('/l/'):
            target = parse_qs(parsed.query).get('uddg', [''])[0]
            href = unquote(target) if target else ''
        host = urlparse(href).hostname or ''
        if href.startswith('http') and not any(host == h or host.endswith('.' + h) for h in SKIPPED_HOSTS) \
                and href not in links:
            links.append(href)
    return links


async def _fetch_page_content(client: httpx.AsyncClient, url: str) -> dict | None:
    for _ in range(4):
        if not await asyncio.to_thread(public_url, url):
            return None
        async with client.stream('GET', url, headers={'User-Agent': BROWSER_AGENT}) as response:
            if response.is_redirect and response.headers.get('location'):
                url = urljoin(url, response.headers['location'])
                continue
            kind = response.headers.get('content-type', '')
            if response.status_code != 200 or 'html' not in kind and 'text/plain' not in kind:
                return None
            body = b''
            async for piece in response.aiter_bytes():
                body += piece
                if len(body) > MAX_PAGE_BYTES:
                    break
            text = body.decode(response.encoding or 'utf-8', errors='replace')
        title, content = html_to_text(text) if 'html' in kind else ('', text)
        if len(content) < 400:
            return None
        return dict(title=clean_title(title or urlparse(url).hostname or 'Web page'), url=url, text=content[:MAX_DOC_CHARS])
    return None


async def _fetch_page(client, url):
    await operations.progress('candidate', url=url, message='Checking public page and readable content.')
    try:
        page = await _fetch_page_content(client, url)
    except (httpx.HTTPError, UnicodeError, ValueError):
        page = None
    await operations.progress('fetched' if page else 'rejected', url=url,
        message='Readable content retrieved.' if page else 'Unavailable, unsupported, unsafe address, or insufficient readable content.')
    return page


async def _wikipedia(client: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    api = 'https://en.wikipedia.org/w/api.php'
    found = await client.get(api, params=dict(action='query', list='search', srsearch=query, format='json', srlimit=limit),
                             headers={'User-Agent': USER_AGENT})
    found.raise_for_status()
    titles = [item['title'] for item in found.json().get('query', {}).get('search', [])][:limit]
    if not titles:
        return []
    pages = await client.get(api, params=dict(action='query', prop='extracts|info', inprop='url', explaintext=1, redirects=1,
                                              titles='|'.join(titles), format='json', exlimit=limit),
                             headers={'User-Agent': USER_AGENT})
    pages.raise_for_status()
    order = {title: i for i, title in enumerate(titles)}
    docs = [dict(title=clean_title(page['title']), url=page.get('fullurl') or f"https://en.wikipedia.org/wiki/{page['title'].replace(' ', '_')}",
                 text=page.get('extract', '')[:MAX_DOC_CHARS])
            for page in pages.json().get('query', {}).get('pages', {}).values() if len(page.get('extract', '')) >= 400]
    for doc in docs:
        await operations.progress('candidate', title=doc['title'], url=doc['url'], message='Retrieved Wikipedia article text.')
    return sorted(docs, key=lambda d: order.get(d['title'], 99))


async def _duckduckgo(client: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    response = await client.post('https://html.duckduckgo.com/html/', data={'q': query}, headers={'User-Agent': BROWSER_AGENT})
    response.raise_for_status()
    docs = []
    for url in duckduckgo_links(response.text)[:limit]:
        try:
            page = await _fetch_page(client, url)
        except (httpx.HTTPError, UnicodeError, ValueError):
            page = None
        if page:
            docs.append(page)
        if len(docs) >= limit:
            break
    return docs


async def _free_search(query: str, max_pages: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=config.WEB_SEARCH_TIMEOUT, follow_redirects=False) as client:
        wiki_count = max(1, (max_pages + 1) // 2)
        results = await asyncio.gather(_wikipedia(client, query, wiki_count),
                                       _duckduckgo(client, query, max_pages - wiki_count), return_exceptions=True)
    docs = []
    for name, result in zip(('wikipedia', 'duckduckgo'), results):
        if isinstance(result, BaseException):
            logs.event('websearch.source_failed', source=name, error=type(result).__name__)
        else:
            docs.extend(result)
    return docs


async def _gemini_search(query: str) -> list[dict]:
    if not config.GEMINI_API_KEY or not config.GEMINI_MODEL:
        raise WebSearchError('Gemini search needs GEMINI_API_KEY and GEMINI_MODEL.')
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent'
    prompt = ('Search the web and write thorough, accurate study notes for a student learning this topic. '
              'Cover definitions, key ideas, worked examples and common mistakes. Topic: ' + query)
    async with httpx.AsyncClient(timeout=config.WEB_SEARCH_TIMEOUT * 3) as client:
        response = await client.post(url, headers={'x-goog-api-key': config.GEMINI_API_KEY},
                                     json={'contents': [{'role': 'user', 'parts': [{'text': prompt}]}], 'tools': [{'google_search': {}}]})
    if response.status_code != 200:
        raise WebSearchError(f'Gemini search failed (HTTP {response.status_code}).')
    candidate = (response.json().get('candidates') or [{}])[0]
    text = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', []))
    if len(text) < 200:
        raise WebSearchError('Gemini search returned no notes.')
    chunks = candidate.get('groundingMetadata', {}).get('groundingChunks', [])
    links = [dict(title=clean_title(c['web'].get('title', 'Web source')), url=c['web'].get('uri', '')) for c in chunks if c.get('web')]
    async with httpx.AsyncClient(timeout=config.WEB_SEARCH_TIMEOUT, follow_redirects=False) as client:
        pages = await asyncio.gather(*[_fetch_page(client, link['url']) for link in links[:4]])
    fetched = [p for p in pages if p]
    if not fetched:
        raise WebSearchError('Grounding links could not be retrieved as page evidence.')
    return fetched


class Meaning(utils.Schema):
    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=300)


class TopicMeanings(utils.Schema):
    meanings: list[Meaning] = Field(min_length=1, max_length=4)


class SelectionReason(utils.Schema):
    index: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    quote: str = Field(max_length=700)

class Relevance(utils.Schema):
    relevant: list[int] = Field(max_length=10)
    decisions: list[SelectionReason] = Field(default_factory=list, max_length=10)


async def interpret_topic(topic: str, learning_context=None) -> list[Meaning]:
    """The study meanings of a topic, most likely first; several only when it is ambiguous."""
    result = await utils.call_llm(prompts.TOPIC_MEANINGS, {'topic': topic, 'learning_context': learning_context or {}}, schema=TopicMeanings,
                                  operation='TOPIC_MEANINGS', max_output_tokens=600)
    return result.meanings


async def relevant_pages(pages: list[dict], subject: str) -> list[dict]:
    """Drop pages that are not about the subject, e.g. "Durag" when studying retrieval-augmented generation."""
    if not pages:
        return []
    previews = [dict(index=i, title=page['title'], url=page['url'], start=page['text'][:700]) for i, page in enumerate(pages)]
    result = await utils.call_llm(prompts.PAGE_RELEVANCE, {'subject': subject, 'pages': previews}, schema=Relevance,
                                  operation='PAGE_RELEVANCE', max_output_tokens=min(3000,max(800,300*len(pages))))
    keep = {i for i in result.relevant if 0 <= i < len(pages)}
    logs.event('websearch.filtered', subject=subject[:200], kept=len(keep), dropped=[p['title'] for i, p in enumerate(pages) if i not in keep])
    reasons = {d.index: d for d in result.decisions}
    for i, page in enumerate(pages):
        decision = reasons.get(i)
        if decision and decision.quote and decision.quote not in page['text']:
            keep.discard(i)
            await operations.progress('rejected',title=page['title'],url=page['url'],reason='Selection evidence did not match the retrieved page.')
            continue
        await operations.progress('selected' if i in keep else 'rejected', title=page['title'], url=page['url'],
            reason=decision.reason if decision else ('Relevant to requested subject.' if i in keep else 'Outside the requested subject.'),
            quote=decision.quote if decision else '')
    return [page for i, page in enumerate(pages) if i in keep]


async def search(query: str, max_pages: int | None = None) -> list[dict]:
    """Return [{title, url, text}] for the query. Raises WebSearchError when nothing usable is found."""
    query = ' '.join(query.split())[:200]
    if not enabled():
        raise WebSearchError('Web search is turned off (WEB_SEARCH_PROVIDER=off).')
    if not query:
        raise WebSearchError('Enter a topic to search for.')
    max_pages = max(1, min(max_pages or config.WEB_SEARCH_MAX_PAGES, 8))
    docs = []
    if config.WEB_SEARCH_PROVIDER == 'gemini':
        try:
            docs = await _gemini_search(query)
        except (WebSearchError, httpx.HTTPError, ValueError) as exc:
            logs.event('websearch.gemini_failed', error=type(exc).__name__, detail=str(exc)[:200])
    if not docs:
        docs = await _free_search(query, max_pages)
    seen, unique = set(), []
    for doc in docs:
        if doc['text'].strip() and doc['url'] not in seen:
            seen.add(doc['url'])
            unique.append(doc)
    logs.event('websearch.complete', query=query, provider=config.WEB_SEARCH_PROVIDER, results=len(unique))
    if not unique:
        raise WebSearchError('Web search found no readable pages for that topic. Try a more specific topic.')
    return unique
