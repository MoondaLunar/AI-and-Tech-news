"""Discord bot that shares recent AI and technology announcements."""

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib import response
from urllib.parse import urljoin
from xml.etree import ElementTree

import discord
import httpx
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from bot import SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("ai_tech_news")
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
CACHE_PATH = Path(__file__).with_name("news_cache.sqlite3")
CACHE_TTL_SECONDS = 24 * 60 * 60
SUMMARY_SEMAPHORE = asyncio.Semaphore(1)
ARTICLE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class NewsItem:
    source_name: str
    title: str
    url: str
    error: Optional[str] = None
    summary: Optional[str] = None
    summary_stage: Optional[str] = None


@dataclass
class ArticleTextResult:
    text: Optional[str]
    stage: str
    detail: str


@dataclass
class SummaryResult:
    text: Optional[str]
    stage: str
    detail: str


class NewsBot(commands.Bot):
    async def setup_hook(self) -> None:
        if DEV_GUILD_ID:
            try:
                guild = discord.Object(id=int(DEV_GUILD_ID))
            except ValueError as error:
                raise RuntimeError("DEV_GUILD_ID must be a numeric Discord server ID.") from error
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s command(s) to development server %s.", len(synced), DEV_GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global command(s). Global updates can take time to appear.", len(synced))


intents = discord.Intents.default()
bot = NewsBot(command_prefix="!", intents=intents)


async def fetch_latest(company_key: str) -> NewsItem:
    source = SOURCES[company_key]
    try:
        timeout = httpx.Timeout(15.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=ARTICLE_HEADERS) as client:
            response = await client.get(source["url"])
            response.raise_for_status()
        if source.get("feed"):
            root = ElementTree.fromstring(response.content)
            entry = root.find(".//item")
            title = entry.findtext("title", default="").strip() if entry is not None else ""
            href = entry.findtext("link", default="").strip() if entry is not None else ""
            if not title or not href:
                return NewsItem(source["name"], "", "", "No matching recent post was found.")
            return NewsItem(source["name"], title, urljoin(source["url"], href))
        soup = BeautifulSoup(response.text, "html.parser")
        elements = soup.select(source["selector"])
        skipped = {title.casefold() for title in source.get("skip_titles", [])}
        element = next(
            (candidate for candidate in elements if candidate.get_text(" ", strip=True).casefold() not in skipped),
            None,
        )
        if not element:
            return NewsItem(source["name"], "", "", "No matching recent post was found.")
        link_tag = element if element.name == "a" else element.select_one("a[href]")
        href = link_tag.get("href") if link_tag else None
        if not href:
            return NewsItem(source["name"], "", "", "The latest post did not include a link.")
        heading = element.find(["h2", "h3"])
        title = heading.get_text(" ", strip=True) if heading else element.get_text(" ", strip=True)
        return NewsItem(source["name"], title, urljoin(source["url"], href))
    except httpx.HTTPError as error:
        logger.warning("Could not fetch %s: %s", source["name"], error)
        return NewsItem(source["name"], "", "", "That source is temporarily unavailable.")
    except Exception:
        logger.exception("Unexpected error while fetching %s", source["name"])
        return NewsItem(source["name"], "", "", "That source could not be read right now.")


