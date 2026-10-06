"""Opt-in, one-page EDHREC reader. Routes and raw shape need live validation.

The public site has no documented API contract. This module must not be enabled
until automated access is independently authorized and a minimal page sample
confirms the proposed route and field mapping. No retries, redirects, cache,
continuations, credentials, or background work are provided.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import multiprocessing
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .card_catalog import CatalogError
from .edhrec_compare import (MAX_AVERAGE_DECK_CARDS, MAX_BODY_BYTES,
                             MAX_SOURCE_CARDS, MAX_THEMES, SourceHTTPError,
                             _number, _slug)


ORIGIN = 'https://json.edhrec.com'
USER_AGENT = 'CommanderGymCatalog/0.1 (+https://github.com/Blue42hand/commander-gym)'
TIMEOUT_SECONDS = 5
TOTAL_SECONDS = 10


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _fetch_project_once(url: str, commander_slug: str,
                        theme_slug: str | None, timeout: float, resolution_delay: float) -> bytes:
    """Run only inside the disposable worker process."""
    if resolution_delay:
        real_getaddrinfo = socket.getaddrinfo
        def slow_getaddrinfo(*args, **kwargs):
            time.sleep(resolution_delay)
            return real_getaddrinfo(*args, **kwargs)
        socket.getaddrinfo = slow_getaddrinfo
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, headers={'User-Agent': USER_AGENT,
                                     'Accept': 'application/json',
                                     'Accept-Encoding': 'identity'}, method='GET')
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise SourceHTTPError(response.status)
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise CatalogError('EDHREC compressed response unsupported')
            if response.headers.get_content_type() != 'application/json':
                raise CatalogError('EDHREC response is not JSON')
            declared = response.headers.get('Content-Length')
            if declared is not None:
                try:
                    if int(declared) > MAX_BODY_BYTES:
                        raise CatalogError('EDHREC context exceeds byte limit')
                except ValueError as exc:
                    raise CatalogError('invalid EDHREC content length') from exc
            chunks, total = [], 0
            read_available = getattr(response, 'read1', response.read)
            while True:
                chunk = read_available(65_536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise CatalogError('EDHREC context exceeds byte limit')
                chunks.append(chunk)
    except HTTPError as exc:
        raise SourceHTTPError(exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CatalogError('EDHREC context unavailable') from exc
    projected = project_page(b''.join(chunks), url, commander_slug, theme_slug)
    if len(projected) > MAX_BODY_BYTES:
        raise CatalogError('EDHREC context exceeds byte limit')
    return projected


def _fetch_worker(send, url: str, commander_slug: str,
                  theme_slug: str | None, timeout: float, resolution_delay: float) -> None:
    try:
        try:
            result = ('ok', _fetch_project_once(url, commander_slug,
                                                theme_slug, timeout, resolution_delay))
        except SourceHTTPError as exc:
            result = ('http', exc.status)
        except CatalogError as exc:
            result = ('error', str(exc))
        except BaseException:
            result = ('error', 'EDHREC context unavailable')
        send.send(result)
    except (OSError, EOFError, BrokenPipeError):
        pass
    finally:
        send.close()


class HttpEdhrecSource:
    """Return the internal context envelope as bytes for ContextReader."""

    def __init__(self, *, origin: str = ORIGIN, allow_loopback_for_tests: bool = False,
                 _test_resolution_delay: float = 0):
        parsed = urlsplit(origin)
        if origin != ORIGIN and not (allow_loopback_for_tests and parsed.scheme == 'http'
                                     and parsed.hostname == '127.0.0.1' and parsed.port
                                     and parsed.path == '' and not parsed.username
                                     and not parsed.password and not parsed.query):
            raise CatalogError('EDHREC source origin is not allowed')
        if _test_resolution_delay and (not allow_loopback_for_tests or
                                       not 0 < _test_resolution_delay <= 30):
            raise CatalogError('test delay requires loopback test mode')
        self.origin = origin
        self._test_resolution_delay = _test_resolution_delay
        self._last_process = None

    def __call__(self, commander_slug: str, theme_slug: str | None) -> bytes:
        commander_slug = _slug(commander_slug, 'commander_slug')
        if theme_slug is not None:
            theme_slug = _slug(theme_slug, 'theme_slug')
        # Theme route is a proposal based on third-party clients, not verified
        # against a current EDHREC page. A mismatch fails; no route probing.
        path = (f'/pages/commanders/{commander_slug}.json' if theme_slug is None else
                f'/pages/commanders/{commander_slug}/{theme_slug}.json')
        url = self.origin + path
        # DNS lookup, TLS, response headers, body, and parsing all run in a
        # disposable process. The parent owns the wall deadline and reaps it.
        ctx = multiprocessing.get_context('spawn')
        receive, send = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_fetch_worker,
                              args=(send, url, commander_slug, theme_slug,
                                    TIMEOUT_SECONDS, self._test_resolution_delay))
        self._last_process = process
        started = time.monotonic()
        launched = False
        try:
            process.start()
            launched = True
            send.close()
            remaining = max(0.0, TOTAL_SECONDS - (time.monotonic() - started))
            if not receive.poll(remaining):
                raise CatalogError('EDHREC request timed out')
            try:
                kind, value = receive.recv()
            except EOFError as exc:
                raise CatalogError('EDHREC context unavailable') from exc
            if time.monotonic() - started > TOTAL_SECONDS:
                raise CatalogError('EDHREC request timed out')
            if kind == 'http':
                raise SourceHTTPError(value)
            if kind == 'error':
                raise CatalogError(value)
            if kind != 'ok' or not isinstance(value, bytes) or len(value) > MAX_BODY_BYTES:
                raise CatalogError('EDHREC context unavailable')
            return value
        finally:
            receive.close()
            send.close()
            if launched:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)


def project_page(raw: bytes, url: str, commander_slug: str,
                 theme_slug: str | None) -> bytes:
    """Project one proposed public JSON page; reject unknown shape."""
    if len(raw) > MAX_BODY_BYTES:
        raise CatalogError('EDHREC context exceeds byte limit')
    try:
        page = json.loads(raw)
        data = page['container']['json_dict']
        commander_name = data['card']['name']
        lists = data['cardlists']
        source_theme = data['selected_theme_slug']
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise CatalogError('unrecognized EDHREC page shape') from exc
    if not isinstance(commander_name, str) or not isinstance(lists, list):
        raise CatalogError('unrecognized EDHREC page shape')
    if len(lists) > 100:
        raise CatalogError('EDHREC card list count exceeds limit')
    if source_theme != theme_slug:
        raise CatalogError('EDHREC source theme does not match requested context')
    cards = []
    for group in lists:
        if not isinstance(group, dict) or not isinstance(group.get('cardviews'), list):
            raise CatalogError('unrecognized EDHREC card list')
        for item in group['cardviews']:
            if not isinstance(item, dict):
                raise CatalogError('unrecognized EDHREC card row')
            synergy = _number(item.get('synergy'), 'source synergy')
            if synergy is not None and not -1 <= synergy <= 1:
                raise CatalogError('unrecognized EDHREC synergy unit')
            cards.append({'name': item.get('name'),
                          'oracle_id': item.get('oracle_id'),
                          'inclusion_count': item.get('num_decks'),
                          'potential_decks': item.get('potential_decks'),
                          'lift_ratio': item.get('lift'),
                          'synergy_percent': None if synergy is None else synergy * 100,
                          'average_quantity': None})
            if len(cards) > MAX_SOURCE_CARDS:
                raise CatalogError('EDHREC card list exceeds limit')
    panels = page.get('panels')
    themes = None
    if isinstance(panels, dict) and isinstance(panels.get('taglinks'), list):
        if len(panels['taglinks']) > MAX_THEMES:
            raise CatalogError('EDHREC theme list exceeds limit')
        themes = [{'name': item.get('value'), 'slug': item.get('slug'),
                   'deck_count': item.get('count')}
                  for item in panels['taglinks'] if isinstance(item, dict)]
        if len(themes) != len(panels['taglinks']):
            raise CatalogError('unrecognized EDHREC theme row')
    average = data.get('average_deck', data.get('avgdeck'))
    if average is not None and not isinstance(average, list):
        raise CatalogError('unrecognized EDHREC average deck')
    if average is not None:
        if len(average) > MAX_AVERAGE_DECK_CARDS:
            raise CatalogError('EDHREC average deck exceeds limit')
        if any(not isinstance(item, dict) for item in average):
            raise CatalogError('unrecognized EDHREC average deck')
        average = [{'name': item.get('name'), 'quantity': item.get('quantity')}
                   for item in average]
    projected = {'commander_slug': commander_slug, 'commander_name': commander_name,
                 'theme_slug': theme_slug, 'source_url': url,
                 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                 'cards': cards, 'themes': themes, 'average_deck': average}
    try:
        return json.dumps(projected, allow_nan=False, ensure_ascii=True).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CatalogError('unrecognized EDHREC page values') from exc
