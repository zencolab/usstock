# Enable the hourly GitHub Actions workflow

The connected GitHub token can write repository files but cannot create files under the protected root `.github/workflows/` directory.

To enable scheduling, copy:

```text
hourly_news_bot/hourly-bilingual-news.workflow.yml
```

to:

```text
.github/workflows/hourly-bilingual-news.yml
```

Then configure the repository Secrets and Variables listed in `hourly_news_bot/README.md`. Set `HOURLY_NEWS_ENABLED=true` to enable the hourly schedule. Manual `workflow_dispatch` runs are allowed regardless of this variable.
