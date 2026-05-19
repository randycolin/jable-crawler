"""91PORNY 免费内容适配器。"""
import html as html_lib
import re
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import SiteAdapter


class Porn91Adapter(SiteAdapter):
    site = '91porny'
    display_name = '91PORNY'
    base_url = 'https://91porny.com'

    FREE_PATH_PREFIXES = ('/video/view/', '/videos/view/', '/vod/view/')
    PAID_PATH_MARKERS = ('/video/viewhd/', '/viewhd/')
    PAID_KEYWORDS = ('VIP', 'vip', '付费', '金币', '购买', '开通', '试看', '预览', '會員', '会员')

    CHANNELS = {
        'video': ('91视频', '/video'),
        'videos': ('蝌蚪', '/videos'),
        'vod': ('精品', '/vod'),
    }

    def matches_url(self, url):
        host = urlparse(url or '').netloc.lower()
        return host.endswith('91porny.com')

    def get_headers(self, url=None):
        headers = super().get_headers(url)
        headers.update({
            'Referer': url if url and str(url).startswith('http') else 'https://91porny.com/',
            'Origin': 'https://91porny.com',
            'Accept': '*/*',
            'Connection': 'keep-alive',
        })
        return headers

    def _session(self):
        s = requests.Session()
        s.headers.update(self.get_headers())
        return s

    def normalize_url(self, url):
        if not url:
            return ''
        url = html_lib.unescape(str(url)).replace('&amp;', '&')
        if url.startswith('//'):
            return 'https:' + url
        return urljoin(self.base_url, url)

    def is_candidate_free_url(self, url):
        path = urlparse(self.normalize_url(url)).path
        if any(marker in path for marker in self.PAID_PATH_MARKERS):
            return False
        return any(path.startswith(prefix) for prefix in self.FREE_PATH_PREFIXES)

    def extract_code(self, url):
        path = urlparse(self.normalize_url(url)).path.strip('/')
        parts = path.split('/')
        if len(parts) >= 3 and parts[0] in ('video', 'videos', 'vod') and parts[1] == 'view':
            return parts[2]
        return re.sub(r'\W+', '_', path)[-40:] or '91porny'

    def parse_video_list(self, html):
        soup = BeautifulSoup(html or '', 'html.parser')
        videos = []
        seen = set()
        cards = soup.select('.video-elem') or soup.select('a[href*="/video/view/"], a[href*="/videos/view/"], a[href*="/vod/view/"]')
        for card in cards:
            root = card if getattr(card, 'name', None) != 'a' else card.parent
            title_a = None
            display_a = None
            if getattr(card, 'name', None) == 'a':
                display_a = card
                title_a = card if 'title' in (card.get('class') or []) else None
            if root:
                title_a = title_a or root.select_one('a.title') or root.select_one('a[href*="/video/view/"], a[href*="/videos/view/"], a[href*="/vod/view/"]')
                display_a = display_a or root.select_one('a.display') or title_a
            href = (title_a or display_a).get('href', '') if (title_a or display_a) else ''
            url = self.normalize_url(href)
            if not url or url in seen or not self.is_candidate_free_url(url):
                continue
            seen.add(url)
            title = title_a.get_text(' ', strip=True) if title_a else ''
            if not title or re.fullmatch(r'[\d:]+\s*(高清)?', title):
                title = f'91PORNY_{self.extract_code(url)}'
            thumb = ''
            if root:
                img_div = root.select_one('.img')
                if img_div:
                    style = img_div.get('style', '')
                    m = re.search(r'url\([\'\"]?([^\'\")]+)', style)
                    if m:
                        thumb = self.normalize_url(m.group(1))
                if not thumb:
                    img = root.select_one('img')
                    if img:
                        thumb = self.normalize_url(img.get('src') or img.get('data-src') or '')
            duration = ''
            if root:
                layer = root.select_one('.layer')
                duration = layer.get_text(' ', strip=True) if layer else ''
            videos.append({
                'site': self.site,
                'code': self.extract_code(url),
                'title': title,
                'url': url,
                'tags': '91porny,免费',
                'thumbnail': thumb,
                'duration': duration,
                'actress': '',
                'publish_date': '',
                'pay_status': 'unknown',
                'is_downloadable': 0,
            })
        return videos

    def _search_url(self, keyword, page=1):
        url = f'{self.base_url}/search?keywords={quote(keyword)}'
        if page and int(page) > 1:
            url += f'&page={int(page)}'
        return url

    def search(self, keyword, page=1):
        resp = self._session().get(self._search_url(keyword, page), timeout=30)
        resp.raise_for_status()
        return self.parse_video_list(resp.text)

    def parse_search_meta(self, html, current_page=1):
        """从搜索结果页解析总页数/总候选数。

        站点不一定给明确总数；total_candidates 用“页数 × 当前页条数”估算，
        主要目的是先告诉用户最多有多少页、避免手动输入超出页数。
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        pages = {int(current_page or 1)}
        for a in soup.select('a[href*="page="]'):
            href = html_lib.unescape(a.get('href') or '')
            parsed = urlparse(self.normalize_url(href))
            raw_pages = parse_qs(parsed.query).get('page') or []
            for raw in raw_pages:
                if str(raw).isdigit():
                    pages.add(int(raw))
            text = a.get_text(' ', strip=True)
            if text.isdigit():
                pages.add(int(text))
        for text in soup.stripped_strings:
            m = re.search(r'(?:共|total)\s*(\d+)\s*(?:页|pages?)', text, re.I)
            if m:
                pages.add(int(m.group(1)))
        page_count = max(pages) if pages else int(current_page or 1)
        first_count = len(self.parse_video_list(html))
        return {
            'pages_found': page_count,
            'first_page_count': first_count,
            'total_candidates': page_count * first_count if first_count else 0,
        }

    def search_all_pages(self, keyword, max_pages=50):
        """先请求第1页解析总页数，再自动拉取全部有效页。"""
        session = self._session()
        resp = session.get(self._search_url(keyword, 1), timeout=30)
        resp.raise_for_status()
        first_html = resp.text
        meta = self.parse_search_meta(first_html, current_page=1)
        pages_found = max(1, int(meta.get('pages_found') or 1))
        pages_to_fetch = min(pages_found, int(max_pages or pages_found))
        videos = self.parse_video_list(first_html)
        seen = {v.get('url') for v in videos if v.get('url')}
        pages_fetched = 1
        stopped_early = False
        stop_reason = ''
        for page in range(2, pages_to_fetch + 1):
            try:
                resp = session.get(self._search_url(keyword, page), timeout=30)
                resp.raise_for_status()
            except requests.HTTPError as e:
                status = getattr(getattr(e, 'response', None), 'status_code', None)
                # 91 搜索分页偶尔会在末尾返回 422；已有结果可用时不要整次失败。
                if status in (404, 410, 422) or '422' in str(e) or '404' in str(e):
                    stopped_early = True
                    stop_reason = str(e)
                    break
                raise
            page_videos = self.parse_video_list(resp.text)
            if not page_videos:
                stopped_early = True
                stop_reason = 'empty page'
                break
            pages_fetched = page
            for v in page_videos:
                if v.get('url') and v.get('url') not in seen:
                    seen.add(v.get('url'))
                    videos.append(v)
        meta['pages_found'] = pages_found
        meta['pages_fetched'] = pages_fetched
        meta['stopped_early'] = stopped_early
        meta['stop_reason'] = stop_reason
        meta['total_candidates'] = len(videos)
        return videos, meta

    def channel_url(self, channel='video', page=1):
        page = int(page or 1)
        if channel == 'video':
            return f'{self.base_url}/video' if page <= 1 else f'{self.base_url}/video/category/hd/{page}'
        if channel == 'videos':
            return f'{self.base_url}/videos' if page <= 1 else f'{self.base_url}/videos/top-rated/{page}'
        if channel == 'vod':
            return f'{self.base_url}/vod' if page <= 1 else f'{self.base_url}/vod?page={page}'
        raise ValueError(f'未知频道: {channel}')

    def crawl_channel_page(self, channel='video', page=1):
        resp = self._session().get(self.channel_url(channel, page), timeout=30)
        resp.raise_for_status()
        return self.parse_video_list(resp.text)

    def fetch_video_detail(self, url):
        url = self.normalize_url(url)
        result = {
            'site': self.site,
            'url': url,
            'title': '',
            'poster': '',
            'media_url': '',
            'm3u8_url': '',
            'direct_url': '',
            'is_free': False,
            'is_downloadable': False,
            'pay_status': 'paid_or_unavailable',
            'skip_reason': '',
            'tags': '91porny,免费',
            'actress': '',
        }
        if not self.is_candidate_free_url(url):
            result['skip_reason'] = '跳过非免费候选链接'
            return result
        resp = self._session().get(url, timeout=30)
        resp.raise_for_status()
        text = resp.text
        if any(k in text for k in self.PAID_KEYWORDS) and 'data-src' not in text:
            result['skip_reason'] = '页面提示非免费或不可播放'
            return result
        soup = BeautifulSoup(text, 'html.parser')
        title_el = soup.select_one('h1, .title')
        if title_el:
            result['title'] = title_el.get_text(' ', strip=True)
        video = soup.select_one('video#video-play') or soup.select_one('video[data-src]')
        media_url = html_lib.unescape(video.get('data-src', '').strip()) if video else ''
        poster = html_lib.unescape(video.get('data-poster', '').strip()) if video else ''
        media_url = self.normalize_url(media_url)
        poster = self.normalize_url(poster)
        result['poster'] = poster
        if not media_url:
            result['skip_reason'] = '没有免费播放源 data-src'
            return result
        result.update({
            'media_url': media_url,
            'is_free': True,
            'is_downloadable': True,
            'pay_status': 'free',
            'skip_reason': '',
        })
        if '.m3u8' in media_url.lower():
            result['m3u8_url'] = media_url
        elif '.mp4' in media_url.lower():
            result['direct_url'] = media_url
        else:
            result['direct_url'] = media_url
        return result

    def is_m3u8_video(self, url):
        """只接受能解析出 m3u8 的免费详情页；mp4 直链返回 False。"""
        try:
            detail = self.fetch_video_detail(url)
        except Exception:
            return False, {}
        return bool(detail.get('m3u8_url')), detail
