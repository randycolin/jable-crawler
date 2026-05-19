"""爬虫模块 - 分页遍历+元数据提取"""
import re
import time
import random
import requests
from urllib.parse import urljoin
from database import (add_video, update_crawl_progress, get_crawl_progress,
                      get_setting, count_videos)

BASE_URL = "https://jable.tv"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


def get_session():
    """创建带代理和UA的session"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': BASE_URL,
    })
    proxy = get_setting('proxy')
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    return session


def get_delay():
    """获取请求间隔（带随机浮动）"""
    base = float(get_setting('request_delay') or 2)
    return base + random.uniform(0, 1)


def fetch_categories():
    """获取所有分类标签"""
    session = get_session()
    try:
        resp = session.get(f"{BASE_URL}/categories/", timeout=15)
        resp.raise_for_status()
        # 提取分类
        cats = re.findall(
            r'<a href="https://jable\.tv/categories/([^"]+)/"[^>]*>\s*<span[^>]*>([^<]+)</span>',
            resp.text
        )
        return [{'slug': c[0], 'name': c[1].strip()} for c in cats]
    except Exception as e:
        return []


def fetch_tags():
    """获取热门标签"""
    session = get_session()
    try:
        resp = session.get(f"{BASE_URL}/tags/", timeout=15)
        resp.raise_for_status()
        tags = re.findall(
            r'<a href="https://jable\.tv/tags/([^"]+)/"[^>]*title="([^"]*)"',
            resp.text
        )
        return [{'slug': t[0], 'name': t[1].strip()} for t in tags]
    except Exception as e:
        return []


def parse_video_list(html):
    """从HTML片段中提取视频列表；优先 BeautifulSoup，失败再用正则 fallback。"""
    videos = []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        seen = set()
        for a in soup.select('a[href*="/videos/"]'):
            href = a.get('href') or ''
            m = re.search(r'https://jable\.tv/videos/([^/]+)/', href)
            if not m or href in seen:
                continue
            seen.add(href)
            code = m.group(1).upper()

            container = a
            for parent in a.parents:
                if getattr(parent, 'name', '') in ('div', 'article'):
                    container = parent
                    break

            title_el = None
            if container:
                title_el = container.select_one('h6.title a, .title a, h6.title, .title')
            if not title_el:
                title_el = a.select_one('h6.title a, .title a, h6.title, .title')
            title = title_el.get_text(strip=True) if title_el else code

            img = a.select_one('img') or (container.select_one('img') if container else None)
            thumb = ''
            if img:
                thumb = img.get('data-src') or img.get('src') or ''

            label = a.select_one('.label') or (container.select_one('.label') if container else None)
            duration = label.get_text(strip=True) if label else ''

            videos.append({
                'url': href.strip(),
                'code': code.strip(),
                'thumbnail': thumb.strip(),
                'duration': duration.strip(),
                'title': title.strip(),
                'tags': '',
                'actress': '',
                'publish_date': '',
            })
    except Exception:
        videos = []

    if videos:
        return videos

    # 正则 fallback：兼容旧布局
    pattern = re.compile(
        r'<a href="(https://jable\.tv/videos/([^/]+)/)"[^>]*>.*?'
        r'<img[^>]*data-src="([^"]*)"[^>]*>.*?'
        r'<span class="label"[^>]*>([^<]*)</span>.*?'
        r'<h6 class="title"[^>]*><a[^>]*>([^<]*)</a>',
        re.DOTALL
    )

    for match in pattern.finditer(html):
        url, code, thumb, duration, title = match.groups()
        videos.append({
            'url': url.strip(),
            'code': code.strip().upper(),
            'thumbnail': thumb.strip(),
            'duration': duration.strip(),
            'title': title.strip(),
            'tags': '',
            'actress': '',
            'publish_date': '',
        })

    if not videos:
        urls = re.findall(r'href="(https://jable\.tv/videos/([^/]+)/)"', html)
        titles = re.findall(r'<h6 class="title"[^>]*><a[^>]*>([^<]*)</a>', html)
        thumbs = re.findall(r'data-src="(https://[^"]*\.jpg[^"]*)"', html)
        durations = re.findall(r'<span class="label"[^>]*>([^<]*)</span>', html)

        for i, (url, code) in enumerate(urls):
            videos.append({
                'url': url,
                'code': code.upper(),
                'thumbnail': thumbs[i] if i < len(thumbs) else '',
                'duration': durations[i] if i < len(durations) else '',
                'title': titles[i] if i < len(titles) else code,
                'tags': '',
                'actress': '',
                'publish_date': '',
            })

    return videos
def crawl_tag(tag_slug, tag_name='', callback=None, stop_event=None):
    """
    爬取指定标签的所有视频元数据
    callback: 回调函数(current_page, total_found, new_added)
    stop_event: threading.Event 用于中断
    """
    session = get_session()
    progress = get_crawl_progress(tag_slug)
    start_page = (progress['last_page'] if progress else 0) + 1

    page = start_page
    total_found = 0
    new_added = 0
    consecutive_empty = 0

    while True:
        if stop_event and stop_event.is_set():
            break

        # 分页API
        api_url = (f"{BASE_URL}/tags/{tag_slug}/?mode=async&function=get_block"
                   f"&block_id=list_videos_common_videos_list&sort_by=post_date&from={page}")

        try:
            time.sleep(get_delay())
            session.headers['User-Agent'] = random.choice(USER_AGENTS)
            resp = session.get(api_url, timeout=15)

            if resp.status_code == 404 or not resp.text.strip():
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue

            resp.raise_for_status()
            videos = parse_video_list(resp.text)

            if not videos:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue

            consecutive_empty = 0
            total_found += len(videos)

            for v in videos:
                v['tags'] = tag_name or tag_slug
                if add_video(v):
                    new_added += 1

            update_crawl_progress(tag_slug, tag_name, page, total_found,
                                  1 if consecutive_empty >= 3 else 0)

            if callback:
                callback(page, total_found, new_added)

            page += 1

        except requests.exceptions.RequestException as e:
            if callback:
                callback(page, total_found, new_added, error=str(e))
            time.sleep(5)
            # 自适应降速
            consecutive_empty += 1
            if consecutive_empty >= 5:
                break

    # 标记完成
    update_crawl_progress(tag_slug, tag_name, page - 1, total_found, 1)
    return {'pages': page - 1, 'total_found': total_found, 'new_added': new_added}


def crawl_category(cat_slug, cat_name='', callback=None, stop_event=None):
    """爬取分类（和标签类似但URL不同）"""
    session = get_session()
    progress = get_crawl_progress(f"cat:{cat_slug}")
    start_page = (progress['last_page'] if progress else 0) + 1

    page = start_page
    total_found = 0
    new_added = 0
    consecutive_empty = 0

    while True:
        if stop_event and stop_event.is_set():
            break

        api_url = (f"{BASE_URL}/categories/{cat_slug}/?mode=async&function=get_block"
                   f"&block_id=list_videos_common_videos_list&sort_by=post_date&from={page}")

        try:
            time.sleep(get_delay())
            session.headers['User-Agent'] = random.choice(USER_AGENTS)
            resp = session.get(api_url, timeout=15)

            if resp.status_code == 404 or not resp.text.strip():
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue

            resp.raise_for_status()
            videos = parse_video_list(resp.text)

            if not videos:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue

            consecutive_empty = 0
            total_found += len(videos)

            for v in videos:
                v['tags'] = cat_name or cat_slug
                if add_video(v):
                    new_added += 1

            update_crawl_progress(f"cat:{cat_slug}", cat_name, page, total_found, 0)

            if callback:
                callback(page, total_found, new_added)

            page += 1

        except requests.exceptions.RequestException as e:
            if callback:
                callback(page, total_found, new_added, error=str(e))
            time.sleep(5)
            consecutive_empty += 1
            if consecutive_empty >= 5:
                break

    update_crawl_progress(f"cat:{cat_slug}", cat_name, page - 1, total_found, 1)
    return {'pages': page - 1, 'total_found': total_found, 'new_added': new_added}


def extract_m3u8_url(html):
    """从详情页 HTML/JS 中提取 m3u8，兼容普通 URL 和 JS 转义 URL。"""
    candidates = []
    patterns = [
        r'(https?://[^"\'<>\\]+\.m3u8[^"\'<>]*)',
        r'(https?:\\/\\/[^"\'<>]+?\.m3u8[^"\'<>]*)',
        r'(?:hlsUrl|source|src|url)\s*[:=]\s*["\']([^"\']+?\.m3u8[^"\']*)["\']',
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html, flags=re.IGNORECASE))
    for raw in candidates:
        url = raw.replace('\\/', '/').replace('\\u0026', '&').replace('&amp;', '&')
        url = url.strip().strip('"\'')
        if '.m3u8' in url:
            return url
    return ''


def fetch_video_detail(video_url):
    """获取视频详情页信息（m3u8链接、演员等）"""
    session = get_session()
    try:
        resp = session.get(video_url, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # 提取m3u8
        m3u8_url = extract_m3u8_url(html)

        # 提取演员
        actress_match = re.findall(
            r'<a href="https://jable\.tv/models/[^"]*/"[^>]*><span[^>]*>([^<]+)</span>',
            html
        )
        actress = ', '.join(actress_match) if actress_match else ''

        # 提取标签
        tag_matches = re.findall(
            r'<a href="https://jable\.tv/tags/[^"]*/"[^>]*>([^<]+)</a>',
            html
        )
        tags = ', '.join(t.strip() for t in tag_matches) if tag_matches else ''

        return {
            'm3u8_url': m3u8_url,
            'actress': actress,
            'tags': tags,
        }
    except Exception as e:
        return {'m3u8_url': '', 'actress': '', 'tags': '', 'error': str(e)}
