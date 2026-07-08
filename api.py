import base64
import json
import re
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from scrapling import Selector

app = FastAPI(
    title="DesiDubAnime Scraping API",
    description="A scraping-based API to interact with DesiDubAnime data, genres, searches, A-Z lists, anime details, episodes, and decoded iframe server URLs.",
    version="1.0.0"
)

# Enable CORS for easy integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://www.desidubanime.me/"
AJAX_URL = "https://www.desidubanime.me/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}

# Helper to decode base64 embeds
def decode_base64_embed(embed_id: str):
    if not embed_id or ":" not in embed_id:
        return None
    try:
        name_b64, url_b64 = embed_id.split(":")
        # Add padding if needed
        name_b64 += '=' * (4 - (len(name_b64) % 4)) if len(name_b64) % 4 else ""
        url_b64 += '=' * (4 - (len(url_b64) % 4)) if len(url_b64) % 4 else ""
        
        name = base64.b64decode(name_b64).decode('utf-8', errors='ignore').strip()
        url = base64.b64decode(url_b64).decode('utf-8', errors='ignore').strip()
        return {"name": name, "url": url}
    except Exception:
        return None

# Helper to resolve IQSmartGames helper mirrors
async def fetch_embedhelper_sources(client: httpx.AsyncClient, sid: str) -> List[dict]:
    payload = {
        "sid": sid,
        "UserFavSite": "",
        "currentDomain": BASE_URL
    }
    helper_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Referer": "https://pro.iqsmartgames.com/",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        r = await client.post("https://pro.iqsmartgames.com/embedhelper.php", data=payload, headers=helper_headers, timeout=10)
        if r.status_code == 200:
            resp = r.json()
            mresult_b64 = resp.get("mresult", "")
            mresult_b64 += '=' * (4 - (len(mresult_b64) % 4)) if len(mresult_b64) % 4 else ""
            
            decoded_mresult = json.loads(base64.b64decode(mresult_b64).decode('utf-8'))
            site_urls = resp.get("siteUrls", {})
            friendly_names = resp.get("siteFriendlyNames", {})
            
            servers = []
            for key, code in decoded_mresult.items():
                base_url = site_urls.get(key)
                name = friendly_names.get(key, key)
                if base_url:
                    servers.append({"name": name, "url": f"{base_url}{code}"})
            return servers
    except Exception:
        pass
    return []

# Pydantic models for Advanced Search input
class AdvancedSearchPayload(BaseModel):
    keyword: Optional[str] = ""
    genres: Optional[List[str]] = []
    producers: Optional[List[str]] = []
    seasons: Optional[List[str]] = []
    years: Optional[List[str]] = []
    orderby: Optional[str] = "date"
    order: Optional[str] = "DESC"
    page: Optional[int] = 1

from fastapi.responses import HTMLResponse
import os

@app.get("/")
def read_root():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard file not found</h1>", status_code=404)

# Helper to parse sidebar lists (Top Airing, Most Popular, Completed)
def parse_sidebar_list(sel_section):
    items = []
    if not sel_section:
        return items
    for li in sel_section.css("ul li"):
        title_spans = li.css("h3 a span::text").getall()
        title_jp = title_spans[0].strip() if len(title_spans) > 0 else ""
        title_en = title_spans[1].strip() if len(title_spans) > 1 else ""
        if not title_jp:
            title_jp = li.css("h3 a::text").get("").strip()
            
        link = li.css("h3 a::attr(href)").get("")
        poster = li.css("img::attr(src)").get("") or li.css("img::attr(data-src)").get("")
        
        slug_match = re.search(r'/anime/([^/]+)/', link)
        slug = slug_match.group(1) if slug_match else ""
        
        items.append({
            "title_en": title_en or title_jp,
            "title_jp": title_jp,
            "slug": slug,
            "link": link,
            "poster": poster
        })
    return items

