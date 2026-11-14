# SDP project schedule template

Planning window: **14 September to 24 November 2026**. Submission is assumed to
be 24 November as requested. This is an editable planning baseline, not a claim
that earlier tasks were completed on these dates. Replace dates, owners and
status after the team confirms actual progress. GitHub renders the chart below.

```mermaid
gantt
    title Bitcoin Agent Exchange schedule from 14 September to 24 November 2026
    dateFormat YYYY-MM-DD
    axisFormat %d %b
    section Research and planning
    Problem definition and scope                 :scope, 2026-09-14, 2026-09-28
    Literature and TradingAgents review           :literature, 2026-09-21, 2026-10-12
    Baseline debate experiments                   :debate_research, 2026-10-05, 2026-10-26
    Proposal and architecture agreement           :proposal, 2026-10-19, 2026-11-02
    section Shared implementation
    Agent contract and archived data              :contract, 2026-10-26, 2026-11-09
    First six agents and Exchange engine          :six, 2026-11-02, 2026-11-13
    Connect six agent demo                        :demo, 2026-11-12, 2026-11-16
    Remaining specialists and debate baseline     :remaining, 2026-11-14, 2026-11-19
    section Student 5
    Macro source and availability policy          :macro_policy, 2026-11-14, 2026-11-15
    World economy agent and macro history         :macro, 2026-11-14, 2026-11-17
    Risk gateway and execution tests              :gateway, 2026-11-14, 2026-11-17
    Contrarian and circuit breaker                :risk_more, 2026-11-16, 2026-11-19
    Security analysis and contribution evidence   :security, 2026-11-17, 2026-11-22
    section Validation and submission
    Full clean replay and integration checks      :clean, 2026-11-18, 2026-11-20
    Repeated attack replay and comparisons        :attacks, 2026-11-19, 2026-11-22
    Statistics and results review                 :stats, 2026-11-21, 2026-11-23
    Report preparation                            :report, 2026-11-14, 2026-11-24
    Final demo rehearsal and submission checks    :rehearsal, 2026-11-23, 2026-11-24
    Submission                                   :milestone, submit, 2026-11-24, 0d
```

| Work package | Owner | Dependency | Status to update | Evidence to link |
| --- | --- | --- | --- | --- |
| Chart and Long-term | Student 1 | Historical BTC prices and forecast contract | Confirm actual status | Commit, tests, sample evidence |
| News, Mood and attack fixtures | Student 2 | Dated articles, sentiment history | Confirm actual status | Commit, clean and attacked samples |
| Exchange, vote, average and runner | Student 3 | Common probability output | Confirm integration | Commit, replay and method outputs |
| Stablecoin and paper execution | Student 4 | Snapshots, final action and approved size | Confirm integration | Commit, simulated trade ledger |
| Macro data and World economy | Student 5 | Dollar/yield history and availability checks | Local implementation for review | Commit after approval, macro tests |
| 25% gateway | Student 5 | Account holdings, quote, execution costs | Local implementation for review | Commit after approval, risk tests |
| Contrarian, circuit breaker, security | Student 5 | Other forecasts, loss/forecast history | Planned, outside this change | Separate implementation and tests |
| Full replay, report and demo | Everyone | Integrated agents, risk and recorded data | Planned | Results, report, rehearsal record |

The 24 November deadline compresses the proposal's original 30 November plan.
Confirm the six-agent integration by 16 November and the full-system scope by
19 November. If the data or remaining specialists are unavailable, record the
scope change explicitly; do not label six-agent results as a 13-agent evaluation.
Dates describe proposed work windows; overlapping bars indicate parallel team
work, not evidence of completion. Status cells are separate from the date bars.
