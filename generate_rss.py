
#!/usr/bin/env python3
"""Generate Feedly-compatible HipHop Fresh RSS feeds from Apify.

Produces:
  site/feed.xml
  site/test2.xml
  site/index.html

Uses the latest successful Apify saved-task run.
Does not start another Apify scrape.
"""

import html
import ipaddress
import json
import os
import re
import socket
import sys

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import (
    Request,
    urlopen,
    build_opener,
    HTTPRedirectHandler,
)
import xml.etree.ElementTree as ET


TASK = os.getenv(
    "APIFY_TASK",
    "fascinated_cleverness~Reddit-Scraper-Pro-task",
)

BASE = os.getenv(
    "FEED_BASE_URL",
    "https://anish-rss.github.io/hiphop-fresh-rss",
).rstrip("/")

OUT = Path(os.getenv("OUTPUT_DIR", "site"))

MAX_LOOKUPS = 35
NS_MEDIA = "http://search.yahoo.com/mrss/"

ET.register_namespace("media", NS_MEDIA)

FRESH = re.compile(r"^\s*\[FRESH(?:\s+[^\]]+)?\]", re.I)


def safe_url(value, image=False):
    if not isinstance(value, str):
        return None

    value = html.unescape(value).strip()

    if not value or len(value) > 2048:
        return None

    try:
        parsed = urlparse(value)

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return None

        host = parsed.hostname.lower().rstrip(".")

        if host == "localhost" or host.endswith(
            (".local", ".localhost", ".internal")
        ):
            return None

        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass

        if image and parsed.path.lower().endswith((".svg", ".svgz")):
            return None

        return value

    except ValueError:
        return None


def public_host(host):
    try:
        addresses = socket.getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )

        return bool(addresses) and all(
            ipaddress.ip_address(address[4][0]).is_global
            for address in addresses
        )

    except (OSError, ValueError):
        return False


class RedirectCheck(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        url = safe_url(url)

        if not url or not public_host(urlparse(url).hostname):
            raise ValueError("Blocked unsafe redirect")

        return super().redirect_request(
            req, fp, code, msg, headers, url
        )


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return

        attrs = dict(attrs)

        key = (
            attrs.get("property")
            or attrs.get("name")
            or ""
        ).lower()

        if key in (
            "og:image",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
        ):
            if attrs.get("content"):
                self.images.append(attrs["content"])


def youtube_image(url):
    parsed = urlparse(url)

    host = (parsed.hostname or "").lower()
    video = None

    if host in ("youtu.be", "www.youtu.be"):
        video = parsed.path.strip("/").split("/")[0]

    elif host in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    ):
        if parsed.path == "/watch":
            video = parse_qs(parsed.query).get("v", [None])[0]

        elif parsed.path.startswith(
            ("/shorts/", "/embed/", "/live/")
        ):
            video = parsed.path.split("/")[2]

    if video and re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        return (
            "https://i.ytimg.com/vi/"
            + video
            + "/hqdefault.jpg"
        )

    return None


def article_image(url):
    url = safe_url(url)

    if not url:
        return None

    host = urlparse(url).hostname

    if (
        host == "reddit.com"
        or host.endswith((".reddit.com", ".redd.it"))
        or not public_host(host)
    ):
        return None

    try:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; HipHopFreshRSS/1.0)"
                ),
                "Accept": "text/html",
            },
        )

        opener = build_opener(RedirectCheck())

        with opener.open(request, timeout=5) as response:
            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "text/html" not in content_type:
                return None

            final_url = response.geturl()
            content = response.read(250000)

            charset = (
                response.headers.get_content_charset()
                or "utf-8"
            )

        parser = Metadata()
        parser.feed(
            content.decode(charset, errors="replace")
        )

        for candidate in parser.images:
            image = safe_url(
                urljoin(final_url, candidate),
                image=True,
            )

            if image:
                return image

    except (
        OSError,
        ValueError,
        UnicodeError,
        LookupError,
    ):
        pass

    return None