# Helper to parse standard anime card grids
def parse_card_list(cards):
    items = []
    for card in cards:
        title_spans = card.css("h3 a span::text").getall()
        title_jp = title_spans[0].strip() if len(title_spans) > 0 else ""
        title_en = title_spans[1].strip() if len(title_spans) > 1 else ""
        if not title_jp:
            title_jp = card.css("img::attr(alt)").get("").strip()
        
        poster = card.css("img::attr(data-src)").get("") or card.css("img::attr(src)").get("")
        link = card.css("h3 a.stretched-link::attr(href)").get("") or card.css("a.stretched-link::attr(href)").get("")
        episode_num = card.css(".line-clamp-1::text").get("").strip()
        
        # Try to extract the real anime detail URL from the Info button
        info_btn = card.css("button[onclick*='/anime/']::attr(onclick)").get("")
        detail_url = ""
        if info_btn:
            detail_match = re.search(r"window\.location\.href='([^']+)'", info_btn)
            if detail_match:
                detail_url = detail_match.group(1)
        
        if detail_url:
            slug_match = re.search(r'/anime/([^/]+)/', detail_url)
            slug = slug_match.group(1) if slug_match else ""
        else:
            slug_match = re.search(r'/(watch|anime)/([^/]+)/', link)
            slug = slug_match.group(2) if slug_match else ""
            slug = re.sub(r'-episode-\d+', '', slug)
            slug = re.sub(r'-movie', '', slug)
        
        items.append({
            "title_en": title_en or title_jp,
            "title_jp": title_jp,
            "slug": slug,
            "link": link,
            "poster": poster,
            "episode": episode_num
        })
    return items

# Helper to parse Popular Post list items
def parse_popular_list(ul_element):
    items = []
    if not ul_element:
        return items
    for li in ul_element.css("li"):
        rank_text = li.css("div.font-bold::text").get("").strip() or li.css("div.text-4xl::text").get("").strip()
        
        title_spans = li.css("h3 span::text").getall()
        title_jp = title_spans[0].strip() if len(title_spans) > 0 else ""
        title_en = title_spans[1].strip() if len(title_spans) > 1 else ""
        if not title_jp:
            title_jp = li.attrib.get("aria-label", "").strip()
            
        link = li.css("a::attr(href)").get("")
        poster = li.css("img::attr(src)").get("") or li.css("img::attr(data-src)").get("")
        
        slug_match = re.search(r'/anime/([^/]+)/', link)
        slug = slug_match.group(1) if slug_match else ""
        
        items.append({
            "rank": rank_text,
            "title_en": title_en or title_jp,
            "title_jp": title_jp,
            "slug": slug,
            "link": link,
            "poster": poster
        })
    return items

