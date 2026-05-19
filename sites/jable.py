"""Jable 站点适配器。"""
from .base import SiteAdapter


class JableAdapter(SiteAdapter):
    site = 'jable'
    display_name = 'Jable'
    base_url = 'https://jable.tv'

    def matches_url(self, url):
        return 'jable.tv' in (url or '')

    def get_headers(self, url=None):
        headers = super().get_headers(url)
        headers['Referer'] = 'https://jable.tv/'
        return headers

    def fetch_video_detail(self, url):
        # 复用现有 crawler 逻辑，避免一次性大迁移导致 Jable 功能回退。
        from crawler import fetch_video_detail
        detail = fetch_video_detail(url)
        detail.setdefault('site', self.site)
        detail.setdefault('media_url', detail.get('m3u8_url', ''))
        detail.setdefault('direct_url', '')
        detail.setdefault('is_downloadable', bool(detail.get('m3u8_url')))
        detail.setdefault('pay_status', 'free' if detail.get('m3u8_url') else 'unknown')
        return detail
