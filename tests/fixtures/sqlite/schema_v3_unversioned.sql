CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL UNIQUE,
    published TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL,
    digest_date TEXT,
    category TEXT DEFAULT 'devops',
    content TEXT,
    primary_category TEXT,
    topic_key TEXT,
    routed_at TEXT
);
CREATE TABLE sources (
    name TEXT PRIMARY KEY,
    fetch_ok INTEGER DEFAULT 0,
    fetch_fail INTEGER DEFAULT 0,
    collected INTEGER DEFAULT 0,
    picked INTEGER DEFAULT 0
);
CREATE INDEX idx_articles_fetched ON articles(fetched_at);
CREATE INDEX idx_articles_cat ON articles(category);
CREATE INDEX idx_articles_primary_cat ON articles(primary_category);
CREATE INDEX idx_articles_topic_key ON articles(topic_key);
INSERT INTO articles(id, source, title, link, published, summary, fetched_at, digest_date, category, content, primary_category, topic_key, routed_at)
VALUES
 (1, 'Alpha', 'One', 'https://example.test/1', NULL, 's1', '2026-07-01T01:00:00+00:00', NULL, 'ai', 'full one', 'ai', 'one', '2026-07-01T03:00:00+00:00'),
 (2, 'Beta', 'Two', 'https://example.test/2', NULL, 's2', '2026-07-01T02:00:00+00:00', '2026-07-02', 'agents', 'full two', 'agents', 'two', '2026-07-01T04:00:00+00:00');
INSERT INTO sources(name, fetch_ok, fetch_fail, collected, picked)
VALUES ('Alpha', 7, 1, 12, 2), ('Beta', 4, 3, 9, 1);