@app.get("/api/home")
async def get_home():
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(BASE_URL)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to fetch homepage")
            
            sel = Selector(r.text)
            
            # 1. Genres
            genres = []
            for a in sel.css("a[href*='/genre/']"):
                name = a.css("::text").get("").strip()
                url = a.attrib.get("href", "")
                slug_match = re.search(r'/genre/([^/]+)/', url)
                slug = slug_match.group(1) if slug_match else ""
                if name and slug:
                    genres.append({"name": name, "slug": slug})
            
            seen_genres = set()
            genres_unique = []
            for g in genres:
                if g["slug"] not in seen_genres:
                    seen_genres.add(g["slug"])
                    genres_unique.append(g)
            
            # 2. Spotlight Slides
            spotlights = []
            for slide in sel.css("div.swiper-slide"):
                subtitle = slide.css(".text-accent::text").get("")
                if "Spotlight" in subtitle:
                    spans = slide.css("h2 span::text").getall()
                    title_jp = spans[0].strip() if len(spans) > 0 else ""
                    title_en = spans[1].strip() if len(spans) > 1 else ""
                    if not title_jp and not title_en:
                        title_jp = slide.css("h2::text").get("").strip()
                    
                    link = slide.css("a::attr(href)").get("")
                    banner = slide.css("img::attr(data-src)").get("") or slide.css("img::attr(src)").get("")
                    slug_match = re.search(r'/anime/([^/]+)/', link)
                    slug = slug_match.group(1) if slug_match else ""
                    
                    spotlights.append({
                        "title_en": title_en or title_jp,
                        "title_jp": title_jp,
                        "slug": slug,
                        "link": link,
                        "banner": banner
                    })

            # 3. Sidebar Lists
            top_airing = []
            most_popular = []
            completed_series = []
            
            for section in sel.css("section"):
                h2_text = section.css("h2::text").get("").strip() or section.css("h2 span::text").get("").strip()
                if "Top Airing!" in h2_text:
                    top_airing = parse_sidebar_list(section)
                elif "Most Popular" in h2_text:
                    most_popular = parse_sidebar_list(section)
                elif "Completed Series" in h2_text:
                    completed_series = parse_sidebar_list(section)

            # 4. Grid Lists (Latest Episodes, Movies, Upcoming)
            latest_episodes = []
            latest_movies = []
            upcoming = []
            
            for section in sel.css("section"):
                h2_text = section.css("h2::text").get("").strip() or section.css("h2 span::text").get("").strip()
                if "Latest Episode" in h2_text:
                    latest_episodes = parse_card_list(section.css(".anime-card"))
                elif "Latest Movies" in h2_text:
                    latest_movies = parse_card_list(section.css(".anime-card"))
                elif "Upcoming" in h2_text:
                    upcoming = parse_card_list(section.css(".anime-card"))

            # 5. Popular Posts (Day, Week, Month tabs)
            popular_today = []
            popular_weekly = []
            popular_monthly = []
            
            for ul in sel.css("ul"):
                ul_class = ul.attrib.get("class", "")
                if "tab-current=day" in ul_class:
                    popular_today = parse_popular_list(ul)
                elif "tab-current=week" in ul_class:
                    popular_weekly = parse_popular_list(ul)
                elif "tab-current=month" in ul_class:
                    popular_monthly = parse_popular_list(ul)

            return {
                "genres": genres_unique,
                "spotlights": spotlights,
                "latest_episodes": latest_episodes,
                "top_airing": top_airing,
                "most_popular": most_popular,
                "completed_series": completed_series,
                "latest_movies": latest_movies,
                "upcoming": upcoming,
                "popular_today": popular_today,
                "popular_weekly": popular_weekly,
                "popular_monthly": popular_monthly
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/az-list")
async def get_az_list(letter: str = Query(..., description="Letter A to Z, 0-9, or 'other'")):
    url = f"{BASE_URL}az-list/?letter={letter}"
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to fetch A-Z page")
            
            sel = Selector(r.text)
            anime_list = []
            
            for card in sel.css("article.anime-card"):
                title_spans = card.css("h3 a span::text").getall()
                title_jp = title_spans[0].strip() if len(title_spans) > 0 else ""
                title_en = title_spans[1].strip() if len(title_spans) > 1 else ""
                if not title_jp and not title_en:
                    title_jp = card.css("h3 a::text").get("").strip() or card.css("img::attr(alt)").get("").strip()
                    title_en = title_jp
                
                poster = card.css("img::attr(src)").get("") or card.css("img::attr(data-src)").get("")
                link = card.css("h3 a.stretched-link::attr(href)").get("") or card.css("a.stretched-link::attr(href)").get("")
                
                # Try to extract the real anime detail URL from the Info button
                info_btn = card.css("button[onclick*='/anime/']::attr(onclick)").get("")
                detail_url = ""
                if info_btn:
                    detail_match = re.search(r"window\.location\.href='([^']+)'", info_btn)
                    if detail_match:
                        detail_url = detail_match.group(1)
                
                if detail_url:
                    slug_match = re.search(r'/anime/([^/]+)/', detail_url)
                    slug = slug_match.group(1) if slug_match else ""
                else:
                    slug_match = re.search(r'/(watch|anime)/([^/]+)/', link)
                    slug = slug_match.group(2) if slug_match else ""
                    slug = re.sub(r'-episode-\d+', '', slug)
                    slug = re.sub(r'-movie', '', slug)
                
                anime_list.append({
                    "title_en": title_en or title_jp,
                    "title_jp": title_jp,
                    "slug": slug,
                    "link": link,
                    "poster": poster
                })
                
            return {"letter": letter, "results": anime_list}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/anime/{slug}")
async def get_anime_detail(slug: str):
    url = f"{BASE_URL}anime/{slug}/"
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"Anime '{slug}' not found")
            
            sel = Selector(r.text)
            
            # Title System (EN/JP)
            spans = sel.css("h1 span.anime::text").getall()
            title_jp = spans[0].strip() if len(spans) > 0 else ""
            title_en = spans[1].strip() if len(spans) > 1 else ""
            if not title_jp:
                title_jp = sel.css("h1::text").get("").strip()
            
            # Poster
            poster = ""
            for img in sel.css("img"):
                src = img.attrib.get("src") or img.attrib.get("data-src") or ""
                if "cdn.myanimelist.net/images/anime" in src or "image.tmdb.org" in src:
                    poster = src
                    break
            
            # Synopsis
            synopsis = " ".join([p.strip() for p in sel.css('section[aria-label="Anime Overview"] p::text').getall() if p.strip()])
            if not synopsis:
                synopsis = sel.css("div[data-synopsis] p::text").get("").strip()
            if not synopsis:
                synopsis = " ".join([p.strip() for p in sel.css("div.prose p::text").getall() if p.strip()])
            
            # Post ID
            post_id = sel.css("input#comment_post_ID::attr(value)").get()
            if not post_id:
                match = re.search(r"showWatchlistModal\('#watchlist-(\d+)'\)", r.text)
                if match:
                    post_id = match.group(1)
            if not post_id:
                match = re.search(r'"postId"\s*:\s*"(\d+)"', r.text)
                if match:
                    post_id = match.group(1)
            
            # Metadata
            metadata = {}
            for dt, dd in zip(sel.css("dt"), sel.css("dd")):
                dt_text = dt.css("::text").get("").strip().replace(":", "")
                dd_text = dd.css("::text").get("").strip()
                if not dd_text:
                    dd_text = dd.css("a::text").get("").strip()
                if dt_text and dd_text:
                    metadata[dt_text] = dd_text
            
            # Seasons
            seasons = []
            for btn in sel.css("#seasonButtonsContainer button"):
                season_id = btn.attrib.get("data-season")
                season_name = btn.css("::text").get("").strip()
                if season_id:
                    seasons.append({"season_id": season_id, "season_name": season_name})
            
            # Fallback if seasons container is empty
            if not seasons and post_id:
                seasons.append({"season_id": post_id, "season_name": "Season 1"})
                
            return {
                "title_en": title_en or title_jp,
                "title_jp": title_jp,
                "slug": slug,
                "url": url,
                "postId": post_id,
                "synopsis": synopsis,
                "poster": poster,
                "metadata": metadata,
                "seasons": seasons
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/anime/{slug}/episodes")
async def get_episodes(
    slug: str,
    postId: str = Query(..., description="Post ID / Season ID"),
    page: int = Query(1, description="Page number for episodes list"),
    order: str = Query("asc", description="Order: asc or desc")
):
    params = {
        "action": "get_episodes",
        "anime_id": postId,
        "page": str(page),
        "order": order
    }
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(AJAX_URL, params=params)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to fetch episodes")
            
            resp = r.json()
            if not resp.get("success"):
                raise HTTPException(status_code=400, detail=resp.get("message", "API returned failure status"))
            
            data = resp.get("data", {})
            episodes = data.get("episodes", [])
            
            # Map episodes and extract slugs
            mapped_episodes = []
            for ep in episodes:
                url = ep.get("url", "")
                slug_match = re.search(r'/watch/([^/]+)/', url)
                slug = slug_match.group(1) if slug_match else ""
                
                mapped_episodes.append({
                    "id": ep.get("id"),
                    "number": ep.get("number"),
                    "title": ep.get("title"),
                    "duration": ep.get("duration"),
                    "released": ep.get("released"),
                    "url": url,
                    "slug": slug,
                    "thumbnail": ep.get("thumbnail")
                })
                
            return {
                "episodes": mapped_episodes,
                "max_pages": data.get("max_episodes_page", 1)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/watch/{episode_slug}")
async def get_episode_watch_servers(episode_slug: str):
    url = f"{BASE_URL}watch/{episode_slug}/"
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"Episode '{episode_slug}' not found")
            
            sel = Selector(r.text)
            servers = []
            
            # Extract standard data-embed-id elements
            for el in sel.css("[data-embed-id]"):
                decoded = decode_base64_embed(el.attrib.get("data-embed-id"))
                if decoded:
                    # Clean tags if there are script iframes inside url field
                    url_val = decoded["url"]
                    if "<iframe" in url_val:
                        iframe_src_match = re.search(r"src=['\"]([^'\"]+)['\"]", url_val)
                        if iframe_src_match:
                            url_val = iframe_src_match.group(1)
                    
                    servers.append({
                        "name": decoded["name"],
                        "url": url_val
                    })
                    
            # Check for GDMirrorBot embeds to fetch other mirrors dynamically
            for s in list(servers):
                embed_url = s.get("url", "")
                if "gdmirrorbot.nl" in embed_url:
                    sid_match = re.search(r'/embed/([^/]+)', embed_url)
                    if sid_match:
                        sid = sid_match.group(1)
                        mirrors = await fetch_embedhelper_sources(client, sid)
                        for m in mirrors:
                            # Avoid duplicates
                            if not any(x.get("url") == m["url"] for x in servers):
                                servers.append(m)
                                
            return {
                "episode_slug": episode_slug,
                "url": url,
                "servers": servers
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search/instant")
async def get_instant_search(query: str = Query(..., description="Query keyword")):
    params = {
        "action": "instant_search",
        "query": query
    }
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(AJAX_URL, params=params)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to run instant search")
            
            resp = r.json()
            if not resp.get("success"):
                return {"results": [], "total": 0}
            
            html_content = resp["data"]["html"]
            sel = Selector(html_content)
            results = []
            
            for card in sel.css("a"):
                spans = card.css("h3 span::text").getall()
                title_jp = spans[0].strip() if len(spans) > 0 else ""
                title_en = spans[1].strip() if len(spans) > 1 else ""
                
                link = card.attrib.get("href", "")
                poster = card.css("img::attr(src)").get("") or card.css("img::attr(data-src)").get("")
                
                slug_match = re.search(r'/anime/([^/]+)/', link)
                slug = slug_match.group(1) if slug_match else ""
                
                results.append({
                    "title_en": title_en or title_jp,
                    "title_jp": title_jp,
                    "slug": slug,
                    "link": link,
                    "poster": poster
                })
                
            return {"results": results, "total": resp["data"].get("total", len(results))}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search/advanced")
async def get_advanced_search(
    q: str = Query("", description="Search keyword"),
    page: int = Query(1, description="Page index")
):
    # Form-encode WordPress payload as dictionary supporting only query and page
    form_data = {
        "action": "advanced_search",
        "page": str(page),
        "s_keyword": q,
        "orderby": "date",
        "order": "DESC"
    }
        
    adv_headers = HEADERS.copy()
    adv_headers["Referer"] = f"{BASE_URL}search/"
    
    async with httpx.AsyncClient(headers=adv_headers, timeout=20) as client:
        try:
            r = await client.post(AJAX_URL, data=form_data)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to fetch advanced search")
            
            resp = r.json()
            if not resp.get("success"):
                return {"results": [], "max_pages": 1, "current_page": page}
            
            html_content = resp["data"]["html"]
            sel = Selector(html_content)
            results = parse_card_list(sel.css(".anime-card"))
                
            return {
                "results": results,
                "max_pages": resp["data"].get("max_pages", 1),
                "current_page": resp["data"].get("current_page", page)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/languages")
async def get_languages():
    return [
        {"name": "Hindi", "slug": "hindi"},
        {"name": "Tamil", "slug": "tamil"},
        {"name": "Telugu", "slug": "telugu"},
        {"name": "English", "slug": "english"},
        {"name": "Japanese", "slug": "japanese"},
        {"name": "Bengali", "slug": "bengali"},
        {"name": "Malayalam", "slug": "malayalam"},
        {"name": "Kannada", "slug": "kannada"}
    ]

@app.get("/api/language/{slug}")
async def get_language_tag(
    slug: str,
    page: int = Query(1, description="Page number")
):
    url = f"{BASE_URL}tag/{slug}/"
    if page > 1:
        url += f"?tag_page={page}"
        
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Failed to fetch language page")
            
            sel = Selector(r.text)
            results = parse_card_list(sel.css("article.anime-card"))
            
            page_nums = [1]
            for page_el in sel.css(".page-numbers::text").getall():
                try:
                    num = int(page_el.strip())
                    page_nums.append(num)
                except ValueError:
                    pass
            max_pages = max(page_nums)
            
            return {
                "language": slug,
                "results": results,
                "max_pages": max_pages,
                "current_page": page
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
