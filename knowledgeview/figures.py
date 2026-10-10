"""Figures for enriched sections.

The section prompt lets the LLM drop placeholder lines where a picture would help:

    [[FIGURE | caption: 한국어 캡션 | search: english keywords | draw: 그릴 그림 설명]]

Each placeholder is resolved in this order:
1. Wikimedia Commons search (reusable licenses only); the LLM picks a candidate by its
   title/description or rejects them all. The thumbnail is embedded as a data URI.
2. Otherwise the LLM draws an SVG from the ``draw`` brief (embedded as a data URI <img>,
   so no script in it can run).
Unresolved placeholders are removed.
"""

from __future__ import annotations

import base64
import html
import json
import re
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from . import prompts
from .llm import LLMClient, LLMError

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "KnowledgeView/0.1 (local study-notes viewer; https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)"
FIGURE_RE = re.compile(r"^(?P<prefix>(?:[ \t]*>[ \t]?)*)[ \t]*\[\[FIGURE\s*\|(?P<body>[^\n]*?)\]\][ \t]*$", re.M)
LICENSE_OK = re.compile(r"^(CC0|Public domain|PD.*|CC BY(-SA)?( \d(\.\d)?)?( [A-Za-z\-]+)?)$", re.I)
IMAGE_MIMES = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
MAX_SVG_CHARS = 200_000


@dataclass
class FigureRequest:
    caption: str
    search: str
    draw: str

    def as_dict(self) -> dict:
        return {"caption": self.caption, "search": self.search, "draw": self.draw}


def parse_request(body: str) -> FigureRequest | None:
    fields: dict[str, str] = {}
    for part in body.split("|"):
        k, sep, v = part.partition(":")
        if sep:
            fields[k.strip().lower()] = v.strip()
    caption = fields.get("caption", "")
    if not caption:
        return None
    return FigureRequest(caption=caption, search=fields.get("search", ""), draw=fields.get("draw", "") or caption)


def find_requests(md: str) -> list[tuple[re.Match, FigureRequest | None]]:
    return [(m, parse_request(m.group("body"))) for m in FIGURE_RE.finditer(md)]


def replace_placeholders(md: str, resolved: list[str | None]) -> str:
    """Replace the n-th placeholder with resolved[n] (an HTML line) or drop it when None."""
    it = iter(resolved)

    def sub(m: re.Match) -> str:
        html_line = next(it, None)
        if not html_line:
            return m.group("prefix").rstrip()
        return m.group("prefix") + html_line

    return FIGURE_RE.sub(sub, md)


def strip_placeholders(md: str) -> str:
    return FIGURE_RE.sub(lambda m: m.group("prefix").rstrip(), md)


def figure_html(fig: dict) -> str:
    """One line of HTML (it may sit inside a '> ' callout)."""
    cap = html.escape(fig.get("caption", ""))
    alt = html.escape(fig.get("alt") or fig.get("caption", ""), quote=True)
    credit = ""
    if fig.get("kind") == "web":
        title = html.escape(re.sub(r"^File:", "", fig.get("title", "")))
        link = f'<a href="{html.escape(fig["page_url"], quote=True)}" target="_blank" rel="noopener">{title}</a>' if fig.get("page_url") else title
        lic = html.escape(fig.get("license", ""))
        if fig.get("license_url"):
            lic = f'<a href="{html.escape(fig["license_url"], quote=True)}" target="_blank" rel="noopener">{lic}</a>'
        artist = html.escape(fig.get("artist", ""))
        credit = f'<span class="kv-credit">출처: Wikimedia Commons, {link}{" · " + artist if artist else ""} · {lic}</span>'
    else:
        credit = '<span class="kv-credit">LLM이 생성한 그림</span>'
    return (f'<figure class="kv-figure kv-added" data-kind="{html.escape(fig.get("kind", ""))}">'
            f'<img src="{fig["data_uri"]}" alt="{alt}" loading="lazy"><figcaption>{cap}{credit}</figcaption></figure>')


# --------------------------------------------------------------------------- web search


