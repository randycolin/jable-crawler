"""站点适配器基础类。"""
from urllib.parse import urljoin


class SiteAdapter:
    site = ''
    display_name = ''
    base_url = ''

    def matches_url(self, url):
        return False

    def normalize_url(self, url):
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/') and self.base_url:
            return urljoin(self.base_url, url)
        return url

    def get_headers(self, url=None):
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': self.base_url + '/' if self.base_url else (url or ''),
        }

    def fetch_video_detail(self, url):
        raise NotImplementedError
