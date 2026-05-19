"""站点适配器注册表。"""
from .jable import JableAdapter
from .porn91 import Porn91Adapter

ADAPTERS = [JableAdapter(), Porn91Adapter()]


def get_adapter(site='jable'):
    site = site or 'jable'
    for adapter in ADAPTERS:
        if adapter.site == site:
            return adapter
    raise ValueError(f'未知站点: {site}')


def detect_adapter(url):
    for adapter in ADAPTERS:
        if adapter.matches_url(url):
            return adapter
    raise ValueError(f'不支持的链接: {url}')
