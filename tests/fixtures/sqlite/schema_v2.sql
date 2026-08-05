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
    content TEXT
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
INSERT INTO articles(id, source, title, link, published, summary, fetched_at, digest_date, category, content)
VALUES
 (1, 'Alpha', 'One', 'https://example.test/1', NULL, 's1', '2026-07-01T01:00:00+00:00', NULL, 'ai', 'full one'),
 (2, 'Beta', 'Two', 'https://example.test/2', NULL, 's2', '2026-07-01T02:00:00+00:00', '2026-07-02', 'agents', 'full two');
INSERT INTO sources(name, fetch_ok, fetch_fail, collected, picked)
VALUES ('Alpha', 7, 1, 12, 2), ('Beta', 4, 3, 9, 1);
