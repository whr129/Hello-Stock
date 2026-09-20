from news_agent.ingestion.feeds import parse_feed


class FakeResponse:
    headers = {"content-type": "application/rss+xml"}
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return b"""
        <rss>
          <channel>
            <item>
              <title>Example</title>
              <link>https://example.com/item</link>
              <description>Summary</description>
            </item>
          </channel>
        </rss>
        """


def test_parse_feed_sends_compatible_headers(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["user_agent"] = request.get_header("User-agent")
        captured["accept"] = request.get_header("Accept")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("news_agent.ingestion.feeds.urlopen", fake_urlopen)

    articles = parse_feed("https://example.com/feed.xml", timeout_seconds=7)

    assert captured["user_agent"] == "news-agent/0.1 (market-research RSS reader)"
    assert "application/rss+xml" in captured["accept"]
    assert captured["timeout"] == 7
    assert articles[0].title == "Example"