def direct_image(post):
    for field in (
        "imageUrl",
        "thumbnailUrl",
        "thumbnail",
        "externalUrl",
    ):
        value = post.get(field)

        if isinstance(value, dict):
            value = value.get("url")

        image = safe_url(value, image=True)

        if image:
            if field != "externalUrl" or urlparse(
                image
            ).path.lower().endswith(
                (".jpg", ".jpeg", ".png", ".webp", ".gif")
            ):
                return image

    galleries = (
        post.get("galleryImagesUrls")
        or post.get("galleryImages")
        or []
    )

    if isinstance(galleries, list):
        for value in galleries:
            if isinstance(value, dict):
                value = (
                    value.get("url")
                    or value.get("imageUrl")
                )

            image = safe_url(value, image=True)

            if image:
                return image

    return None


def posted_at(post):
    fields = (
        "submittedAt",
        "createdAt",
        "createdUtc",
        "created_utc",
        "publishedAt",
        "date",
        "timestamp",
        "created",
    )

    for field in fields:
        value = post.get(field)

        try:
            if isinstance(value, (int, float)):
                seconds = (
                    value / 1000
                    if value > 1e11
                    else value
                )

                return datetime.fromtimestamp(
                    seconds, timezone.utc
                )

            if isinstance(value, str) and value:
                date = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if not date.tzinfo:
                    date = date.replace(
                        tzinfo=timezone.utc
                    )

                return date.astimezone(timezone.utc)

        except (ValueError, OverflowError):
            pass

    return None


