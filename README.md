# AI & Tech News Discord Bot

A Discord slash-command bot that finds the latest announcement from selected AI and technology publishers.

## Run it locally

1. Create a Discord application and bot in the Discord Developer Portal.
2. Invite it to your test server with both the `bot` and `applications.commands` OAuth scopes.
3. Copy `.env.example` to `.env`, add the bot token, and set `DEV_GUILD_ID` to your test server's numeric ID. Enable Discord Developer Mode to copy that ID. Add `OPENAI_API_KEY` to enable AI summaries.
4. Install the packages from `requirements.txt` in a Python virtual environment.
5. Start the bot with:

   ```powershell
   .\.venv\Scripts\python.exe .\main.py
   ```

When `DEV_GUILD_ID` is configured, slash-command updates are registered only in that test server and should appear quickly. Without it, commands are global and Discord may take time to show changes.

## Commands

- `/ai_news [company]` — latest announcement from one tracked source, or all sources.
- `/sources` — lists tracked sources.
- `/status` — confirms the bot is online.
- `/help` — quick usage guidance.

## AI summaries

For each new article, the bot extracts readable article text and asks the OpenAI Responses API for a two-bullet summary. Summary requests are serialized and retried after a rate-limit response. Summaries are cached in `news_cache.sqlite3` for 24 hours, so repeat requests do not create another API call. If no `OPENAI_API_KEY` is present, the article link still works and the bot explains how to enable summaries.

The next product milestone is scheduled posts and per-server channel settings.

## Optional Brave hybrid discovery

Official RSS feeds and site selectors remain the primary sources. Set `BRAVE_SEARCH_API_KEY` in your local, ignored `.env` to include up to three Brave-discovered stories in `/ai_news` without a company argument. Set `BRAVE_NEWS_QUERY`, `BRAVE_FRESHNESS`, and `BRAVE_RESULT_COUNT` to customize discovery. The configured freshness filters search results, but independently verified chronological ordering is not guaranteed.

When normal article extraction fails, the bot optionally asks Brave's LLM Context endpoint for passages. It only uses retrieved passages when the returned article URL matches the requested article URL. The OpenAI Responses API continues to create summaries; summaries are cached for 24 hours. Brave failures do not block official feeds. API tokens stay in the local `.env` and are not committed.

Run unit tests: `python -m unittest discover -s tests -v`.