def http_client(timeout: float = 20) -> httpx.Client:
    """Use the OS trust store (works behind TLS-inspecting antivirus); fall back to certifi."""
    try:
        import truststore

        verify: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        verify = True
    return httpx.Client(verify=verify, timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _plain(s: str, limit: int = 240) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def query_variants(query: str) -> list[str]:
    """Commons search ANDs every word, so long queries rarely match: also try shorter ones.

    Each variant is searched for drawings first (diagrams), then any non-document file."""
    words = query.split()
    bases = []
    for b in (words, words[:3], words[:2], words[-2:]):
        s = " ".join(b)
        if s and s not in bases:
            bases.append(s)
    out = []
    for b in bases:
        out += [f"{b} filetype:drawing", f"{b} -filemime:pdf -filemime:djvu"]
    return out


def commons_candidates(http: httpx.Client, query: str, want: int = 8, max_requests: int = 6) -> list[dict]:
    seen: dict[str, dict] = {}
    for n, variant in enumerate(query_variants(query)):
        if n >= max_requests or len(seen) >= want:
            break
        for c in commons_search(http, variant, limit=6):
            seen.setdefault(c["title"], c)
    return list(seen.values())[:want]


def commons_search(http: httpx.Client, query: str, limit: int = 8) -> list[dict]:
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": query, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1000,
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ImageDescription|ObjectName",
    }
    r = http.get(COMMONS_API, params=params)
    r.raise_for_status()
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for pg in pages:
        info = (pg.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value", ""), 60)
        if not LICENSE_OK.match(lic) or info.get("mime") not in IMAGE_MIMES:
            continue
        if min(info.get("width", 0), info.get("height", 0)) < 150 or not info.get("thumburl"):
            continue
        out.append({
            "title": pg.get("title", ""),
            "description": _plain((meta.get("ImageDescription") or {}).get("value", "")),
            "thumb_url": info["thumburl"],
            "page_url": info.get("descriptionurl", ""),
            "artist": _plain((meta.get("Artist") or {}).get("value", ""), 80),
            "license": lic,
            "license_url": (meta.get("LicenseUrl") or {}).get("value", ""),
            "size": f"{info.get('width')}x{info.get('height')}",
        })
    return out


def download_data_uri(http: httpx.Client, url: str, max_bytes: int) -> str:
    r = http.get(url)
    r.raise_for_status()
    mime = r.headers.get("content-type", "").split(";")[0].strip()
    if mime not in IMAGE_MIMES:
        raise ValueError(f"이미지가 아닌 응답: {mime}")
    if len(r.content) > max_bytes:
        raise ValueError(f"이미지가 너무 큼: {len(r.content)} bytes")
    return f"data:{mime};base64,{base64.b64encode(r.content).decode()}"


def _json_obj(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("JSON을 찾지 못함")
    return json.loads(text[a:b + 1])


def choose_candidate(client: LLMClient, req: FigureRequest, cands: list[dict]) -> int | None:
    listing = "\n".join(
        f"{i}. {c['title']} ({c['size']}) — {c['description'] or '설명 없음'}" for i, c in enumerate(cands, 1)
    )
    user = prompts.FIGURE_PICK_USER.format(caption=req.caption, draw=req.draw, candidates=listing)
    choice = _json_obj(client.complete(prompts.FIGURE_PICK_SYSTEM, user)).get("choice")
    if isinstance(choice, int) and 1 <= choice <= len(cands):
        return choice - 1
    return None


# --------------------------------------------------------------------------- SVG


def sanitize_svg(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"<svg\b.*</svg>", text, re.S | re.I)
    if not m:
        raise ValueError("SVG를 찾지 못함")
    svg = m.group(0)
    if len(svg) > MAX_SVG_CHARS:
        raise ValueError("SVG가 너무 큼")
    if re.search(r"<!(DOCTYPE|ENTITY)", svg, re.I):
        raise ValueError("DOCTYPE/ENTITY가 있는 SVG")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    if "xmlns=" not in svg[:300]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    try:
        rootel = ET.fromstring(svg)
    except ET.ParseError as e:
        raise ValueError(f"SVG 파싱 실패: {e}") from e
    banned = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}
    for parent in list(rootel.iter()):
        for child in list(parent):
            if child.tag.split("}")[-1].lower() in banned:
                parent.remove(child)
    for el in rootel.iter():
        for attr in list(el.attrib):
            name = attr.split("}")[-1].lower()
            val = el.attrib[attr].strip().lower()
            if name.startswith("on") or (name == "href" and not val.startswith("#")):
                del el.attrib[attr]
    if not rootel.get("viewBox") and rootel.get("width") and rootel.get("height"):
        w, h = (re.sub(r"[^\d.]", "", rootel.get(k) or "") for k in ("width", "height"))
        if w and h:
            rootel.set("viewBox", f"0 0 {w} {h}")
    return ET.tostring(rootel, encoding="unicode")


def draw_svg(client: LLMClient, req: FigureRequest, context: str) -> str:
    user = prompts.FIGURE_SVG_USER.format(caption=req.caption, draw=req.draw, context=context[:4000])
    svg = sanitize_svg(client.complete(prompts.FIGURE_SVG_SYSTEM, user))
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


# --------------------------------------------------------------------------- resolve


def resolve(req: FigureRequest, *, client: LLMClient, cfg: dict, context: str, log) -> dict | None:
    """Return a figure dict ({kind, data_uri, caption, ...}) or None."""
    if cfg.get("web_search", True) and req.search:
        try:
            with http_client() as http:
                cands = commons_candidates(http, req.search)
                if cands:
                    idx = choose_candidate(client, req, cands)
                    if idx is not None:
                        c = cands[idx]
                        uri = download_data_uri(http, c["thumb_url"], int(cfg.get("max_bytes") or 1_500_000))
                        return {"kind": "web", "data_uri": uri, "caption": req.caption, **{k: c[k] for k in (
                            "title", "page_url", "artist", "license", "license_url")}}
                log(f"웹 이미지 없음: {req.search} (후보 {len(cands)}개)")
        except (httpx.HTTPError, ValueError, LLMError) as e:
            log(f"웹 이미지 검색 실패: {req.search}: {e}")
    if cfg.get("svg_fallback", True):
        try:
            return {"kind": "svg", "data_uri": draw_svg(client, req, context), "caption": req.caption}
        except (ValueError, LLMError) as e:
            log(f"SVG 생성 실패: {req.caption}: {e}")
    return None