def generate(records, feed_filename="feed.xml"):
    if not isinstance(records, list):
        raise ValueError(
            "Expected a JSON list from Apify"
        )

    posts = []
    seen = set()

    for post in records:
        if not isinstance(post, dict):
            continue

        subreddit = (
            str(post.get("subreddit") or "")
            .lower()
            .removeprefix("r/")
            .strip("/")
        )

        title = str(
            post.get("title") or ""
        ).strip()

        link = safe_url(
            post.get("submissionUrl")
            or post.get("url")
            or post.get("permalink")
        )

        ident = str(
            post.get("submissionId") or link
        )

        if (
            subreddit != "hiphopheads"
            or not FRESH.match(title)
            or not link
            or ident in seen
        ):
            continue

        seen.add(ident)

        posts.append(
            (
                post,
                title,
                link,
                ident,
                posted_at(post),
            )
        )

    if not posts:
        raise ValueError(
            "No matching [FRESH] posts. "
            "Refusing to publish an empty feed."
        )

    posts.sort(
        key=lambda post: (
            post[4]
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    metadata = (
        ("title", "HipHop Fresh - r/hiphopheads"),
        ("link", BASE + "/"),
        (
            "description",
            "New [FRESH] releases from r/hiphopheads",
        ),
        ("language", "en"),
        ("ttl", "720"),
    )

    for tag, value in metadata:
        ET.SubElement(channel, tag).text = value

    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {
            "href": BASE + "/" + feed_filename,
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    image_cache = {}

    counts = {
        "reddit": 0,
        "youtube": 0,
        "article": 0,
    }

    for post, title, link, ident, date in posts:
        item = ET.SubElement(channel, "item")

        ET.SubElement(
            item, "title"
        ).text = title

        ET.SubElement(
            item, "link"
        ).text = link

        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "false"},
        ).text = "reddit:hiphopheads:" + ident

        if date:
            ET.SubElement(
                item, "pubDate"
            ).text = format_datetime(date)

        picture = direct_image(post)

        if picture:
            counts["reddit"] += 1

        else:
            outbound = safe_url(
                post.get("externalUrl")
                or post.get("outboundUrl")
                or post.get("linkUrl")
            )

            if outbound:
                picture = youtube_image(outbound)

                if picture:
                    counts["youtube"] += 1

                elif outbound in image_cache:
                    picture = image_cache[outbound]

                elif len(image_cache) < MAX_LOOKUPS:
                    picture = article_image(outbound)
                    image_cache[outbound] = picture

                    if picture:
                        counts["article"] += 1

        description = "<p><b>r/hiphopheads</b></p>"

        if picture:
            ET.SubElement(
                item,
                "{" + NS_MEDIA + "}thumbnail",
                {"url": picture},
            )

            description += (
                '<p><img src="'
                + html.escape(picture, quote=True)
                + '" alt="Cover or preview" /></p>'
            )

        body = re.sub(
            r"\s+",
            " ",
            str(
                post.get("bodyText")
                or post.get("selftext")
                or ""
            ).strip(),
        )[:1400]

        if body:
            description += (
                "<p>"
                + html.escape(body)
                + "</p>"
            )

        description += (
            '<p><a href="'
            + html.escape(link, quote=True)
            + '">Open on Reddit</a></p>'
        )

        ET.SubElement(
            item, "description"
        ).text = description

    print(
        f"Filtered {len(posts)} FRESH posts "
        f"from {len(records)} Apify records"
    )

    print(
        "Thumbnails: "
        + " ".join(
            f"{key}={value}"
            for key, value in counts.items()
        )
    )

    return ET.tostring(
        rss,
        encoding="utf-8",
        xml_declaration=True,
    )


def fetch_records():
    token = os.getenv("APIFY_TOKEN")

    if not token:
        raise RuntimeError(
            "Missing GitHub Actions secret APIFY_TOKEN"
        )

    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "HipHopFreshRSS/1.0",
    }

    def read(url):
        with urlopen(
            Request(url, headers=headers),
            timeout=40,
        ) as response:
            return json.load(response)

    task_id = quote(
        TASK.replace("/", "~"),
        safe="~",
    )

    run = read(
        "https://api.apify.com/v2/actor-tasks/"
        + task_id
        + "/runs/last?status=SUCCEEDED"
    )["data"]

    if (
        run.get("status") != "SUCCEEDED"
        or not run.get("finishedAt")
    ):
        raise RuntimeError(
            "No successful saved-task run found."
        )

    finished = datetime.fromisoformat(
        run["finishedAt"].replace(
            "Z", "+00:00"
        )
    )

    if (
        datetime.now(timezone.utc) - finished
        > timedelta(hours=48)
    ):
        raise RuntimeError(
            "Last successful Apify task run "
            "is over 48 hours old."
        )

    dataset = run.get("defaultDatasetId")

    if not dataset:
        raise RuntimeError(
            "Successful run has no dataset."
        )

    return read(
        "https://api.apify.com/v2/datasets/"
        + quote(dataset, safe="")
        + "/items?format=json&limit=300"
    )


def main():
    if len(sys.argv) > 1:
        records = json.loads(
            Path(sys.argv[1]).read_text(
                encoding="utf-8"
            )
        )
    else:
        records = fetch_records()

    # Generate both RSS feeds from the same Apify data.
    # Each feed gets its own correct self URL.
    feed_xml = generate(
        records,
        feed_filename="feed.xml",
    )

    test_xml = generate(
        records,
        feed_filename="test2.xml",
    )

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Both files are required by the GitHub workflow.
    (OUT / "feed.xml").write_bytes(feed_xml)
    (OUT / "test2.xml").write_bytes(test_xml)

    index_html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HipHop Fresh RSS</title>
</head>
<body>
<h1>HipHop Fresh RSS</h1>
<p><a href="feed.xml">Main RSS Feed</a></p>
<p><a href="test2.xml">Test2 RSS Feed</a></p>
</body>
</html>
"""

    (OUT / "index.html").write_text(
        index_html,
        encoding="utf-8",
    )

    (OUT / ".nojekyll").touch()

    print("Generated:", OUT / "feed.xml")
    print("Generated:", OUT / "test2.xml")
    print("Published successfully.")


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "RSS generation failed:",
            exc,
            file=sys.stderr,
        )
        sys.exit(1)
