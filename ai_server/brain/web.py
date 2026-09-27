"""Internet search for Altron: how to use a mod's item, what something is. Free and without keys:
DuckDuckGo's HTML page (Bing's as a fallback), then the best pages are read and only the passages
that fit the question are kept, so the small model gets a short, relevant text."""
import asyncio
import html
import re
import urllib.parse

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ru,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
SKIP_SITES = ("youtube.com", "youtu.be", "tiktok.com", "vk.com/video", "twitch.tv", "pinterest.")
RESULT_CHARS = 3500


def _text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).replace("\xa0", " ").strip()


def _words(s):
    return {w for w in re.findall(r"[a-zа-яё0-9]{3,}", s.lower().replace("ё", "е"))}


async def _duckduckgo(client, query):
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "ru-ru"})
    r.raise_for_status()
    out = []
    for block in r.text.split('class="result results_links')[1:]:
        a = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not a:
            continue
        href = html.unescape(a.group(1))
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        url = target[0] if target else href
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, flags=re.S)
        out.append({"url": url, "title": _text(a.group(2)), "snippet": _text(snippet.group(1)) if snippet else ""})
    return out


async def _bing(client, query):
    r = await client.get("https://www.bing.com/search", params={"q": query, "setlang": "ru"})
    r.raise_for_status()
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', r.text, flags=re.S):
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not a:
            continue
        snippet = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.S)
        out.append({"url": html.unescape(a.group(1)), "title": _text(a.group(2)),
                    "snippet": _text(snippet.group(1)) if snippet else ""})
    return out


async def _read(client, url, question, limit=1400):
    """The passages of a page that share the most words with the question."""
    try:
        r = await client.get(url, timeout=12)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return ""   # some wikis turn programs away (403)
        page = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", r.text)
        page = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr|td)>", "\n", page)
        lines = [re.sub(r"\s+", " ", _text(x)) for x in page.split("\n")]
        lines = [x for x in lines if len(x) > 40]
    except Exception:
        return ""
    q = _words(question)
    ranked = sorted(range(len(lines)), key=lambda i: -len(q & _words(lines[i])))
    keep, size = [], 0
    for i in ranked[:12]:
        if not q & _words(lines[i]) or size > limit:
            break
        keep.append(i)
        size += len(lines[i])
    return "\n".join(lines[i][:500] for i in sorted(keep))


async def search(query, pages=2):
    """Text for the model: the top results with their snippets, and the fitting parts of the best pages."""
    query = query.strip()
    if not query:
        return "пустой запрос"
    # trust_env: the internet goes through the proxy set up in Windows, if there is one
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True, trust_env=True) as client:
        results = []
        for engine in (_duckduckgo, _bing):
            try:
                results = await engine(client, query)
            except Exception:
                results = []
            if results:
                break
        if not results:
            return "Интернет-поиск ничего не дал (нет сети или поисковик не ответил)."
        results = [r for r in results if not any(s in r["url"] for s in SKIP_SITES)][:6]
        # read a few pages at once; the first ones that let us in are used
        texts = await asyncio.gather(*[_read(client, r["url"], query) for r in results[:pages + 2]])
    out = ["Найдено в интернете по запросу «%s»:" % query]
    for r in results:
        out.append("- %s (%s): %s" % (r["title"], urllib.parse.urlparse(r["url"]).netloc, r["snippet"][:250]))
    used = 0
    for r, t in zip(results, texts):
        if t and used < pages:
            used += 1
            out.append("\nСо страницы %s:\n%s" % (urllib.parse.urlparse(r["url"]).netloc, t))
    return "\n".join(out)[:RESULT_CHARS]


if __name__ == "__main__":
    import sys
    print(asyncio.run(search(" ".join(sys.argv[1:]) or "SecurityCraft sentry remote access tool как пользоваться")))
