# Experimental market-evidence forecasting

## Decision and limits

The strict quote inspector reads a price only from an equivalent contract; it
does not produce an evidence-based forecast. The available daily market is not
equivalent to the requested rolling 24-hour strict-up event. Its working data
collection is retained.

The optional Market Evidence Specialist instead asks an LLM to estimate the
explicit target from that market evidence. This fits the existing News/Mood
LLM-specialist design, but it is NOT an empirically proven improvement and does
not solve missing evidence by formula. It can and should abstain if insufficient.
Do not add this alongside the strict reader as an extra independent vote: it is
an alternative role for the same evidence, not an independent information source.

Every forecast is explicitly an experimental, uncalibrated model estimate.
Valid market windows, thresholds and tie rules can differ from the target; these
differences are disclosed and do not by themselves force abstention. The model
may state forecasting assumptions clearly as assumptions rather than observations.
It must not fabricate observations or mechanically copy the market midpoint.
An estimate may equal the midpoint; numerical equality alone does not show how
the estimate was derived. Model output must include nonblank sections addressing
the requested target, contract differences and assumptions. The structured
checks cannot independently establish that those explanations are truthful.
The program cannot establish the truth of a model's explanation, prevent every
unsupported inference, or demonstrate accuracy through unit tests. No trained
conversion model or calibration dataset is included. If price context is absent,
the prompt discloses the missing spot/opening prices and volatility history.

## Validation

- Reads an actual saved `book_observed` report from the live reader.
- Recomputes the midpoint/depth from its exact raw frame and checks the summary.
- Requires observation time at/before prediction and at most five minutes old.
- Rejects stale/future frames, synthetic flags, ended/inactive markets, wrong rules
  or conflicting metadata. Recorded provenance is caller-attested, not cryptographic proof.
- The threshold-evidence parser supports only the exact saved Binance BTC/USDT
  group rule format, with verified parent/child linkage, threshold, date range,
  Yes/No mapping and a timestamped child book. Other threshold formats require
  independently validated rules and mapping. No live threshold book is saved here.
- Exposes source, endpoint convention, elapsed/remaining window and tie mismatches
  explicitly. Does not reinterpret contract prices as the target probability.
- Requires 24-hour target, explicit reference source/convention, known evidence
  citations, bounded numeric outputs and consistent status/null handling.
- No model is constructed implicitly by the callable/adapter. CLI calls require
  `--live`, `--forecast-now`, and a model name. Existing keys are read from the
  process environment only; no keys or .env contents are printed or archived.
- `--check` never calls Gemini and produces no probability. Historical input is
  allowed only for explicitly labelled check/replay, not the CLI live path.

## Run from the team repository

Use the existing environment with the Student 2 dependencies (including
`google-genai`). The new test file reuses synthetic fixtures from
`tests/test_limitless_live_reader.py`, already installed by the reader patch.

```powershell
python -m pytest tests/test_market_evidence_specialist.py tests/test_limitless_live_reader.py tests/test_betting_odds_specialist.py -q
```

Example OFFLINE check of the previously observed data. This is historical and
makes no model call. The explicit Binance spot-price target below is an example
for the standalone forecaster, not an agreed replacement for the team's CSV target:

```powershell
python -m student2.agents.market_evidence_specialist --observation runs/betting_odds_live/20261114T202306Z-4d13717826/observation.json --prediction-time 2026-11-14T20:24:00Z --price-source binance:BTC/USDT --reference-price-definition spot_price --check
```

For a NEW estimate, obtain a fresh successful market observation with the working
PowerShell collector plus WebSocket reader. Do not reuse the November 14 snapshot.
Then run the following, replacing the observation path and using the model already
configured for this project. This one command explicitly authorizes one potentially
paid Gemini call (no automatic retry):

```powershell
python -m student2.agents.market_evidence_specialist --observation PATH_TO_NEW_OBSERVATION_JSON --forecast-now --price-source binance:BTC/USDT --reference-price-definition spot_price --live --model models/gemini-3.8-flash
```

An environment key must already exist. Neither the example target nor model ID is
silently defaulted by the program. It refuses evidence older than five minutes.
Saves raw observation, normalized features, prompt, result and metadata in a unique
`runs/market_evidence/...` folder. Never describe an offline/mock result as live.
A valid null result is still possible. A valid number is not evidence of accuracy.

## Team interface

`MarketEvidenceAdapter(model_call=...)` exposes `forecast(day, data)` and consumes
`student2_forecast` plus `student2_market_observation`. It returns the existing
p_up/data_missing shape. Registration must use the runner's explicit
`student2_adapters`, `student2_inputs`, and CSV settlement contract inputs so the
runner can check exact target alignment. This alternative is not automatically
registered and must not be counted as another vote alongside Betting Odds.
Historical LLM runs are retrospective experiments, not prospective accuracy proof.
`Student2Adapter("betting_odds", model_client=callable)` uses the same
evidence-based forecast path for a supplied book; the separate strict quote
reader remains available for exact-contract price inspection. The runner permits
this adapter only after its existing settlement-window validation; it continues
to block legacy Betting Odds adapters that might return a raw market price.

The price-context demo saves each Binance HTTP response as a separate raw file,
along with a manifest containing its URL and UTC retrieval time. Python parses
those files into `price_context.json`, preserving case-sensitive JSON keys and
nested arrays. The existing source, price and timestamp checks still run before
any model call; raw response retrieval times are not substituted for trade times.

## Verification

17 new tests passed here with the actual uploaded schema and specialist imports,
synthetic observations, and mocked model calls. The prior full repository suite
was not rerun; the complete current repository is not available here. No paid
calls, live model estimates, accuracy evaluation, commits or pushes were performed.

## Evaluation required before claiming improvement

Preserve predictions before outcomes and settle using the identical price source,
convention and times. Compare Brier score and coverage with 0.5, a simple baseline
and the system without this feature on a held-out period. Reusing historical LLM
knowledge, changing targets mid-evaluation, or counting abstentions as 0.5 would
invalidate that comparison. More features/model complexity need evidence of benefit.
