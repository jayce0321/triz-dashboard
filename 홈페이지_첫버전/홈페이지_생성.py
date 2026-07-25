from __future__ import annotations

import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
THESES_DIR = ROOT / "테제"
DASHBOARD_FILE = ROOT / "대시보드목록.json"
HOYA_SOURCE_FILE = ROOT / "호야테제_연결.json"
HOYA_CACHE_FILE = ROOT / "호야테제_캐시.json"
OUTPUT_FILE = ROOT / "index.html"


@dataclass
class Thesis:
    title: str
    date: str
    summary: str
    tags: list[str]
    status: str
    slug: str
    body_html: str
    updated_at: float


def local_thesis_date(path: Path, meta: dict[str, str], body: str) -> str:
    meta_date = meta.get("date", "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", meta_date):
        return meta_date

    filename_date = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if filename_date:
        return filename_date.group(1)

    body_date = re.search(r"작성\s*기준\s*시각:\s*(\d{4}-\d{2}-\d{2})", body)
    if body_date:
        return body_date.group(1)

    return datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


@dataclass
class HoyaThesis:
    key: str
    name: str
    date: str
    description: str
    url: str
    title: str
    quote: str
    status: str
    published_ts: float = 0


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text

    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.S)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, match.group(2)


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_html(markdown: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        image_match = re.match(r"^!\[(.*?)\]\((.*?)\)$", line)
        if image_match:
            flush_paragraph()
            alt = html.escape(image_match.group(1))
            src = html.escape(image_match.group(2), quote=True)
            blocks.append(f'<figure class="article-image"><img src="{src}" alt="{alt}"><figcaption>{alt}</figcaption></figure>')
        elif line.startswith("### "):
            flush_paragraph()
            blocks.append(f"<h3>{inline_markdown(line[4:])}</h3>")
        elif line.startswith("## "):
            flush_paragraph()
            blocks.append(f"<h2>{inline_markdown(line[3:])}</h2>")
        elif line.startswith("# "):
            flush_paragraph()
            blocks.append(f"<h1>{inline_markdown(line[2:])}</h1>")
        else:
            paragraph.append(line)

    flush_paragraph()
    return "\n".join(blocks)


def markdown_excerpt(markdown: str, limit: int = 120) -> str:
    text = re.sub(r"^#+\s*", "", markdown, flags=re.M)
    text = re.sub(r"[*_`>#-]", "", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def strip_html(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", "", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", "", value, flags=re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def fetch_url(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hoya-lab/1.0"})
        with urllib.request.urlopen(req, timeout=6) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def parse_rss_items(feed_xml: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return []

    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        data: dict[str, str] = {}
        for key in ("title", "link", "description", "pubDate", "category"):
            element = item.find(key)
            data[key] = strip_html(element.text or "") if element is not None else ""
        if data.get("link"):
            items.append(data)
    return items


def topic_matches_feed_item(topic_key: str, item: dict[str, str]) -> bool:
    link = item.get("link", "")
    category = item.get("category", "")
    filename = link.rsplit("/", 1)[-1]

    if topic_key == "weekly_digest":
        return filename.startswith("weekly-") or "주간" in category or "다이제스트" in category
    if topic_key == "economy":
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}\.html$", filename)) or category == "경제·투자"
    if topic_key == "economy_pm":
        return filename.endswith("-pm.html") or category == "경제·투자 (오후)"
    if topic_key in ("politics", "culture"):
        return filename.endswith(f"-{topic_key}.html")
    return False


def rss_timestamp(value: str) -> float:
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, AttributeError):
        return 0


def split_feed_description(description: str, fallback_title: str) -> tuple[str, str]:
    description = description.strip()
    quoted = re.match(r'^("[^"]+")\s+—\s+(.+)$', description)
    if quoted:
        return quoted.group(1), quoted.group(2)
    if " — " in description:
        title, quote = description.split(" — ", 1)
        return title.strip(), quote.strip()
    return fallback_title, description


def feed_item_date(item: dict[str, str], fallback: str) -> str:
    title_date = re.search(r"(\d{4})[.](\d{2})[.](\d{2})", item.get("title", ""))
    if title_date:
        return f"{title_date.group(1)}-{title_date.group(2)}-{title_date.group(3)}"
    link_date = re.search(r"(\d{4}-\d{2}-\d{2})", item.get("link", ""))
    if link_date:
        return link_date.group(1)
    try:
        return parsedate_to_datetime(item.get("pubDate", "")).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    except (TypeError, ValueError, AttributeError):
        return fallback


def feed_item_to_hoya_thesis(topic: dict[str, str], item: dict[str, str], today: str) -> HoyaThesis:
    filename = item["link"].rsplit("/", 1)[-1]
    published_date = feed_item_date(item, published_date_from_filename(filename, today))
    description = item.get("description", "") or item.get("title", "")
    title, quote = split_feed_description(description, item.get("title", ""))
    return HoyaThesis(
        key=topic["key"],
        name=topic["name"],
        date=published_date,
        description=topic["description"],
        url=item["link"],
        title=title[:120],
        quote=quote[:240],
        status="오늘 인용" if published_date == today else "최신 인용",
        published_ts=rss_timestamp(item.get("pubDate", "")),
    )


def latest_hoya_from_feed(source: dict[str, object], topic: dict[str, str], today: str) -> HoyaThesis | None:
    feed_url = str(source.get("feed_url") or "").strip()
    if not feed_url:
        return None
    feed_xml = fetch_url(feed_url)
    if not feed_xml:
        return None
    for item in parse_rss_items(feed_xml):
        if topic_matches_feed_item(topic["key"], item):
            return feed_item_to_hoya_thesis(topic, item, today)
    return None


def extract_json_ld_article(raw: str) -> tuple[str, str] | None:
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        raw,
        flags=re.I,
    )
    for script in scripts:
        try:
            data = json.loads(html.unescape(script.strip()))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type == "Article" or (isinstance(item_type, list) and "Article" in item_type):
                title = strip_html(str(item.get("headline", "")))
                quote = strip_html(str(item.get("description", "")))
                if title and quote:
                    return title, quote
    return None


def fetch_hoya_excerpt(url: str) -> tuple[str, str, str]:
    raw = fetch_url(url)
    if not raw:
        return "발행 대기 또는 네트워크 확인 필요", "현재 빌드 환경에서는 본문을 가져오지 못했습니다. 원문 링크에서 Hoya Bot 테제를 확인할 수 있습니다.", "연결"

    json_ld = extract_json_ld_article(raw)
    if json_ld:
        return json_ld[0][:120], json_ld[1][:220], "인용"

    title_match = re.search(r"<title>(.*?)</title>", raw, flags=re.I | re.S)
    meta_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', raw, flags=re.I | re.S)
    h2_match = re.search(r"<h2[^>]*>(.*?)</h2>", raw, flags=re.I | re.S)
    title = strip_html(title_match.group(1)) if title_match else "Hoya Bot 데일리 테제"
    quote = strip_html(meta_match.group(1)) if meta_match else ""
    if not quote:
        quote = strip_html(h2_match.group(1)) if h2_match else strip_html(raw)[:160]
    return title[:120], quote[:220], "인용"


def published_date_from_filename(filename: str, fallback: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return match.group(1) if match else fallback


def latest_links_from_home(base_url: str, topic_key: str) -> list[str]:
    raw = fetch_url(f"{base_url}/")
    if not raw:
        return []

    hrefs = re.findall(r'href=["\']([^"\']+\.html)["\']', raw, flags=re.I)
    if topic_key == "weekly_digest":
        pattern = re.compile(r"^weekly-\d{4}-W\d{1,2}\.html$")
    elif topic_key == "economy":
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")
    elif topic_key == "economy_pm":
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-pm\.html$")
    else:
        pattern = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(topic_key)}\.html$")

    links = []
    for href in hrefs:
        filename = href.rsplit("/", 1)[-1]
        if pattern.match(filename):
            links.append(filename)
    return sorted(set(links), reverse=True)


def recent_candidate_filenames(topic: dict[str, str], today: str, days: int = 14) -> list[str]:
    if topic.get("key") == "weekly_digest":
        start = datetime.strptime(today, "%Y-%m-%d").date()
        return [
            topic["filename"].format(
                year=(start - timedelta(days=offset * 7)).isocalendar().year,
                week=str((start - timedelta(days=offset * 7)).isocalendar().week).zfill(2),
            )
            for offset in range(8)
        ]
    start = datetime.strptime(today, "%Y-%m-%d").date()
    filenames = []
    for offset in range(days):
        day = (start - timedelta(days=offset)).isoformat()
        filenames.append(topic["filename"].format(today=day))
    return filenames


def topic_filename(topic: dict[str, str], day: str) -> str:
    date_value = datetime.strptime(day, "%Y-%m-%d").date()
    iso = date_value.isocalendar()
    return topic["filename"].format(today=day, year=iso.year, week=str(iso.week).zfill(2))


def load_theses() -> list[Thesis]:
    theses: list[Thesis] = []
    for path in sorted(THESES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        tags = [tag.strip() for tag in meta.get("tags", "").split(",") if tag.strip()]
        summary = meta.get("summary", "").strip() or markdown_excerpt(body)
        date = local_thesis_date(path, meta, body)
        theses.append(
            Thesis(
                title=meta.get("title", path.stem),
                date=date,
                summary=summary,
                tags=tags,
                status=meta.get("status", "발행"),
                slug=path.stem,
                body_html=markdown_to_html(body),
                updated_at=path.stat().st_mtime,
            )
        )
    return sorted(theses, key=lambda item: (item.date, item.updated_at, item.slug), reverse=True)


def load_dashboards() -> list[dict[str, object]]:
    return json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))


def load_hoya_cache(today: str) -> dict[str, HoyaThesis]:
    if not HOYA_CACHE_FILE.exists():
        return {}
    try:
        items = json.loads(HOYA_CACHE_FILE.read_text(encoding="utf-8"))
        cache: dict[str, HoyaThesis] = {}
        for item in items:
            item["status"] = "오늘 인용" if item.get("date") == today else "최신 인용"
            cache[item["key"]] = HoyaThesis(**item)
        return cache
    except (OSError, TypeError, ValueError, KeyError):
        return {}


def save_hoya_cache(theses: list[HoyaThesis]) -> None:
    live_items = [item for item in theses if item.status != "연결"]
    if not live_items:
        return
    payload = [
        {
            "key": item.key,
            "name": item.name,
            "date": item.date,
            "description": item.description,
            "url": item.url,
            "title": item.title,
            "quote": item.quote,
            "status": item.status,
            "published_ts": item.published_ts,
        }
        for item in live_items
    ]
    HOYA_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_hoya_theses(today: str) -> list[HoyaThesis]:
    source = json.loads(HOYA_SOURCE_FILE.read_text(encoding="utf-8"))
    base_url = source["base_url"].rstrip("/")
    cache = load_hoya_cache(today)
    theses: list[HoyaThesis] = []
    for topic in source["topics"]:
        feed_thesis = latest_hoya_from_feed(source, topic, today)
        if feed_thesis:
            theses.append(feed_thesis)
            continue

        home_candidates = latest_links_from_home(base_url, topic["key"])
        candidates = [
            topic_filename(topic, today),
            *home_candidates,
            *recent_candidate_filenames(topic, today),
        ]
        seen: set[str] = set()
        title = "발행 대기 또는 네트워크 확인 필요"
        quote = "현재 빌드 환경에서는 본문을 가져오지 못했습니다. 원문 링크에서 Hoya Bot 테제를 확인할 수 있습니다."
        status = "연결"
        url = f"{base_url}/"
        published_date = today
        for filename in candidates:
            if filename in seen:
                continue
            seen.add(filename)
            candidate_url = f"{base_url}/{filename}"
            candidate_title, candidate_quote, candidate_status = fetch_hoya_excerpt(candidate_url)
            if candidate_status == "인용":
                title = candidate_title
                quote = candidate_quote
                url = candidate_url
                published_date = published_date_from_filename(filename, today)
                status = "오늘 인용" if published_date == today else "최신 인용"
                break
        if status == "연결" and topic["key"] in cache:
            theses.append(cache[topic["key"]])
            continue
        theses.append(
            HoyaThesis(
                key=topic["key"],
                name=topic["name"],
                date=published_date,
                description=topic["description"],
                url=url,
                title=title,
                quote=quote,
                status=status,
            )
        )
    save_hoya_cache(theses)
    return theses


def tag_html(tags: list[str]) -> str:
    return "".join(f"<span>{html.escape(tag)}</span>" for tag in tags)


def dashboard_cards(dashboards: list[dict[str, object]]) -> str:
    cards: list[str] = []
    for item in dashboards:
        tech = "".join(f"<span>{html.escape(str(value))}</span>" for value in item.get("tech", []))
        cards.append(
            f"""
            <article class="dashboard-card">
              <a class="dashboard-image" href="{html.escape(str(item.get("url", "#")))}" target="_blank" rel="noreferrer">
                <img src="{html.escape(str(item.get("image", "")))}" alt="{html.escape(str(item.get("title", "대시보드")))} 미리보기">
              </a>
              <div class="dashboard-body">
                <div class="card-meta">
                  <span>{html.escape(str(item.get("category", "대시보드")))}</span>
                  <strong>{html.escape(str(item.get("status", "")))}</strong>
                </div>
                <h3>{html.escape(str(item.get("title", "")))}</h3>
                <p>{html.escape(str(item.get("description", "")))}</p>
                <div class="tech-stack">{tech}</div>
                <a class="text-link" href="{html.escape(str(item.get("url", "#")))}" target="_blank" rel="noreferrer">열기</a>
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def thesis_cards(theses: list[Thesis]) -> str:
    cards: list[str] = []
    for thesis in theses:
        cards.append(
            f"""
            <article class="thesis-card">
              <div class="card-meta">
                <span>{html.escape(thesis.date)}</span>
                <strong>{html.escape(thesis.status)}</strong>
              </div>
              <h3>{html.escape(thesis.title)}</h3>
              <p>{html.escape(thesis.summary)}</p>
              <div class="tag-row">{tag_html(thesis.tags)}</div>
              <a class="text-link" href="#{html.escape(thesis.slug)}">본문 읽기</a>
            </article>
            """
        )
    return "\n".join(cards)


def hoya_thesis_cards(theses: list[HoyaThesis]) -> str:
    cards: list[str] = []
    for thesis in theses:
        cards.append(
            f"""
            <article class="thesis-card hoya-card">
              <div class="card-meta">
                <span>{html.escape(thesis.date)} · {html.escape(thesis.name)}</span>
                <strong>{html.escape(thesis.status)}</strong>
              </div>
              <h3>{html.escape(thesis.title)}</h3>
              <p>{html.escape(thesis.description)}</p>
              <blockquote>{html.escape(thesis.quote)}</blockquote>
              <a class="text-link" href="{html.escape(thesis.url)}" target="_blank" rel="noreferrer">원문 열기</a>
            </article>
            """
        )
    return "\n".join(cards)


def thesis_articles(theses: list[Thesis]) -> str:
    articles: list[str] = []
    for index, thesis in enumerate(theses):
        if index == 0:
            articles.append(
                f"""
                <article class="article featured-article" id="{html.escape(thesis.slug)}">
                  <div class="article-kicker">최신 보조테제 · {html.escape(thesis.date)} · {html.escape(thesis.status)}</div>
                  <h2>{html.escape(thesis.title)}</h2>
                  <p class="article-summary">{html.escape(thesis.summary) if thesis.summary else "요약문이 비어 있습니다."}</p>
                  <div class="tag-row">{tag_html(thesis.tags)}</div>
                  <div class="article-body">
                    {thesis.body_html}
                  </div>
                </article>
                """
            )
            continue

        articles.append(
            f"""
            <details class="article collapsed-article" id="{html.escape(thesis.slug)}">
              <summary>
                <span class="article-kicker">{html.escape(thesis.date)} · {html.escape(thesis.status)}</span>
                <strong>{html.escape(thesis.title)}</strong>
                <span>{html.escape(thesis.summary) if thesis.summary else "요약문이 비어 있습니다."}</span>
              </summary>
              <div class="tag-row">{tag_html(thesis.tags)}</div>
              <div class="article-body">
                {thesis.body_html}
              </div>
            </details>
            """
        )
    return "\n".join(articles)


def source_note() -> str:
    source = json.loads(HOYA_SOURCE_FILE.read_text(encoding="utf-8"))
    repo = html.escape(source["repository"])
    base_url = html.escape(source["base_url"])
    return f"""
      <div class="source-note">
        <strong>테제 인용 원천</strong>
        <span>Hoya Bot이 <code>{repo}</code> 저장소의 GitHub Actions로 발행하는 데일리 테제를 인용합니다.</span>
        <a href="{base_url}" target="_blank" rel="noreferrer">발행 홈 열기</a>
      </div>
    """


def hero_thesis_panel(thesis: HoyaThesis | None) -> str:
    if not thesis:
        return """
        <aside class="hero-panel" id="heroThesisPanel">
          <span class="panel-label">TODAY'S THESIS</span>
          <h2>Hoya Bot 테제 연결 대기</h2>
          <p>daily thesis feed를 확인하고 있습니다.</p>
        </aside>
        """
    return f"""
        <aside class="hero-panel" id="heroThesisPanel">
          <span class="panel-label">{html.escape(thesis.date)} · {html.escape(thesis.name)} · {html.escape(thesis.status)}</span>
          <h2>{html.escape(thesis.title)}</h2>
          <p>{html.escape(thesis.quote)}</p>
          <a href="{html.escape(thesis.url)}" target="_blank" rel="noreferrer">원문 보기</a>
        </aside>
        """


def local_editor_panel() -> str:
    return """
      <div class="local-editor" id="local-editor">
        <div class="editor-toolbar">
          <label class="file-drop" for="localThesisFile">
            <strong>Markdown 파일 올리기</strong>
            <span>.md 파일을 선택하면 메타데이터와 본문을 읽어옵니다.</span>
            <input id="localThesisFile" type="file" accept=".md,.markdown,.txt">
          </label>
          <div class="editor-actions">
            <button class="editor-button primary" type="button" id="downloadLocalThesis">Markdown 다운로드</button>
            <button class="editor-button" type="button" id="copyLocalThesis">전체 복사</button>
            <button class="editor-button" type="button" id="copyApplyCommand">반영 명령 복사</button>
            <button class="editor-button" type="button" id="clearLocalThesis">비우기</button>
          </div>
        </div>
        <div class="editor-grid">
          <div class="editor-fields" aria-label="로컬 보조 테제 입력">
            <label>제목<input id="localTitle" type="text" placeholder="예: 오늘 테제에 대한 보조 분석"></label>
            <label>날짜<input id="localDate" type="date"></label>
            <label>요약<textarea id="localSummary" rows="3" placeholder="홈페이지 카드에 보일 한두 문장 요약"></textarea></label>
            <label>태그<input id="localTags" type="text" placeholder="경제, 반도체, 보조테제"></label>
            <label>상태
              <select id="localStatus">
                <option>초안</option>
                <option>검토</option>
                <option>발행</option>
              </select>
            </label>
            <label>본문 붙여넣기<textarea id="localBody" rows="14" placeholder="여기에 Markdown 본문을 붙여넣으세요."></textarea></label>
            <div class="image-assistant">
              <div class="image-assistant-head">
                <div>
                  <strong>이미지컷 3개 준비</strong>
                  <span>본문을 바탕으로 이미지컷 3개를 만들고, 마음에 드는 컷을 대표 이미지로 선택합니다.</span>
                </div>
                <button class="editor-button" type="button" id="generateImagePrompts">이미지컷 생성</button>
              </div>
              <div class="image-status" id="imageStatus">아직 이미지컷을 생성하지 않았습니다.</div>
              <div class="image-options">
                <label class="image-option">
                  <input type="radio" name="selectedImage" value="0" checked>
                  <span>컷 1 · 대표 이미지</span>
                  <textarea id="imagePrompt0" rows="4" placeholder="프롬프트가 여기에 생성됩니다."></textarea>
                  <input id="imageFile0" type="file" accept="image/*">
                  <img id="imagePreview0" alt="컷 1 미리보기">
                </label>
                <label class="image-option">
                  <input type="radio" name="selectedImage" value="1">
                  <span>컷 2 · 데이터/차트형</span>
                  <textarea id="imagePrompt1" rows="4" placeholder="프롬프트가 여기에 생성됩니다."></textarea>
                  <input id="imageFile1" type="file" accept="image/*">
                  <img id="imagePreview1" alt="컷 2 미리보기">
                </label>
                <label class="image-option">
                  <input type="radio" name="selectedImage" value="2">
                  <span>컷 3 · 추상/연구실형</span>
                  <textarea id="imagePrompt2" rows="4" placeholder="프롬프트가 여기에 생성됩니다."></textarea>
                  <input id="imageFile2" type="file" accept="image/*">
                  <img id="imagePreview2" alt="컷 3 미리보기">
                </label>
              </div>
            </div>
          </div>
          <div class="editor-preview" aria-live="polite">
            <div class="article-kicker" id="previewKicker">LOCAL THESIS</div>
            <h3 id="previewTitle">로컬 보조 테제 편집</h3>
            <p id="previewSummary">파일을 올리거나 내용을 붙여넣으면 이곳에 미리보기가 표시됩니다.</p>
            <div class="tag-row" id="previewTags"></div>
            <div class="article-body" id="previewBody"></div>
            <div class="save-note">
              <strong id="downloadName">파일명 미리보기</strong>
              <span>다운로드한 파일을 <code>홈페이지_첫버전/테제/</code> 폴더에 넣고 <code>python3 홈페이지_생성.py</code>를 실행하면 아래 아카이브에 반영됩니다.</span>
            </div>
            <div class="next-step">
              <strong>다운로드 다음 단계</strong>
              <span>방금 받은 Markdown을 실제 홈페이지 아카이브에 넣으려면 터미널에서 아래 명령을 실행하세요.</span>
              <code id="applyCommandText">cd "홈페이지_첫버전" && python3 로컬보조테제_반영.py</code>
            </div>
          </div>
        </div>
      </div>
    """


def hoya_live_sync_script() -> str:
    source = json.loads(HOYA_SOURCE_FILE.read_text(encoding="utf-8"))
    feed_url = json.dumps(source.get("feed_url", ""))
    return f"""
  <script>
    (() => {{
      const FEED_URL = {feed_url};
      const topicMeta = {{
        weekly_digest: {{ name: "주간 다이제스트", description: "한 주의 경제·정치·컬처 흐름을 하나의 구조로 묶어 정리하는 주간 테제입니다." }},
        economy: {{ name: "경제·투자", description: "시장과 투자 흐름을 중심으로 발행되는 오전 데일리 테제입니다." }},
        economy_pm: {{ name: "경제·투자 오후", description: "오후 장 흐름과 추가 변수를 반영하는 경제·투자 테제입니다." }},
        politics: {{ name: "정치", description: "정치 이슈를 구조화해 당일 관찰 포인트로 정리하는 데일리 테제입니다." }},
        culture: {{ name: "컬처", description: "문화·콘텐츠 흐름을 읽고 의미 있는 신호를 정리하는 데일리 테제입니다." }},
      }};

      function escapeHtml(value) {{
        return String(value || "").replace(/[&<>"']/g, (ch) => ({{
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }}[ch]));
      }}

      function textOf(item, selector) {{
        return item.querySelector(selector)?.textContent?.trim() || "";
      }}

      function dateFromLink(link) {{
        return link.match(/(\\d{{4}}-\\d{{2}}-\\d{{2}})/)?.[1] || "";
      }}

      function dateFromItem(link, title, pubDate) {{
        const titleDate = String(title || "").match(/(\\d{{4}})[.](\\d{{2}})[.](\\d{{2}})/);
        if (titleDate) return `${{titleDate[1]}}-${{titleDate[2]}}-${{titleDate[3]}}`;
        const linkDate = dateFromLink(link);
        if (linkDate) return linkDate;
        const parsed = new Date(pubDate);
        if (!Number.isNaN(parsed.getTime())) {{
          return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
        }}
        return "";
      }}

      function topicKey(link, category) {{
        if (link.includes("/weekly-") || category.includes("주간") || category.includes("다이제스트")) return "weekly_digest";
        if (link.endsWith("-pm.html") || category.includes("오후")) return "economy_pm";
        if (link.endsWith("-politics.html") || category.includes("정치")) return "politics";
        if (link.endsWith("-culture.html") || category.includes("컬처")) return "culture";
        if (/\\d{{4}}-\\d{{2}}-\\d{{2}}[.]html$/.test(link) || category.includes("경제")) return "economy";
        return "";
      }}

      function splitDescription(description, fallbackTitle) {{
        const value = String(description || "").trim();
        const quoted = value.match(/^("[^"]+")\\s+—\\s+(.+)$/);
        if (quoted) return {{ title: quoted[1], quote: quoted[2] }};
        const parts = value.split(" — ");
        if (parts.length > 1) return {{ title: parts[0].trim(), quote: parts.slice(1).join(" — ").trim() }};
        return {{ title: fallbackTitle || "데일리 테제", quote: value }};
      }}

      function parseFeed(xmlText) {{
        const doc = new DOMParser().parseFromString(xmlText, "application/xml");
        return Array.from(doc.querySelectorAll("item")).map((item) => {{
          const link = textOf(item, "link");
          const category = textOf(item, "category");
          const description = textOf(item, "description");
          const title = textOf(item, "title");
          const pubDate = textOf(item, "pubDate");
          const key = topicKey(link, category);
          const split = splitDescription(description, title);
          const date = dateFromItem(link, title, pubDate);
          return {{
            key,
            name: topicMeta[key]?.name || category || "테제",
            description: topicMeta[key]?.description || "",
            date,
            title: split.title,
            quote: split.quote,
            url: link,
            publishedAt: Number.isNaN(new Date(pubDate).getTime()) ? 0 : new Date(pubDate).getTime(),
            status: date === new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10) ? "오늘 인용" : "최신 인용",
          }};
        }}).filter((item) => item.key && item.url);
      }}

      function cardHtml(item) {{
        return `
          <article class="thesis-card hoya-card">
            <div class="card-meta">
              <span>${{escapeHtml(item.date)}} · ${{escapeHtml(item.name)}}</span>
              <strong>${{escapeHtml(item.status)}}</strong>
            </div>
            <h3>${{escapeHtml(item.title)}}</h3>
            <p>${{escapeHtml(item.description)}}</p>
            <blockquote>${{escapeHtml(item.quote)}}</blockquote>
            <a class="text-link" href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">원문 열기</a>
          </article>
        `;
      }}

      function updateHero(item) {{
        const panel = document.getElementById("heroThesisPanel");
        if (!panel || !item) return;
        panel.innerHTML = `
          <span class="panel-label">${{escapeHtml(item.date)}} · ${{escapeHtml(item.name)}} · ${{escapeHtml(item.status)}}</span>
          <h2>${{escapeHtml(item.title)}}</h2>
          <p>${{escapeHtml(item.quote)}}</p>
          <a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">원문 보기</a>
        `;
      }}

      async function syncDailyThesis() {{
        if (!FEED_URL) return;
        try {{
          const response = await fetch(FEED_URL, {{ cache: "no-store" }});
          if (!response.ok) return;
          const items = parseFeed(await response.text()).sort((a, b) =>
            b.publishedAt - a.publishedAt || b.date.localeCompare(a.date)
          );
          const latestByTopic = [];
          const seen = new Set();
          for (const item of items) {{
            if (seen.has(item.key)) continue;
            seen.add(item.key);
            latestByTopic.push(item);
            if (latestByTopic.length >= 5) break;
          }}
          if (!latestByTopic.length) return;
          const grid = document.getElementById("hoyaThesisGrid");
          if (grid) grid.innerHTML = latestByTopic.map(cardHtml).join("");
          const count = document.getElementById("hoyaThesisCount");
          if (count) count.textContent = String(latestByTopic.length);
          const latestEconomy = items.find((item) => item.key === "economy" || item.key === "economy_pm");
          updateHero(latestEconomy || items[0] || latestByTopic[0]);
        }} catch (error) {{
          console.warn("daily thesis live sync failed", error);
        }}
      }}

      window.addEventListener("DOMContentLoaded", syncDailyThesis);
    }})();
  </script>
    """


def local_editor_script() -> str:
    return """
  <script>
    (() => {
      const $ = (id) => document.getElementById(id);
      const fields = {
        file: $("localThesisFile"),
        title: $("localTitle"),
        date: $("localDate"),
        summary: $("localSummary"),
        tags: $("localTags"),
        status: $("localStatus"),
        body: $("localBody"),
      };
      const imageState = [
        { dataUrl: "", name: "" },
        { dataUrl: "", name: "" },
        { dataUrl: "", name: "" },
      ];
      const preview = {
        kicker: $("previewKicker"),
        title: $("previewTitle"),
        summary: $("previewSummary"),
        tags: $("previewTags"),
        body: $("previewBody"),
        filename: $("downloadName"),
      };
      const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      fields.date.value = today;

      function applyCommand() {
        const path = decodeURIComponent(window.location.pathname).replace(/[/]index[.]html$/, "");
        return `cd "${path}" && python3 로컬보조테제_반영.py`;
      }

      function slugify(value) {
        return (value || "로컬-보조-테제")
          .trim()
          .replace(/[\\s_]+/g, "-")
          .replace(/[\\\\/:*?"<>|#%{}^~[\\]`]+/g, "")
          .replace(/-+/g, "-")
          .slice(0, 60) || "로컬-보조-테제";
      }

      function escapeHtml(value) {
        return (value || "").replace(/[&<>"']/g, (ch) => ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[ch]));
      }

      function parseFrontMatter(text) {
        const match = text.match(/^---\\n([\\s\\S]*?)\\n---\\n?([\\s\\S]*)$/);
        if (!match) return { meta: {}, body: text };
        const meta = {};
        match[1].split("\\n").forEach((line) => {
          const idx = line.indexOf(":");
          if (idx === -1) return;
          const key = line.slice(0, idx).trim();
          const value = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
          meta[key] = value;
        });
        return { meta, body: match[2] };
      }

      function markdownToHtml(markdown) {
        return (markdown || "")
          .split(/\\n{2,}/)
          .map((block) => block.trim())
          .filter(Boolean)
          .map((block) => {
            const imageMatch = block.match(/^!\\[(.*?)\\]\\((.*?)\\)$/);
            if (imageMatch) {
              return `<figure class="article-image"><img src="${escapeHtml(imageMatch[2])}" alt="${escapeHtml(imageMatch[1])}"><figcaption>${escapeHtml(imageMatch[1])}</figcaption></figure>`;
            }
            if (block.startsWith("### ")) return `<h3>${escapeHtml(block.slice(4))}</h3>`;
            if (block.startsWith("## ")) return `<h2>${escapeHtml(block.slice(3))}</h2>`;
            if (block.startsWith("# ")) return `<h2>${escapeHtml(block.slice(2))}</h2>`;
            return `<p>${escapeHtml(block).replace(/\\n/g, "<br>")}</p>`;
          })
          .join("");
      }

      function buildMarkdown() {
        const title = fields.title.value.trim() || "로컬 보조 테제";
        const date = fields.date.value || today;
        const summary = fields.summary.value.trim();
        const tags = fields.tags.value.trim();
        const status = fields.status.value || "초안";
        const selected = Number(document.querySelector('input[name="selectedImage"]:checked')?.value || 0);
        const selectedImage = imageState[selected];
        const imagePrompt = $("imagePrompt" + selected).value.trim();
        const imageMeta = selectedImage.dataUrl ? `\\nimage_prompt: ${imagePrompt.replace(/\\n/g, " ")}` : "";
        const imageMarkdown = selectedImage.dataUrl ? `![${title} 대표 이미지](${selectedImage.dataUrl})\\n\\n` : "";
        const body = fields.body.value.trim();
        return `---\\ntitle: ${title}\\ndate: ${date}\\nsummary: ${summary}\\ntags: ${tags}\\nstatus: ${status}${imageMeta}\\n---\\n\\n${imageMarkdown}${body}\\n`;
      }

      function compactText(value, limit = 220) {
        return (value || "").replace(/\\s+/g, " ").trim().slice(0, limit);
      }

      function imagePromptSeeds() {
        const title = fields.title.value.trim() || "보조테제";
        const summary = fields.summary.value.trim() || compactText(fields.body.value, 120);
        const tags = fields.tags.value.trim();
        const base = `Subject: ${title}. Context: ${summary}. Keywords: ${tags}.`;
        return [
          `Create a sophisticated editorial news image for a Korean AI lab thesis. ${base} Visualize the core issue with realistic market/research atmosphere, premium lighting, no readable text, no logos, no people, 16:9 composition, blue #2367d7 accent.`,
          `Create a clean data-driven visual cut for an economic/research briefing. ${base} Use abstract charts, screens, financial signals, crisp depth, modern newsroom dashboard mood, no readable text, no logos, 16:9 composition.`,
          `Create a high-end abstract AI research lab image for a thesis archive. ${base} Use glass surfaces, data layers, subtle code traces, calm dark-to-light contrast, no readable text, no logos, 16:9 composition.`,
        ];
      }

      function wrapText(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
        const words = String(text || "").split(/\\s+/).filter(Boolean);
        let line = "";
        let lines = 0;
        for (let i = 0; i < words.length; i += 1) {
          const testLine = line ? `${line} ${words[i]}` : words[i];
          if (ctx.measureText(testLine).width > maxWidth && line) {
            ctx.fillText(line, x, y);
            y += lineHeight;
            lines += 1;
            line = words[i];
            if (lines >= maxLines - 1) break;
          } else {
            line = testLine;
          }
        }
        if (line && lines < maxLines) ctx.fillText(line, x, y);
      }

      function drawGrid(ctx, width, height, color) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        for (let x = 0; x < width; x += 56) {
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, height);
          ctx.stroke();
        }
        for (let y = 0; y < height; y += 56) {
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(width, y);
          ctx.stroke();
        }
      }

      function drawImageCut(index) {
        const canvas = document.createElement("canvas");
        canvas.width = 1280;
        canvas.height = 720;
        const ctx = canvas.getContext("2d");
        const title = fields.title.value.trim() || "로컬 보조 테제";
        const summary = fields.summary.value.trim() || compactText(fields.body.value, 150);
        const tags = fields.tags.value.trim() || "hoya lab";
        const themes = [
          ["#071225", "#2367d7", "#ffffff", "#8db8ff"],
          ["#f8fbff", "#2367d7", "#101827", "#167c5f"],
          ["#101827", "#e8f1ff", "#ffffff", "#f4b740"],
        ];
        const [bg, accent, text, sub] = themes[index];

        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        const gradient = ctx.createRadialGradient(960, 120, 40, 960, 120, 680);
        gradient.addColorStop(0, index === 1 ? "rgba(35,103,215,0.24)" : "rgba(35,103,215,0.56)");
        gradient.addColorStop(1, "rgba(35,103,215,0)");
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        if (index !== 1) drawGrid(ctx, canvas.width, canvas.height, "rgba(255,255,255,0.055)");
        if (index === 1) drawGrid(ctx, canvas.width, canvas.height, "rgba(35,103,215,0.08)");

        ctx.fillStyle = accent;
        ctx.fillRect(72, 78, 8, 88);
        ctx.beginPath();
        ctx.arc(1030, 170, 92, 0, Math.PI * 2);
        ctx.fillStyle = index === 2 ? "rgba(244,183,64,0.2)" : "rgba(35,103,215,0.22)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(1110, 252, 150, 0, Math.PI * 2);
        ctx.strokeStyle = index === 1 ? "rgba(35,103,215,0.28)" : "rgba(255,255,255,0.18)";
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.fillStyle = accent;
        ctx.font = "700 28px Apple SD Gothic Neo, Noto Sans KR, sans-serif";
        ctx.fillText("hoya lab · local thesis cut 0" + (index + 1), 104, 104);

        ctx.fillStyle = text;
        ctx.font = "800 64px Apple SD Gothic Neo, Noto Sans KR, sans-serif";
        wrapText(ctx, title, 104, 210, 720, 76, 3);

        ctx.fillStyle = sub;
        ctx.font = "400 30px Apple SD Gothic Neo, Noto Sans KR, sans-serif";
        wrapText(ctx, summary, 108, 470, 760, 42, 3);

        const tagText = tags.split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 3).join("  ·  ");
        ctx.fillStyle = index === 1 ? "#475569" : "rgba(255,255,255,0.72)";
        ctx.font = "700 24px Apple SD Gothic Neo, Noto Sans KR, sans-serif";
        ctx.fillText(tagText || "daily thesis · dashboard · note", 104, 638);

        if (index === 1) {
          const bars = [170, 260, 215, 330, 285];
          bars.forEach((bar, i) => {
            ctx.fillStyle = i % 2 ? "#167c5f" : "#2367d7";
            ctx.fillRect(910 + i * 52, 560 - bar, 32, bar);
          });
          ctx.strokeStyle = "#dbe3ef";
          ctx.beginPath();
          ctx.moveTo(880, 560);
          ctx.lineTo(1180, 560);
          ctx.stroke();
        } else {
          ctx.strokeStyle = index === 2 ? "rgba(244,183,64,0.66)" : "rgba(141,184,255,0.72)";
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.moveTo(890, 500);
          ctx.bezierCurveTo(960, 430, 1025, 560, 1100, 470);
          ctx.bezierCurveTo(1140, 424, 1160, 420, 1202, 392);
          ctx.stroke();
        }

        return canvas.toDataURL("image/jpeg", 0.88);
      }

      function applyGeneratedImage(index, dataUrl) {
        imageState[index] = { dataUrl, name: `hoya-lab-cut-${index + 1}.jpg` };
        $("imagePreview" + index).src = dataUrl;
        $("imagePreview" + index).closest(".image-option").classList.add("has-image");
      }

      function selectedImageMarkdownPreview() {
        const selected = Number(document.querySelector('input[name="selectedImage"]:checked')?.value || 0);
        const selectedImage = imageState[selected];
        if (!selectedImage.dataUrl) return "";
        const title = fields.title.value.trim() || "로컬 보조 테제";
        return `![${title} 대표 이미지](${selectedImage.dataUrl})\\n\\n`;
      }

      function filename() {
        return `${fields.date.value || today}-${slugify(fields.title.value)}.md`;
      }

      function updatePreview() {
        const title = fields.title.value.trim() || "로컬 보조 테제 편집";
        const summary = fields.summary.value.trim() || "파일을 올리거나 내용을 붙여넣으면 이곳에 미리보기가 표시됩니다.";
        const tags = fields.tags.value.split(",").map((tag) => tag.trim()).filter(Boolean);
        preview.kicker.textContent = `${fields.date.value || today} · ${fields.status.value || "초안"}`;
        preview.title.textContent = title;
        preview.summary.textContent = summary;
        preview.tags.innerHTML = tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
        preview.body.innerHTML = markdownToHtml(selectedImageMarkdownPreview() + fields.body.value);
        preview.filename.textContent = filename();
      }

      function loadMarkdown(text) {
        const parsed = parseFrontMatter(text);
        fields.title.value = parsed.meta.title || fields.title.value;
        fields.date.value = parsed.meta.date || fields.date.value || today;
        fields.summary.value = parsed.meta.summary || fields.summary.value;
        fields.tags.value = parsed.meta.tags || fields.tags.value;
        fields.status.value = parsed.meta.status || fields.status.value || "초안";
        fields.body.value = parsed.body.trim();
        updatePreview();
      }

      fields.file.addEventListener("change", async (event) => {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        loadMarkdown(await file.text());
      });

      Object.values(fields).forEach((field) => {
        if (field && field !== fields.file) field.addEventListener("input", updatePreview);
      });

      $("generateImagePrompts").addEventListener("click", () => {
        $("imageStatus").textContent = "이미지컷을 생성하는 중입니다...";
        try {
          imagePromptSeeds().forEach((prompt, index) => {
            $("imagePrompt" + index).value = prompt;
            applyGeneratedImage(index, drawImageCut(index));
          });
          document.querySelector('input[name="selectedImage"][value="0"]').checked = true;
          $("imageStatus").textContent = "이미지컷 3개가 생성됐습니다. 마음에 드는 컷을 선택하세요.";
          updatePreview();
        } catch (error) {
          $("imageStatus").textContent = "이미지컷 생성 중 오류가 발생했습니다. 브라우저를 새로고침한 뒤 다시 시도하세요.";
        }
      });

      [0, 1, 2].forEach((index) => {
        $("imageFile" + index).addEventListener("change", async (event) => {
          const file = event.target.files && event.target.files[0];
          if (!file) return;
          const reader = new FileReader();
          reader.addEventListener("load", () => {
            imageState[index] = { dataUrl: String(reader.result || ""), name: file.name };
            $("imagePreview" + index).src = imageState[index].dataUrl;
            $("imagePreview" + index).closest(".image-option").classList.add("has-image");
            document.querySelector(`input[name="selectedImage"][value="${index}"]`).checked = true;
            updatePreview();
          });
          reader.readAsDataURL(file);
        });
        document.querySelector(`input[name="selectedImage"][value="${index}"]`).addEventListener("change", updatePreview);
      });

      $("downloadLocalThesis").addEventListener("click", () => {
        const blob = new Blob([buildMarkdown()], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename();
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      });

      $("copyLocalThesis").addEventListener("click", async () => {
        const markdown = buildMarkdown();
        try {
          await navigator.clipboard.writeText(markdown);
        } catch (error) {
          const helper = document.createElement("textarea");
          helper.value = markdown;
          helper.setAttribute("readonly", "");
          helper.style.position = "fixed";
          helper.style.left = "-9999px";
          document.body.appendChild(helper);
          helper.select();
          document.execCommand("copy");
          helper.remove();
        }
      });

      $("copyApplyCommand").addEventListener("click", async () => {
        const command = applyCommand();
        try {
          await navigator.clipboard.writeText(command);
        } catch (error) {
          const helper = document.createElement("textarea");
          helper.value = command;
          helper.setAttribute("readonly", "");
          helper.style.position = "fixed";
          helper.style.left = "-9999px";
          document.body.appendChild(helper);
          helper.select();
          document.execCommand("copy");
          helper.remove();
        }
      });

      $("applyCommandText").textContent = applyCommand();
      $("clearLocalThesis").addEventListener("click", () => {
        fields.title.value = "";
        fields.date.value = today;
        fields.summary.value = "";
        fields.tags.value = "";
        fields.status.value = "초안";
        fields.body.value = "";
        fields.file.value = "";
        updatePreview();
      });

      updatePreview();
    })();
  </script>
    """


def render() -> str:
    theses = load_theses()
    dashboards = load_dashboards()
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    hoya_theses = load_hoya_theses(today)
    economy_theses = [item for item in hoya_theses if item.key in {"economy", "economy_pm"}]
    latest = max(economy_theses, key=lambda item: (item.published_ts, item.date)) if economy_theses else None
    if latest is None and hoya_theses:
        latest = max(hoya_theses, key=lambda item: (item.published_ts, item.date))
    latest_title = latest.title if latest else "Hoya Bot 테제 연결 대기"
    latest_summary = latest.quote if latest else "Hoya Bot 데일리 테제 발행처를 확인하고 있습니다."

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>hoya lab</title>
  <meta name="description" content="hoya lab은 daily thesis와 코딩 대시보드를 연결하는 개인 AI 연구실입니다. 자동 발행되는 테제, 분석 도구, 로컬 보조테제를 한곳에서 운영합니다.">
  <meta property="og:type" content="website">
  <meta property="og:title" content="hoya lab">
  <meta property="og:description" content="daily thesis와 코딩 대시보드를 연결하는 개인 AI 연구실">
  <meta property="og:image" content="assets/hoya-lab-hero.png">
  <style>
    :root {{
      --blue: #2367d7;
      --ink: #111827;
      --muted: #64748b;
      --line: #dbe3ef;
      --paper: #f7f9fc;
      --surface: #ffffff;
      --green: #167c5f;
      --rose: #b83b5e;
      --amber: #a65f00;
      --shadow: 0 18px 55px rgba(15, 23, 42, 0.12);
      font-family: "세계하나본문명조", "Apple SD Gothic Neo", "Noto Serif KR", serif;
    }}

    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 8% 12%, rgba(35, 103, 215, 0.08), transparent 28%),
        linear-gradient(180deg, #f9fbff 0%, var(--paper) 44%, #eef3f9 100%);
      line-height: 1.65;
    }}

    a {{ color: inherit; }}
    img {{ display: block; max-width: 100%; }}

    .site-header {{
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid rgba(219, 227, 239, 0.92);
      background: rgba(247, 249, 252, 0.92);
      backdrop-filter: blur(16px);
    }}

    .nav {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
      letter-spacing: 0;
    }}

    .brand-mark {{
      width: 30px;
      height: 30px;
      background: var(--blue);
      color: #fff;
      display: grid;
      place-items: center;
      font-size: 15px;
      border-radius: 6px;
    }}

    .nav-links {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 14px;
    }}

    .nav-links a {{
      text-decoration: none;
      padding: 8px 10px;
      border-radius: 6px;
      color: #334155;
      white-space: nowrap;
    }}

    .nav-links a:hover {{ background: #e9f0fb; color: var(--blue); }}

    main {{ overflow: hidden; }}

    .hero {{
      min-height: calc(100svh - 64px);
      display: grid;
      align-items: end;
      position: relative;
      color: #fff;
      background:
        linear-gradient(90deg, rgba(3, 9, 26, 0.9), rgba(3, 9, 26, 0.64) 43%, rgba(3, 9, 26, 0.18)),
        linear-gradient(180deg, rgba(3, 9, 26, 0.06), rgba(3, 9, 26, 0.24)),
        url("assets/hoya-lab-hero.png") center / cover no-repeat;
      isolation: isolate;
    }}

    .hero::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(rgba(255, 255, 255, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: linear-gradient(90deg, rgba(0, 0, 0, 0.82), transparent 72%);
      pointer-events: none;
    }}

    .hero::after {{
      content: "";
      position: absolute;
      inset: auto 0 0;
      height: 30%;
      background: linear-gradient(0deg, #f9fbff, rgba(249, 251, 255, 0));
      pointer-events: none;
    }}

    .hero-inner {{
      position: relative;
      z-index: 1;
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 72px 0 90px;
    }}

    .hero-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 430px);
      gap: 42px;
      align-items: end;
    }}

    .eyebrow {{
      margin: 0 0 16px;
      color: #b9d3ff;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 14px;
      font-weight: 700;
    }}

    h1, h2, h3 {{
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      line-height: 1.18;
      letter-spacing: 0;
    }}

    h1 {{
      max-width: 760px;
      margin: 0;
      font-size: clamp(44px, 7vw, 84px);
      text-shadow: 0 22px 54px rgba(0, 0, 0, 0.28);
    }}

    .hero-copy {{
      max-width: 650px;
      margin: 22px 0 0;
      color: #e7eefc;
      font-size: 18px;
      text-shadow: 0 12px 34px rgba(0, 0, 0, 0.3);
    }}

    .hero-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 30px;
    }}

    .button {{
      min-height: 44px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 10px 16px;
      border: 1px solid rgba(255, 255, 255, 0.46);
      border-radius: 6px;
      color: #fff;
      text-decoration: none;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 700;
    }}

    .button.primary {{ background: var(--blue); border-color: var(--blue); }}
    .button:hover {{ transform: translateY(-1px); }}

    .hero-panel {{
      padding: 24px;
      border: 1px solid rgba(255, 255, 255, 0.24);
      border-radius: 8px;
      background: rgba(4, 12, 32, 0.58);
      box-shadow: 0 26px 70px rgba(0, 0, 0, 0.34);
      backdrop-filter: blur(18px);
    }}

    .panel-label {{
      display: block;
      color: #9ec1ff;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0;
      margin-bottom: 12px;
    }}

    .hero-panel h2 {{
      margin: 0;
      color: #fff;
      font-size: clamp(24px, 3vw, 34px);
    }}

    .hero-panel p {{
      margin: 14px 0 0;
      color: #dbe8ff;
      font-size: 15px;
    }}

    .hero-panel a {{
      display: inline-flex;
      margin-top: 18px;
      color: #ffffff;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
      text-decoration: none;
      border-bottom: 2px solid #8db8ff;
    }}

    .section {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 76px 0;
    }}

    .section-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(240px, 420px);
      gap: 28px;
      align-items: end;
      margin-bottom: 28px;
    }}

    .section h2 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 46px);
    }}

    .section-lead {{
      margin: 0;
      color: var(--muted);
    }}

    .stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
      backdrop-filter: blur(18px);
    }}

    .stat {{
      padding: 22px;
      border-right: 1px solid var(--line);
    }}

    .stat:last-child {{ border-right: 0; }}
    .stat strong {{
      display: block;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 28px;
      color: var(--blue);
    }}
    .stat span {{ color: var(--muted); font-size: 14px; }}

    .system-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-top: 22px;
    }}

    .system-item {{
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.78);
    }}

    .system-item strong {{
      display: block;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 18px;
      margin-bottom: 8px;
    }}

    .system-item span {{
      color: #475569;
      font-size: 14px;
    }}

    .dashboard-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
    }}

    .dashboard-card,
    .thesis-card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 0 rgba(15, 23, 42, 0.02);
    }}

    .dashboard-card:hover,
    .thesis-card:hover {{ box-shadow: var(--shadow); }}

    .dashboard-image {{
      height: 190px;
      background: #dce6f3;
      display: block;
      overflow: hidden;
    }}

    .dashboard-image img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.25s ease;
    }}

    .dashboard-card:hover img {{ transform: scale(1.03); }}

    .dashboard-body,
    .thesis-card {{
      padding: 20px;
    }}

    .card-meta {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 13px;
    }}

    .card-meta strong {{
      color: var(--green);
      white-space: nowrap;
    }}

    .dashboard-card h3,
    .thesis-card h3 {{
      margin: 12px 0 8px;
      font-size: 22px;
    }}

    .dashboard-card p,
    .thesis-card p {{
      margin: 0;
      color: #475569;
      font-size: 15px;
    }}

    .hoya-card blockquote {{
      margin: 18px 0 0;
      padding: 14px 16px;
      border-left: 4px solid var(--blue);
      background: #f3f7ff;
      color: #1e3a6f;
      font-size: 15px;
    }}

    .tech-stack,
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 16px;
    }}

    .tech-stack span,
    .tag-row span {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      color: #334155;
      background: #f8fafc;
      font-size: 12px;
    }}

    .text-link {{
      display: inline-flex;
      margin-top: 18px;
      color: var(--blue);
      text-decoration: none;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
    }}

    .thesis-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}

    .workflow {{
      background: #101827;
      color: #eef4ff;
    }}

    .workflow .section-lead {{ color: #b8c6da; }}
    .flow {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 28px;
    }}

    .flow-step {{
      min-height: 150px;
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 8px;
      padding: 18px;
      background: rgba(255, 255, 255, 0.05);
    }}

    .flow-step strong {{
      display: block;
      color: #8db8ff;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 24px;
    }}

    .flow-step span {{
      display: block;
      margin-top: 8px;
      color: #dbeafe;
      font-size: 14px;
    }}

    .article-list {{
      display: grid;
      gap: 24px;
    }}

    .source-note {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 20px;
      margin-bottom: 22px;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: #475569;
    }}

    .source-note strong {{
      color: var(--ink);
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      white-space: nowrap;
    }}

    .source-note a {{
      color: var(--blue);
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
      text-decoration: none;
      white-space: nowrap;
    }}

    .article {{
      padding: 32px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}

    .featured-article {{
      border-color: rgba(35, 103, 215, 0.36);
      box-shadow: 0 18px 55px rgba(35, 103, 215, 0.12);
    }}

    .collapsed-article {{
      padding: 0;
      overflow: hidden;
    }}

    .collapsed-article summary {{
      display: grid;
      grid-template-columns: minmax(160px, 0.6fr) minmax(180px, 0.9fr) minmax(240px, 1.3fr);
      gap: 16px;
      align-items: center;
      padding: 22px 26px;
      cursor: pointer;
      list-style: none;
    }}

    .collapsed-article summary::-webkit-details-marker {{
      display: none;
    }}

    .collapsed-article summary::after {{
      content: "펼치기";
      justify-self: end;
      color: var(--blue);
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 13px;
      font-weight: 800;
    }}

    .collapsed-article[open] summary::after {{
      content: "접기";
    }}

    .collapsed-article summary strong {{
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 20px;
    }}

    .collapsed-article summary span:last-child {{
      color: #475569;
    }}

    .collapsed-article .tag-row,
    .collapsed-article .article-body {{
      margin-left: 26px;
      margin-right: 26px;
    }}

    .collapsed-article .article-body {{
      margin-bottom: 26px;
      padding-top: 8px;
      border-top: 1px solid var(--line);
    }}

    .article-kicker {{
      color: var(--blue);
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-size: 14px;
      font-weight: 800;
    }}

    .article h2 {{
      margin: 10px 0 10px;
      font-size: clamp(26px, 4vw, 38px);
    }}

    .article-summary {{
      margin: 0;
      color: #475569;
      font-size: 17px;
    }}

    .article-body {{
      margin-top: 24px;
      color: #1f2937;
      font-size: 18px;
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .article-body code {{
      padding: 2px 5px;
      border-radius: 4px;
      background: #eef4ff;
      color: #153d80;
    }}

    .article-image {{
      margin: 24px 0;
    }}

    .article-image img {{
      width: 100%;
      max-height: 520px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--line);
    }}

    .article-image figcaption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }}

    .local-editor {{
      margin-bottom: 28px;
      padding: 24px;
      background: rgba(255, 255, 255, 0.9);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 14px 48px rgba(15, 23, 42, 0.08);
    }}

    .editor-toolbar {{
      display: flex;
      align-items: stretch;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}

    .file-drop {{
      flex: 1;
      display: grid;
      gap: 4px;
      padding: 18px;
      border: 1px dashed #9cb7e8;
      border-radius: 8px;
      background: #f5f8ff;
      cursor: pointer;
    }}

    .file-drop strong,
    .editor-fields label,
    .save-note strong {{
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
    }}

    .file-drop span {{
      color: var(--muted);
      font-size: 14px;
    }}

    .file-drop input {{
      margin-top: 8px;
      color: #334155;
    }}

    .editor-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-content: center;
      justify-content: flex-end;
    }}

    .editor-button {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 13px;
      background: #fff;
      color: #1f2937;
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
      cursor: pointer;
    }}

    .editor-button.primary {{
      background: var(--blue);
      border-color: var(--blue);
      color: #fff;
    }}

    .editor-grid {{
      display: grid;
      grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
      gap: 18px;
    }}

    .editor-fields {{
      display: grid;
      gap: 12px;
    }}

    .editor-fields label {{
      display: grid;
      gap: 6px;
      color: #1f2937;
      font-size: 14px;
    }}

    .editor-fields input,
    .editor-fields textarea,
    .editor-fields select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }}

    .editor-fields textarea {{
      resize: vertical;
    }}

    .editor-preview {{
      min-height: 520px;
      padding: 26px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
    }}

    .editor-preview h3 {{
      margin: 10px 0;
      font-size: 28px;
    }}

    .save-note {{
      display: grid;
      gap: 6px;
      margin-top: 24px;
      padding: 14px;
      border-radius: 8px;
      background: #eef4ff;
      color: #334155;
      font-size: 14px;
    }}

    .next-step {{
      display: grid;
      gap: 8px;
      margin-top: 14px;
      padding: 14px;
      border: 1px solid #b8ccef;
      border-radius: 8px;
      background: #f8fbff;
      color: #334155;
      font-size: 14px;
    }}

    .next-step strong {{
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
    }}

    .next-step code {{
      display: block;
      overflow-x: auto;
      padding: 10px;
      border-radius: 6px;
      background: #111827;
      color: #f8fafc;
      white-space: nowrap;
    }}

    .image-assistant {{
      display: grid;
      gap: 14px;
      padding: 16px;
      border: 1px solid #b8ccef;
      border-radius: 8px;
      background: #f8fbff;
    }}

    .image-assistant-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}

    .image-assistant-head div {{
      display: grid;
      gap: 4px;
    }}

    .image-assistant-head strong,
    .image-option span {{
      font-family: "세계하나제목고딕", "Apple SD Gothic Neo", sans-serif;
      font-weight: 800;
    }}

    .image-assistant-head span {{
      color: var(--muted);
      font-size: 14px;
    }}

    .image-options {{
      display: grid;
      gap: 10px;
    }}

    .image-status {{
      padding: 10px 12px;
      border-radius: 6px;
      background: #eef4ff;
      color: #1e3a6f;
      font-size: 14px;
    }}

    .image-option {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px 10px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}

    .image-option textarea,
    .image-option input[type="file"],
    .image-option img {{
      grid-column: 1 / -1;
    }}

    .image-option img {{
      display: none;
      width: 100%;
      max-height: 220px;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--line);
    }}

    .image-option.has-image img {{
      display: block;
    }}

    .site-footer {{
      border-top: 1px solid var(--line);
      padding: 32px 0;
      color: var(--muted);
      background: #fff;
    }}

    .footer-inner {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      gap: 18px;
      font-size: 14px;
    }}

    @media (max-width: 860px) {{
      .nav {{
        align-items: flex-start;
        padding: 12px 0;
        flex-direction: column;
      }}
      .nav-links {{
        width: 100%;
        overflow-x: auto;
        padding-bottom: 2px;
      }}
      .hero {{
        min-height: 760px;
      }}
      .section-head,
      .hero-layout,
      .dashboard-grid,
      .thesis-grid,
      .system-strip,
      .flow {{
        grid-template-columns: 1fr;
      }}
      .stats {{
        grid-template-columns: 1fr;
      }}
      .stat {{
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .stat:last-child {{ border-bottom: 0; }}
      .footer-inner {{
        flex-direction: column;
      }}
      .source-note {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .editor-toolbar,
      .editor-grid {{
        grid-template-columns: 1fr;
        flex-direction: column;
      }}
      .editor-actions {{
        justify-content: flex-start;
      }}
      .collapsed-article summary {{
        grid-template-columns: 1fr;
      }}
      .collapsed-article summary::after {{
        justify-self: start;
      }}
    }}
  </style>
</head>
<body>
  <header class="site-header">
    <nav class="nav" aria-label="주요 메뉴">
      <a class="brand" href="#">
        <span class="brand-mark">H</span>
        <span>hoya lab</span>
      </a>
      <div class="nav-links">
        <a href="#dashboards">대시보드</a>
        <a href="#theses">테제</a>
        <a href="#workflow">자동 발행</a>
        <a href="#local-editor">보조테제 편집</a>
        <a href="#articles">본문</a>
      </div>
    </nav>
  </header>

  <main>
    <section class="hero">
      <div class="hero-inner">
        <div class="hero-layout">
          <div>
            <p class="eyebrow">CODING DASHBOARD · DAILY THESIS · PUBLISHING SYSTEM</p>
            <h1>hoya lab</h1>
            <p class="hero-copy">매일 발행되는 thesis와 직접 만든 코딩 대시보드를 연결하는 개인 AI 연구실입니다. 자동 발행, 해설, 실험 도구를 한 화면에서 이어 봅니다.</p>
            <div class="hero-actions">
              <a class="button primary" href="#theses">오늘의 테제</a>
              <a class="button" href="#dashboards">대시보드 보기</a>
              <a class="button" href="#local-editor">보조테제 작성</a>
            </div>
          </div>
          {hero_thesis_panel(latest)}
        </div>
      </div>
    </section>

    <section class="section" aria-label="요약">
      <div class="section-head">
        <div>
          <p class="eyebrow">OPERATING SYSTEM</p>
          <h2>테제와 도구가 같이 자라는 작업실</h2>
        </div>
        <p class="section-lead">hoya lab은 자동 발행되는 daily thesis를 받아오고, 대시보드로 실험하며, 로컬 보조테제로 내 해설과 후속 관찰을 쌓는 구조입니다.</p>
      </div>
      <div class="stats">
        <div class="stat"><strong>{len(dashboards)}</strong><span>등록된 코딩 대시보드</span></div>
        <div class="stat"><strong id="hoyaThesisCount">{len(hoya_theses)}</strong><span>최신 연결된 Hoya Bot 테제</span></div>
        <div class="stat"><strong>{today}</strong><span>마지막 생성일</span></div>
      </div>
      <div class="system-strip">
        <div class="system-item"><strong>Daily Thesis</strong><span>RSS를 통해 최신 경제·오후·정치·컬처·주간 다이제스트를 자동으로 불러옵니다.</span></div>
        <div class="system-item"><strong>Dashboards</strong><span>분석 도구와 실험 화면을 한곳에 모아 실제 작업 흐름을 보여줍니다.</span></div>
        <div class="system-item"><strong>Local Notes</strong><span>공식 발행본에 대한 해설, 반론, 후속 아이디어를 보조테제로 축적합니다.</span></div>
      </div>
    </section>

    <section class="section" id="dashboards">
      <div class="section-head">
        <div>
          <p class="eyebrow">DASHBOARDS</p>
          <h2>작동하는 생각의 도구들</h2>
        </div>
        <p class="section-lead">각 대시보드는 하나의 문제의식을 실행 가능한 화면으로 바꾼 결과물입니다. 기사, 분석, 의사결정, 자동화 실험을 계속 갱신합니다.</p>
      </div>
      <div class="dashboard-grid">
        {dashboard_cards(dashboards)}
      </div>
    </section>

    <section class="section" id="theses">
      <div class="section-head">
        <div>
          <p class="eyebrow">THESES</p>
          <h2>Hoya Bot 테제 인용</h2>
        </div>
        <p class="section-lead">홈페이지의 테제 영역은 Hoya Bot이 발행하는 오늘자 데일리 테제를 원천으로 인용합니다. 빌드 시 제목과 핵심 문장을 가져오고, 원문 링크를 함께 둡니다.</p>
      </div>
      {source_note()}
      <div class="thesis-grid" id="hoyaThesisGrid">
        {hoya_thesis_cards(hoya_theses)}
      </div>
    </section>

    <section class="workflow" id="workflow">
      <div class="section">
        <div class="section-head">
          <div>
            <p class="eyebrow">AUTO PUBLISH</p>
          <h2>daily thesis 자동 연동 흐름</h2>
        </div>
          <p class="section-lead">daily thesis가 발행되면 RSS를 읽어 최신 테제를 반영합니다. 정기 실행과 발행 직후 트리거를 함께 두어 업데이트 누락을 줄입니다.</p>
        </div>
        <div class="flow">
          <div class="flow-step"><strong>01</strong><span>Hoya Bot에서 발행 트리거</span></div>
          <div class="flow-step"><strong>02</strong><span>daily-thesis RSS 갱신</span></div>
          <div class="flow-step"><strong>03</strong><span>생성 스크립트가 최신 feed 확인</span></div>
          <div class="flow-step"><strong>04</strong><span>오늘 인용 카드와 링크 갱신</span></div>
          <div class="flow-step"><strong>05</strong><span>GitHub Pages·Vercel 배포</span></div>
        </div>
      </div>
    </section>

    <section class="section" id="articles">
      <div class="section-head">
        <div>
          <p class="eyebrow">ARCHIVE</p>
          <h2>로컬 보조 테제</h2>
        </div>
        <p class="section-lead">필요하면 Hoya Bot 원문과 별도로 직접 작성한 Markdown 테제를 이곳에 보조 아카이브로 쌓을 수 있습니다.</p>
      </div>
      {local_editor_panel()}
      <div class="article-list">
        {thesis_articles(theses) if theses else '<article class="article"><div class="article-kicker">LOCAL THESIS</div><h2>아직 로컬 보조 테제가 없습니다</h2><p class="article-summary">현재 홈페이지의 주 테제 원천은 Hoya Bot 데일리 테제입니다.</p></article>'}
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="footer-inner">
      <span>hoya lab · 개인 연구실형 홈페이지</span>
      <span>Generated from dashboards and theses</span>
    </div>
  </footer>
{hoya_live_sync_script()}
{local_editor_script()}
</body>
</html>
"""


def main() -> None:
    OUTPUT_FILE.write_text(render(), encoding="utf-8")
    print(f"생성 완료: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
