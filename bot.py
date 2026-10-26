"""News-source configuration for AI & Tech News."""

SOURCES = {
    "openai": {"url": "https://openai.com/news/rss.xml", "feed": True, "name": "OpenAI"},
    "anthropic": {"url": "https://www.anthropic.com/news", "selector": "main a[href^='/news/']", "name": "Anthropic"},
    "google_deepmind": {"url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "feed": True, "name": "Google DeepMind"},
    "proton": {"url": "https://proton.me/blog/feed", "feed": True, "name": "Proton"},
    "meta_ai": {"url": "https://ai.meta.com/blog/", "selector": "a[href*='/blog/']", "skip_titles": ["FEATURED"], "name": "Meta AI"},
}
