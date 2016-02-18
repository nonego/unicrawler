# -*- coding: utf-8 -*-
__author__ = 'yijingping'
# 加载django环境
import sys
import os
reload(sys)
sys.setdefaultencoding('utf8') 
sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
os.environ['DJANGO_SETTINGS_MODULE'] = 'unicrawler.settings'
import django
django.setup()

import time
import json
import requests
from django.conf import settings
from cores.models import Site
from configs.models import Proxy
from random import sample
from cores.util import get_redis, get_uniqueid
from cores.constants import KIND_DETAIL_URL

import logging
logger = logging.getLogger()


class RequestsDownloaderBackend(object):
    headers = [
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.118 Safari/537.36'
        }
    ]

    def __init__(self, proxy=None):
        self.proxy = proxy

    def format_proxies(self):
        p = self.proxy
        if self.proxy:
            if p.user:
                data = 'http://%s:%s@%s:%s' % (p.user, p.password, p.host, p.port)
            else:
                data = 'http://%s:%s' % (p.host, p.port)
            return {
                "http": data
            }
        else:
            return None

    def download(self, url):
        header = sample(self.headers, 1)[0]
        proxies = self.format_proxies()
        if isinstance(url, basestring):
            rsp = requests.get(url, headers=header, proxies=proxies)
            rsp.close()
            rsp.encoding = rsp.apparent_encoding
            return rsp.text
        elif isinstance(url, dict):
            link, method, data, data_type = url.get('url'), url.get('method'), url.get('data'), url.get('dataType')
            req = {'GET': requests.get, 'POST': requests.post}.get(method)
            rsp = req(link, data=data, headers=header, proxies=proxies)
            rsp.close()
            rsp.encoding = rsp.apparent_encoding
            if data_type == 'json':
                return rsp.json()
            else:
                return rsp.text


class BrowserDownloaderBackend(object):
    def download(self):
        pass


class MysqlProxyBackend(object):
    def __init__(self):
        proxy = Proxy.objects.order_by('?').first()
        self.user = proxy.user
        self.password = proxy.password
        self.host = proxy.host
        self.port = proxy.port

    def __str__(self):
        return ':'.join([str(self.user), str(self.password), str(self.host), str(self.port)])


class Downloader(object):
    def __init__(self):
        self.redis = get_redis()

    def get_proxy(self, kind):
        if kind == Site.PROXY_MYSQL:
            return MysqlProxyBackend()
        else:
            return None

    def check_limit_speed(self, config):
        if config["limit_speed"] <= 0:
            return False, None
        else:
            proxy = self.get_proxy(config['proxy'])
            key = 'unicrawler:limit_speed:%s:%s' % (config['domain'], proxy)
            if self.redis.exists(key):
                return True, proxy
            else:
                self.redis.psetex(key, config["limit_speed"], config["limit_speed"])
                return False, proxy

    def check_detail_fresh_time(self, data):
        unique_key, fresh_time, rule_id = data['unique_key'], data["detail_fresh_time"], data["rule_id"]
        if fresh_time <= 0:
            return False
        else:
            unique_value = ''.join([data.get(item) for item in unique_key])
            key = 'unicrawler:detail_fresh_time:%s:%s' % (rule_id, get_uniqueid(unique_value))
            if self.redis.exists(key):
                return True
            else:
                self.redis.setex(key, fresh_time, fresh_time)
                return False

    def run(self):
        r = self.redis
        if settings.CRAWLER_DEBUG:
            r.delete(settings.CRAWLER_CONFIG["downloader"])
        while True:
            try:
                resp_data = r.brpop(settings.CRAWLER_CONFIG["downloader"])
            except Exception as e:
                print e
                continue

            try:
                data = json.loads(resp_data[1])
                site_config = data['site_config']
                logger.debug(data["url"])
                is_limited, proxy = self.check_limit_speed(site_config)
                if is_limited:
                    print '# 被限制, 放回去, 下次下载'
                    time.sleep(1)  # 休息一秒, 延迟放回去的时间
                    r.lpush(settings.CRAWLER_CONFIG["downloader"], resp_data[1])
                elif (data["kind"] == KIND_DETAIL_URL
                    and self.check_detail_fresh_time(data)):
                    print '# 该详情页已下载过, 不下载了'
                else:
                    print '# 未被限制,可以下载'
                    if site_config['browser'] == Site.BROWSER_NONE:
                        browser = RequestsDownloaderBackend(proxy=proxy)
                    else:
                        return

                    data['body'] = browser.download(data["url"])
                    r.lpush(settings.CRAWLER_CONFIG["extractor"], json.dumps(data))
                    logger.debug(data)
            except Exception as e:
                print e
                raise


if __name__ == '__main__':
    downloader = Downloader()
    downloader.run()
