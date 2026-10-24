#!/usr/bin/env python3
"""Publish a Feedly-friendly hiphopheads FRESH RSS feed from an Apify task.

Reads the latest successful task dataset; does not start or charge for an actor run.
"""
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET

TASK = os.getenv('APIFY_TASK', 'fascinated_cleverness~Reddit-Scraper-Pro-task')
BASE = os.getenv('FEED_BASE_URL', 'https://anish-rss.github.io/hiphop-fresh-rss').rstrip('/')
OUT = Path(os.getenv('OUTPUT_DIR', 'site'))
MAX_LOOKUPS = 35
NS_MEDIA = 'http://search.yahoo.com/mrss/'
ET.register_namespace('media', NS_MEDIA)
FRESH = re.compile(r'^\s*\[FRESH(?:\s+[^\]]+)?\]', re.I)


def safe_url(value, image=False):
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    if not value or len(value) > 2048:
        return None
    try:
        p = urlparse(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return None
        host = p.hostname.lower().rstrip('.')
        if host == 'localhost' or host.endswith(('.local', '.localhost', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if image and p.path.lower().endswith(('.svg', '.svgz')):
            return None
        return value
    except ValueError:
        return None


def public_host(host):
    try:
        return bool(host) and all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
    except (OSError, ValueError):
        return False


class RedirectCheck(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        url = safe_url(url)
        if not url or not public_host(urlparse(url).hostname):
            raise ValueError('Blocked unsafe redirect')
        return super().redirect_request(req, fp, code, msg, headers, url)


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'meta':
            return
        a = dict(attrs)
        key = (a.get('property') or a.get('name') or '').lower()
        if key in ('og:image', 'og:image:secure_url', 'twitter:image', 'twitter:image:src') and a.get('content'):
            self.images.append(a['content'])


def youtube_image(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    video = None
    if host in ('youtu.be', 'www.youtu.be'):
        video = p.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if p.path == '/watch':
            video = parse_qs(p.query).get('v', [None])[0]
        elif p.path.startswith(('/shorts/', '/embed/', '/live/')):
            video = p.path.split('/')[2]
    if video and re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
        return f'https://i.ytimg.com/vi/{video}/hqdefault.jpg'
    return None


def article_image(url):
    url = safe_url(url)
    if not url:
        return None
    host = urlparse(url).hostname
    if host == 'reddit.com' or host.endswith(('.reddit.com', 'redd.it')) or not public_host(host):
        return None
    try:
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; HipHopFreshRSS/1.0)', 'Accept': 'text/html'})
        with build_opener(RedirectCheck()).open(request, timeout=5) as response:
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                return None
            final = response.geturl()
            content = response.read(250000)
            charset = response.headers.get_content_charset() or 'utf-8'
        parser = Metadata()
        parser.feed(content.decode(charset, errors='replace'))
        for candidate in parser.images:
            image = safe_url(urljoin(final, candidate), image=True)
            if image:
                return image
    except (OSError, ValueError, UnicodeError, LookupError):
        pass
    return None


def direct_image(post):
    for field in ('imageUrl', 'thumbnailUrl', 'thumbnail', 'externalUrl'):
        val = post.get(field)
        if isinstance(val, dict):
            val = val.get('url')
        image = safe_url(val, image=True)
        if image:
            if field != 'externalUrl' or urlparse(image).path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                return image
    for val in (post.get('galleryImagesUrls') or post.get('galleryImages') or []):
        if isinstance(val, dict):
            val = val.get('url') or val.get('imageUrl')
        image = safe_url(val, image=True)
        if image:
            return image
    return None


def posted_at(post):
    for field in ('submittedAt', 'createdAt', 'createdUtc', 'created_utc', 'publishedAt', 'date', 'timestamp', 'created'):
        value = post.get(field)
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
            if isinstance(value, str) and value:
                result = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
    return None


def generate(records, lookup=True):
    if not isinstance(records, list):
        raise ValueError('Expected JSON list from Apify')
    posts = []
    seen = set()
    for post in records:
        if not isinstance(post, dict):
            continue
        subreddit = str(post.get('subreddit') or '').lower().removeprefix('r/').strip('/')
        title = str(post.get('title') or '').strip()
        url = safe_url(post.get('submissionUrl') or post.get('url') or post.get('permalink'))
        ident = str(post.get('submissionId') or url)
        if subreddit != 'hiphopheads' or not FRESH.match(title) or not url or ident in seen:
            continue
        seen.add(ident)
        posts.append((post, title, url, ident, posted_at(post)))
    if not posts:
        raise ValueError('No matching [FRESH] posts; refusing to overwrite the live feed')
    posts.sort(key=lambda p: p[4] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    for tag, value in [('title', 'HipHop Fresh - r/hiphopheads'), ('link', BASE + '/'), ('description', '[FRESH], [FRESH ALBUM], [FRESH VIDEO] and other FRESH releases from r/hiphopheads'), ('language', 'en'), ('ttl', '720')]:
        ET.SubElement(channel, tag).text = value
    ET.SubElement(channel, '{http://www.w3.org/2005/Atom}link', {'href': BASE + '/feed.xml', 'rel': 'self', 'type': 'application/rss+xml'})
    image_cache = {}
    counts = {'reddit': 0, 'youtube': 0, 'article': 0}
    for post, title, link, ident, date in posts:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = title
        ET.SubElement(item, 'link').text = link
        ET.SubElement(item, 'guid', {'isPermaLink': 'false'}).text = 'reddit:hiphopheads:' + ident
        if date:
            ET.SubElement(item, 'pubDate').text = format_datetime(date)
        picture = direct_image(post)
        if picture:
            counts['reddit'] += 1
        else:
            outbound = safe_url(post.get('externalUrl') or post.get('outboundUrl') or post.get('linkUrl'))
            if outbound:
                picture = youtube_image(outbound)
                if picture:
                    counts['youtube'] += 1
                elif lookup:
                    if outbound in image_cache:
                        picture = image_cache[outbound]
                    elif len(image_cache) < MAX_LOOKUPS:
                        picture = article_image(outbound)
                        image_cache[outbound] = picture
                        if picture:
                            counts['article'] += 1
        description = '<p><b>r/hiphopheads</b></p>'
        if picture:
            ET.SubElement(item, '{' + NS_MEDIA + '}thumbnail', {'url': picture})
            description += '<p><img src="' + html.escape(picture, quote=True) + '" alt="Cover or preview" /></p>'
        body = re.sub(r'\s+', ' ', str(post.get('bodyText') or post.get('selftext') or '').strip())[:1400]
        if body:
            description += '<p>' + html.escape(body) + '</p>'
        description += '<p><a href="' + html.escape(link, quote=True) + '">Open on Reddit</a></p>'
        ET.SubElement(item, 'description').text = description
    print(f'Filtered {len(posts)} FRESH posts from {len(records)} Apify records')
    print('Thumbnails: Reddit={reddit} YouTube={youtube} Articles={article}'.format(**counts))
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True)


def fetch_records():
    token = os.getenv('APIFY_TOKEN')
    if not token:
        raise RuntimeError('Missing GitHub Actions secret APIFY_TOKEN')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': 'HipHopFreshRSS/1.0'}
    def read(url):
        with urlopen(Request(url, headers=headers), timeout=40) as response:
            return json.load(response)
    task_id = quote(TASK.replace('/', '~'), safe='~')
    run = read('https://api.apify.com/v2/actor-tasks/' + task_id + '/runs/last?status=SUCCEEDED')['data']
    if run.get('status') != 'SUCCEEDED' or not run.get('finishedAt'):
        raise RuntimeError('No successful saved-task run. Run the saved task once from Apify Tasks (not the actor).')
    finished = datetime.fromisoformat(run['finishedAt'].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) - finished > timedelta(hours=48):
        raise RuntimeError('Last successful saved-task run is more than 48 hours old')
    dataset = run.get('defaultDatasetId')
    if not dataset:
        raise RuntimeError('Successful task run has no dataset')
    # No clean=1: it removes thumbnailUrl, externalUrl and submittedAt.
    return read('https://api.apify.com/v2/datasets/' + quote(dataset, safe='') + '/items?format=json&limit=300')


def main():
    records = json.loads(Path(sys.argv[1]).read_text('utf-8')) if len(sys.argv) > 1 else fetch_records()
    xml = generate(records)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'test2.xml').write_bytes(xml)
    (OUT / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>HipHop Fresh RSS</title><h1>HipHop Fresh RSS</h1><p><a href="feed.xml">Subscribe to RSS</a></p>\n', encoding='utf-8')
    (OUT / '.nojekyll').touch()
    print('Published RSS to', OUT / 'feed.xml')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RSS generation failed:', exc, file=sys.stderr)
        sys.exit(1)
