# Standalone News and Mood Specialists

This repo contains the contribution package for the Bitcoin News Specialist and Mood Specialist, prepared in a self-contained form without depending on the original TradingAgents project.

## Included

- `student2/agents/news_specialist.py` — filtered evidence-based Bitcoin forecast logic.
- `student2/agents/mood_specialist.py` — Alternative.me Fear & Greed sentiment signal.
- `student2/agents/schemas.py` — shared structured result schema used by both specialists.
- `student2/compat/yahoo_compat.py` — minimal compatibility helpers for article extraction.
- `tests/test_news_specialist.py` — mock validation for filtering and prompt neutrality.
- `tests/test_mood_specialist.py` — mock validation for sentiment output.
- `tests/fixtures/news_specialist_fake_headline/` — synthetic-article experiment fixtures and manifest.

## Run tests

```powershell
cd "C:\Users\Fatema Tariq\Downloads\SDP-1-Agentic-AI-for-Cryptocurreny-Fall-2026"
python -m pytest tests/test_news_specialist.py tests/test_mood_specialist.py -q
```

## Notes

- Synthetic provenance stays in the experiment manifest only.
- The neutral experimental prompt wording preserves the filtered article set without exposing the synthetic label.
- No `.env`, API keys, or secret material are included in this package.

## License

This contribution retains the Apache 2.0 license notice used by the reused project codebase.