def get_cached_summary(url: str) -> Optional[str]:
    with sqlite3.connect(CACHE_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS summaries (url TEXT PRIMARY KEY, summary TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        row = connection.execute(
            "SELECT summary, created_at FROM summaries WHERE url = ?", (url,)
        ).fetchone()
    if row and time.time() - row[1] < CACHE_TTL_SECONDS:
        return row[0]
    return None


def cache_summary(url: str, summary: str) -> None:
    with sqlite3.connect(CACHE_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS summaries (url TEXT PRIMARY KEY, summary TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO summaries (url, summary, created_at) VALUES (?, ?, ?)",
            (url, summary, time.time()),
        )


async def fetch_article_text(url: str) -> ArticleTextResult:
    """Extract readable paragraphs from a linked article without storing its full text."""
    try:
        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "AI-and-Tech-News-Discord-Bot/1.0"}) as client:
            response = await client.get(url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.select("script, style, nav, header, footer, aside, form"):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.body
        if not container:
            return ArticleTextResult(None, "extraction_container_missing", "No article, main, or body element was found.")
        paragraphs = [paragraph.get_text(" ", strip=True) for paragraph in container.select("p")]
        text = "\n\n".join(paragraph for paragraph in paragraphs if len(paragraph) >= 40)
        if not text:
            return ArticleTextResult(None, "extraction_no_readable_paragraphs", "No paragraphs of at least 40 characters were found.")
        return ArticleTextResult(text[:12_000], "extraction_success", f"Extracted {len(text)} characters before truncation.")
    except httpx.HTTPStatusError as error:
        logger.warning("Article fetch returned HTTP %s for %s", error.response.status_code, url)
        return ArticleTextResult(None, f"extraction_http_{error.response.status_code}", "The article server rejected or could not fulfill the request.")
    except httpx.HTTPError as error:
        logger.warning("Could not fetch article text from %s: %s", url, error)
        return ArticleTextResult(None, "extraction_network_error", "The article could not be fetched due to a network error.")
    except Exception:
        logger.exception("Unexpected article extraction error for %s", url)
        return ArticleTextResult(None, "extraction_parse_error", "The article page could not be parsed.")


def response_text(payload: dict) -> Optional[str]:
    """Read text from the Responses API's JSON representation."""
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"].strip()
    return None


async def create_summary(title: str, article_text: str) -> SummaryResult:
    if not OPENAI_API_KEY:
        return SummaryResult(None, "llm_disabled", "OPENAI_API_KEY is not configured.")
    prompt = (
        "Summarize the following AI or technology news article for a Discord audience. "
        "Use only information in the article. Write 2 concise bullet points, no more than 90 words total. "
        "Do not add a heading, a URL, or speculation.\n\n"
        f"Article title: {title}\n\nArticle text:\n{article_text}"
    )
    try:
        async with SUMMARY_SEMAPHORE:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                for attempt in range(3):
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                        json={"model": OPENAI_MODEL, "input": prompt, "store": False},
                    )
                    if response.status_code == 429:
                        logger.warning(
                            "OpenAI 429 response: headers=%s body=%s",
                            dict(response.headers),
                            response.text,
                        )
                        retry_after = response.headers.get("retry-after")
                        try:
                            delay = min(float(retry_after), 15.0) if retry_after else 2 ** attempt
                        except ValueError:
                            delay = 2 ** attempt
                        logger.warning(
                            "Summary request rate-limited; retrying in %s seconds (attempt %s/3).",
                            delay,
                            attempt + 1,
                        )
                        await asyncio.sleep(delay)
                        continue

                    response.raise_for_status()
                    text = response_text(response.json())
                    if not text:
                        return SummaryResult(
                            None,
                            "llm_invalid_response",
                            "The API response did not contain summary text.",
                        )
                    return SummaryResult(text, "llm_success", "Summary generated successfully.")

        return SummaryResult(None, "llm_rate_limited", "The summary API rate-limited three attempts. Try again shortly.")
    except httpx.HTTPStatusError as error:
        logger.warning("Summary request returned HTTP %s", error.response.status_code)
        return SummaryResult(None, f"llm_http_{error.response.status_code}", "The summary provider rejected or could not fulfill the request.")
    except httpx.HTTPError as error:
        logger.warning("Summary request failed: %s", error)
        return SummaryResult(None, "llm_network_error", "The summary provider could not be reached.")
    except ValueError:
        logger.warning("Summary request returned invalid JSON")
        return SummaryResult(None, "llm_invalid_response", "The summary provider returned invalid data.")


async def add_summary(item: NewsItem) -> NewsItem:
    if item.error:
        item.summary_stage = "source_fetch_error"
        return item
    cached = get_cached_summary(item.url)
    if cached:
        item.summary = cached
        item.summary_stage = "summary_cache_hit"
        logger.info("Summary stage=summary_cache_hit source=%s url=%s", item.source_name, item.url)
        return item
    extraction = await fetch_article_text(item.url)
    logger.info(
        "Summary stage=%s source=%s url=%s detail=%s",
        extraction.stage,
        item.source_name,
        item.url,
        extraction.detail,
    )
    if not extraction.text:
        item.summary_stage = extraction.stage
        item.summary = f"Article extraction failed (`{extraction.stage}`). {extraction.detail}"
        return item
    generated = await create_summary(item.title, extraction.text)
    logger.info(
        "Summary stage=%s source=%s url=%s detail=%s",
        generated.stage,
        item.source_name,
        item.url,
        generated.detail,
    )
    item.summary_stage = generated.stage
    if generated.text:
        item.summary = generated.text
        cache_summary(item.url, generated.text)
    elif generated.stage == "llm_disabled":
        item.summary = "Add `OPENAI_API_KEY` to `.env` to enable AI-generated article summaries."
    else:
        item.summary = f"AI summary generation failed (`{generated.stage}`). {generated.detail}"
    return item


def news_embed(item: NewsItem) -> discord.Embed:
    if item.error:
        return discord.Embed(title=f"Latest from {item.source_name}", description=f"⚠️ {item.error}", colour=discord.Colour.orange())
    title = item.title[:250] + ("…" if len(item.title) > 250 else "")
    summary = item.summary or "Preparing article summary…"
    embed = discord.Embed(title=f"Latest from {item.source_name}", description=f"**[{title}]({item.url})**\n\n{summary[:3_600]}", colour=discord.Colour.blurple(), url=item.url)
    stage = item.summary_stage or "not_started"
    embed.set_footer(text=f"AI & Tech News • Summary stage: {stage} • Cache: 24 hours")
    return embed


async def company_autocomplete(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    return [app_commands.Choice(name=source["name"], value=key) for key, source in SOURCES.items() if current in key or current in source["name"].lower()][:25]


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.tree.command(name="ai_news", description="Get the latest announcement from one source or all sources.")
@app_commands.describe(company="Choose a source. Leave blank to see every source.")
@app_commands.autocomplete(company=company_autocomplete)
async def ai_news(interaction: discord.Interaction, company: Optional[str] = None) -> None:
    await interaction.response.defer(thinking=True)
    if company:
        await interaction.followup.send(embed=news_embed(await add_summary(await fetch_latest(company))))
        return
    items = await asyncio.gather(*(fetch_latest(key) for key in SOURCES))
    items = await asyncio.gather(*(add_summary(item) for item in items))
    await interaction.followup.send("Here are the latest items from the sources I track:")
    for item in items:
        await interaction.followup.send(embed=news_embed(item))


@bot.tree.command(name="sources", description="See the publications this bot currently tracks.")
async def sources(interaction: discord.Interaction) -> None:
    names = "\n".join(f"• **{source['name']}** — {source['url']}" for source in SOURCES.values())
    await interaction.response.send_message(f"I currently track:\n{names}", ephemeral=True)


@bot.tree.command(name="status", description="Check whether the bot is online and configured.")
async def status(interaction: discord.Interaction) -> None:
    mode = "development-server sync" if DEV_GUILD_ID else "global sync"
    await interaction.response.send_message(f"✅ Online. Tracking {len(SOURCES)} sources. Command mode: {mode}.", ephemeral=True)


@bot.tree.command(name="help", description="Learn how to use AI & Tech News.")
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Use `/ai_news` for all sources, or choose one from its autocomplete list. Use `/sources` to see all publications.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env before starting the bot.")
    bot.run(TOKEN, log_handler=None)
