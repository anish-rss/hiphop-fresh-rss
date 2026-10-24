# HipHop Fresh RSS

A single RSS feed for release posts from `r/hiphopheads`, including `[FRESH]`, `[FRESH ALBUM]`, `[FRESH VIDEO]`, `[FRESH EP]` and other `[FRESH ...]` variants.

- GitHub: `anish-rss/hiphop-fresh-rss`
- Apify account: `fascinated_cleverness` (Martin)
- Saved task: `fascinated_cleverness/Reddit-Scraper-Pro-task`
- Actor: <https://apify.com/coding-doctor-omar/reddit-scraper-pro>
- RSS after deployment: <https://anish-rss.github.io/hiphop-fresh-rss/feed.xml>

## Setup

1. Upload `generate_rss.py` and this `README.md` at the repository root. Create `.github/workflows/update-rss.yml` exactly at that path. GitHub's browser upload may not preserve a leading-dot folder; use Add file > Create new file and enter `.github/workflows/update-rss.yml` if necessary.
2. Set GitHub repo secret `APIFY_TOKEN` to a token from the **Martin** Apify account. Never commit tokens.
3. In Settings > Pages, choose **GitHub Actions** as the build/deployment source.
4. In Apify **Tasks**, run `fascinated_cleverness/Reddit-Scraper-Pro-task` once and verify success. Prior actor-only runs do **not** count as saved-task runs.
5. In Actions > Publish HipHop Fresh RSS > Run workflow, trigger manually. Verify successful publication, then open the RSS link.
6. Configure twice-daily Apify scheduling and update the GitHub workflow `schedule` only after checking run costs and timing. GitHub Actions cron uses UTC by default.

## Filters

The generator retains only posts where `subreddit` is `r/hiphopheads` and the title begins with `[FRESH]` or `[FRESH something]` (case insensitive). It retrieves the **full** Apify dataset, never `clean=1`, and attempts Reddit, YouTube, then article preview thumbnails. Plain discussion posts are excluded.

## Costs and operational notes

GitHub Actions fetches an existing Apify dataset; it does not launch a new scrape. The 75-result actor test cost about US$0.020, but future costs may vary. A later workflow scheduling step must follow Apify's saved task run (not precede it). The feed GUID remains stable for Feedly updates. Feedly may cache older entries without thumbnails; for a new test use `feed.xml?test=2` as a separate subscription if Feedly accepts it.
