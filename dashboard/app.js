'use strict';
var portfolioValuation = null,
  portfolioFetchError = false;
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
const roles = [
  ['Chart', 'chart', 'Technical trends', 'agent-chart'],
  ['Borrowing', 'borrowing', 'Funding & open interest', 'agent-borrowing'],
  ['Network', 'network', 'On-chain activity', 'agent-network'],
  ['Miner', 'miner', 'Hash rate & fees', 'agent-miner'],
  ['Stablecoin', 'stablecoin', 'Liquidity & peg stability', 'agent-stablecoin'],
  ['ETF', 'etf', 'Institutional flows', 'agent-etf'],
  ['News', 'news', 'News & events', 'agent-news'],
  ['Mood', 'mood', 'Market sentiment', 'agent-mood'],
  ['World economy', 'world-economy', 'Dollar & Treasury yields', 'agent-world-economy'],
  ['Betting odds', 'betting', 'Prediction-market evidence', 'agent-betting'],
  ['Crash', 'crash', 'Tail risk & drawdown', 'agent-crash'],
  ['Long-term', 'long-term', 'Long-horizon structure', 'agent-long-term'],
  ['Contrarian', 'contrarian', 'Consensus reversal', 'agent-contrarian'],
];
let state = null,
  market = { candles: [] },
  signature = '',
  marketVersion = null,
  marketFetching = false,
  filter = 'all',
  search = '',
  range = 90,
  chartStyle = 'candles',
  showEma = true,
  hover = null,
  plot = null,
  replayVersion = null;
const percent = (v) =>
  typeof v === 'number' && Number.isFinite(v) ? (v * 100).toFixed(1) + '%' : '—';
const num = (v, places = 2) =>
  typeof v === 'number' && Number.isFinite(v)
    ? v.toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places })
    : '—';
const money = (v) =>
  v !== null && v !== undefined && Number.isFinite(Number(v)) ? '$' + num(Number(v)) : '—';
const utc = (v) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.valueOf())
    ? '—'
    : d.toLocaleString('en-GB', {
        timeZone: 'UTC',
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }) + ' UTC';
};
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function agentIcon(role) {
  const spec = roles.find((r) => r[0] === role);
  if (!spec) return null;
  const icon = node('span', undefined, 'agent-icon ' + spec[3]);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', '#i-agent-' + spec[1]);
  svg.append(use);
  icon.append(svg);
  return icon;
}
function setText(id, text) {
  $(id).textContent = text;
}
function toast(text) {
  setText('toast', text);
  $('toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($('toast').hidden = true), 5500);
}
async function post(path, body) {
  const r = await fetch('/api/' + path, {
    method: 'POST',
    headers: { 'X-Demo-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let result;
  try {
    result = await r.json();
  } catch {
    throw new Error('The local service could not complete this request.');
  }
  if (!r.ok) throw new Error(result.error || 'Request failed');
  return result;
}
async function refreshMarket() {
  if (marketFetching) return;
  marketFetching = true;
  try {
    const r = await fetch('/api/market');
    if (!r.ok) throw new Error();
    market = await r.json();
    marketVersion = state?.sources?.btc_prices?.received_at ?? null;
    hover = null;
    renderMarket();
    drawPrice();
  } catch {
    toast('Chart data could not be loaded from the local service.');
  } finally {
    marketFetching = false;
  }
}
async function refreshState() {
  try {
    const r = await fetch('/api/state');
    if (!r.ok) throw new Error();
    const next = await r.json(),
      sig = JSON.stringify(next);
    state = next;
    if (sig !== signature) {
      signature = sig;
      render(next);
    }
    const version = next.sources?.btc_prices?.received_at ?? null;
    if (version !== marketVersion || !refresh.initialMarket) {
      refresh.initialMarket = true;
      await refreshMarket();
    }
    $('connection').replaceChildren(node('i'), document.createTextNode('Local service connected'));
  } catch {
    $('connection').replaceChildren(node('i'), document.createTextNode('Service disconnected'));
    $('connection').style.color = '#ed929f';
    setText('status-text', 'Local service disconnected. Restart python -m dashboard to reconnect.');
  }
}
let refreshTask = null,
  refreshTimer = null;
async function refresh() {
  if (refreshTask) return refreshTask;
  clearTimeout(refreshTimer);
  refreshTask = refreshState();
  try {
    await refreshTask;
  } finally {
    refreshTask = null;
    refreshTimer = setTimeout(refresh, state?.busy ? 200 : 2000);
  }
}
function duration(ms) {
  return typeof ms === 'number'
    ? ms < 1
      ? ms.toFixed(2) + ' ms'
      : ms < 1000
        ? ms.toFixed(1) + ' ms'
        : (ms / 1000).toFixed(2) + ' s'
    : '—';
}
let activitySignature = '';
function renderActivity(activity) {
  const a = activity ?? {
    status: 'idle',
    phase: 'Ready for a forecast',
    agents: [],
    events: [],
    evaluated_count: 0,
  };
  const statusNames = {
    idle: 'READY',
    running: 'IN PROGRESS',
    completed: 'COMPLETE',
    failed: 'INTERRUPTED',
  };
  setText('activity-state', statusNames[a.status] ?? 'READY');
  $('activity-state').className = 'label activity-label ' + a.status;
  setText('activity-phase', a.phase);
  $('activity-indicator').className = 'activity-indicator ' + a.status;
  setText('activity-time', a.status === 'idle' ? '—' : duration(a.elapsed_ms));
  setText(
    'activity-caption',
    a.status === 'idle'
      ? 'All 13 agents are queued until you generate a forecast.'
      : a.evaluated_count +
          ' of 13 evaluated · ' +
          (a.forecasted_count ?? 0) +
          ' forecasts · ' +
          (a.abstained_count ?? 0) +
          ' abstentions' +
          (a.failed_count ? ' · ' + a.failed_count + ' failures' : ''),
  );
  $('activity-progress-fill').style.width = (a.evaluated_count / 13) * 100 + '%';
  $('activity-progress').setAttribute('aria-valuenow', String(a.evaluated_count));
  const sig = JSON.stringify([
    a.run_id,
    a.status,
    a.agents,
    a.events,
    state?.busy,
    state?.gemini_configured,
    state?.sources?.betting_odds,
  ]);
  if (sig === activitySignature) return;
  activitySignature = sig;
  const grid = $('activity-agents');
  grid.replaceChildren();
  const labels = {
    queued: 'Queued',
    running: 'Evaluating',
    forecasted: 'Forecast returned',
    abstained: 'Abstained',
    failed: 'Failed',
    not_run: 'Not reached',
  };
  roles.forEach(([role, glyph, , color]) => {
    const record = a.agents.find((x) => x.role === role) ?? { role, status: 'queued' },
      card = node('div', undefined, 'activity-agent ' + color + ' ' + record.status);
    const top = node('div', undefined, 'activity-agent-top');
    top.append(agentIcon(role), node('strong', role));
    const stamp = node('span', labels[record.status] ?? record.status, 'activity-agent-status');
    const value = node('div', undefined, 'activity-agent-value');
    value.append(
      node(
        'span',
        typeof record.p_up === 'number'
          ? percent(record.p_up) + ' P(up)'
          : record.status === 'running'
            ? 'Evaluating evidence…'
            : record.status === 'abstained'
              ? 'No usable forecast'
              : '—',
      ),
      node('small', duration(record.duration_ms ?? record.running_ms)),
    );
    card.append(top, stamp, value);
    const connectGemini = !state?.gemini_configured && requiresGemini(record.summary);
    const summary = forecastReason(role, record.summary);
    if (summary) {
      card.title = summary;
      card.append(node('p', summary, 'activity-agent-reason'));
    }
    if (connectGemini) {
      const connect = node('button', 'Connect Gemini', 'report-button');
      connect.disabled = !!state?.busy;
      connect.addEventListener('click', openGeminiDialog);
      card.append(connect);
    }
    if (record.result) {
      const button = node('button', 'View returned evidence ↗', 'report-button');
      button.addEventListener('click', () =>
        openEvidence(role, roles.find((r) => r[0] === role)[2], record.result),
      );
      card.append(button);
    }
    grid.append(card);
  });
  const methodBox = $('activity-methods');
  methodBox.replaceChildren();
  if (a.methods) {
    for (const [key, title] of [
      ['exchange', 'Agent Exchange'],
      ['average', 'Simple average'],
      ['vote', 'Majority vote'],
    ]) {
      const item = node('div');
      item.append(node('span', title), node('strong', percent(a.methods[key])));
      methodBox.append(item);
    }
  }
  const list = $('activity-log'),
    atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 45;
  list.replaceChildren();
  const events = a.events ?? [];
  if (!events.length)
    list.append(node('li', 'Execution events will appear here.', 'activity-empty'));
  for (const event of events) {
    const item = node('li', undefined, event.kind),
      time = node('time', '+' + duration(event.elapsed_ms)),
      body = node('div');
    const name = node('strong', undefined, 'activity-event-name'),
      icon = agentIcon(event.role);
    if (icon) name.append(icon);
    name.append(
      node(
        'span',
        event.role ?? (event.kind.startsWith('aggregation') ? 'Agent Exchange' : 'Forecast run'),
      ),
    );
    body.append(name, node('span', event.message));
    if (event.duration_ms !== undefined)
      body.append(
        node(
          'small',
          labels[event.status] +
            ' · ' +
            duration(event.duration_ms) +
            (typeof event.p_up === 'number' ? ' · ' + percent(event.p_up) + ' P(up)' : ''),
        ),
      );
    item.append(time, body);
    list.append(item);
  }
  if (atBottom) list.scrollTop = list.scrollHeight;
  setText('activity-event-count', events.length + ' events');
  setText(
    'activity-summary',
    a.status === 'completed'
      ? 'All specialists evaluated. Only validated forecasts contribute; the 24-hour outcome is still pending.'
      : a.status === 'failed'
        ? 'This run stopped before completion. Unreached agents are not presented as completed.'
        : a.current_agent === 'Contrarian'
          ? 'Contrarian reads the completed peer forecasts and runs last.'
          : a.status === 'running'
            ? 'Specialists run sequentially; fast numerical agents may finish between screen updates. Their events and timings stay visible.'
            : 'Queued → evaluate evidence → validate forecast → combine.',
  );
}

function render(s) {
  renderActivity(s.activity);
  renderLiveCoins(s);
  setText('status-text', s.message);
  $('status').classList.toggle('busy', s.busy);
  setText('notice-end', s.busy ? 'WORKING' : 'PAPER MODE');
  document.querySelectorAll('[data-action]').forEach((b) => {
    const a = b.dataset.action;
    b.disabled =
      s.busy ||
      (['paper', 'debate'].includes(a) && !s.forecast) ||
      (a === 'debate' && !s.gemini_configured) ||
      (a === 'real-replay' && !s.sources?.btc_prices);
  });
  document.querySelectorAll('[data-open-key]').forEach((b) => (b.disabled = s.busy));
  const f = s.forecast,
    p = f?.methods?.exchange ?? null,
    count = f?.active_count ?? s.activity?.forecasted_count ?? 0;
  setText('prob', percent(p));
  setText('summary-prob', percent(p));
  setText('strip-coverage', count + ' / 13');
  setText('active-count', count);
  $('count').replaceChildren(document.createTextNode(String(count) + ' '), node('small', '/ 13'));
  setText('filter-active-count', count);
  setText(
    'filter-missing-count',
    f ? 13 - count : (s.activity?.abstained_count ?? 0) + (s.activity?.failed_count ?? 0),
  );
  setText(
    'agent-summary',
    s.activity?.status === 'running'
      ? s.activity.evaluated_count + ' / 13 EVALUATED'
      : f || s.activity?.status === 'failed'
        ? count +
          ' ACTIVE · ' +
          (s.activity?.abstained_count ?? 0) +
          ' ABSTAINED · ' +
          (s.activity?.failed_count ?? 0) +
          ' FAILED'
        : '13 QUEUED',
  );
  $('coverage-fill').style.width = (count / 13) * 100 + '%';
  const dots = $('coverage-dots');
  dots.replaceChildren(...roles.map((_, i) => node('i', undefined, i < count ? 'on' : '')));
  $('gauge-arc').setAttribute('stroke-dasharray', p === null ? '0 100' : p * 100 + ' 100');
  const bias =
    p === null
      ? f
        ? 'No usable forecast'
        : 'Awaiting forecast'
      : p > 0.5
        ? 'Upward bias'
        : p < 0.5
          ? 'Downward bias'
          : 'Neutral outlook';
  setText('bias', bias);
  $('bias').className =
    'signal-pill' + (p === null ? '' : p > 0.5 ? ' up' : p < 0.5 ? ' down' : '');
  setText(
    'summary-signal',
    p === null
      ? f
        ? 'No validated forecasts available'
        : 'Run a forecast to reveal the signal'
      : bias + ' · experimental probability',
  );
  setText(
    'coverage-note',
    !f
      ? 'Evidence determines participation'
      : f.cap_feasible
        ? '20% influence cap is feasible'
        : 'Displayed forecast: fewer than 5 active · BUY blocked',
  );
  for (const method of ['exchange', 'average', 'vote'])
    setText('method-' + method, percent(f?.methods?.[method]));
  const window = $('forecast-window').querySelector('span');
  window.textContent = f
    ? 'Target: ' + utc(f.contract.target_time) + ' · strict higher price'
    : 'Outcome available 24 hours after forecast.';
  setText(
    'cap-state',
    f
      ? f.cap_feasible
        ? 'Feasible for displayed forecast'
        : 'Infeasible: forecast BUY blocked'
      : 'Requires ≥ 5 active',
  );
  const configured = s.gemini_configured;
  setText('key-label', configured ? 'Gemini configured' : 'Connect Gemini');
  setText(
    'model-state',
    configured
      ? s.gemini?.source === 'session'
        ? 'Session key configured'
        : 'Environment key configured'
      : 'Session key not configured',
  );
  renderKeyStatus();
  renderAgents();
  renderSources();
  renderMarket();
  renderPaperSettings();
  renderPaper();
  renderPortfolio();
  setText(
    'contract',
    f
      ? JSON.stringify(
          {
            contract: f.contract,
            reference: f.quote,
            methods: f.methods,
            weights: f.weights,
            coins: f.coins,
            coins_basis: f.coins_basis,
            live_learning: s.learning,
            limitations: f.limitations,
            usage: f.usage,
            debate: s.debate,
          },
          null,
          2,
        )
      : 'Generate a forecast to inspect its event definition.',
  );
  if (s.replay && replayVersion !== s.replay.output) {
    replayVersion = s.replay.output;
    renderReplay();
  }
  drawReplay();
}
function renderMarket() {
  const bars = market.candles || [],
    last = bars.at(-1),
    prior = bars.at(-2),
    f = state?.forecast;
  const price = f?.quote?.price ?? last?.close ?? null;
  $('btc-price').replaceChildren(
    document.createTextNode(price === null ? '—' : num(price)),
    node('small', 'USDT'),
  );
  let change = null,
    basis = 'Waiting for market data';
  if (f && last) {
    change = (f.quote.price / last.close - 1) * 100;
    basis = 'since last daily close';
  } else if (last && prior) {
    change = (last.close / prior.close - 1) * 100;
    basis = 'last completed UTC day';
  }
  setText(
    'price-change',
    change === null ? basis : (change >= 0 ? '↗ +' : '↘ ') + num(change) + '% ' + basis,
  );
  $('price-change').className = change === null ? '' : change >= 0 ? 'positive' : 'negative';
  setText(
    'price-basis',
    f
      ? 'Reference ' + utc(f.quote.bar_close_time)
      : last
        ? 'Closed ' + last.date
        : 'No observation yet',
  );
  setText('daily-high', last?.high !== undefined ? num(last.high) : '—');
  setText('daily-low', last?.low !== undefined ? 'Low ' + num(last.low) : 'Low —');
  setText('daily-volume', last?.volume !== undefined ? num(last.volume, 0) + ' BTC' : '—');
  $('chart-empty').hidden = bars.length > 0;
  setText('chart-date', last ? 'Last candle · ' + last.date + ' UTC' : 'No candle history loaded');
  readout(last);
  $('candles-button').disabled =
    bars.length > 0 && !bars.every((b) => b.high !== undefined && b.low !== undefined);
}
function readout(bar) {
  const r = $('chart-readout');
  if (!bar) {
    r.replaceChildren(
      document.createTextNode('Completed UTC daily candles '),
      node('span', '· Refresh market data to begin'),
    );
    return;
  }
  r.replaceChildren(
    node('span', bar.date + '  '),
    document.createTextNode(
      'O ' +
        num(bar.open) +
        '  ' +
        (bar.high !== undefined ? 'H ' + num(bar.high) + '  L ' + num(bar.low) + '  ' : '') +
        'C ' +
        num(bar.close),
    ),
    node('span', '  · 1D / UTC'),
  );
}
function renderAgents() {
  const f = state?.forecast,
    body = $('agent-rows');
  body.replaceChildren();
  let shown = 0;
  roles.forEach(([role, glyph, perspective, color], i) => {
    const progress = state?.activity?.agents?.find((a) => a.role === role),
      a = f?.agents.find((a) => a.role === role) ?? progress?.result,
      active = !!a && a.p_up !== null && a.p_up !== undefined;
    const queued = !a && (!progress || ['queued', 'not_run'].includes(progress.status)),
      running = progress?.status === 'running';
    if (
      (filter === 'active' && !active) ||
      (filter === 'missing' && (active || running || queued)) ||
      (search && !(role + ' ' + perspective).toLowerCase().includes(search))
    )
      return;
    shown++;
    const row = node('tr');
    row.append(node('td', String(i + 1).padStart(2, '0')));
    const name = node('td'),
      wrap = node('div', undefined, 'agent-name');
    wrap.append(agentIcon(role), node('span', role));
    name.append(wrap);
    row.append(name, node('td', perspective, 'agent-perspective'));
    const direction = !active
        ? '—'
        : a.p_up > 0.5
          ? 'Bullish'
          : a.p_up < 0.5
            ? 'Bearish'
            : 'Neutral',
      signal = node('td');
    signal.append(
      node(
        'span',
        direction,
        'signal ' + (!active ? 'missing' : a.p_up > 0.5 ? 'up' : a.p_up < 0.5 ? 'down' : 'neutral'),
      ),
    );
    row.append(signal);
    const prob = node('td'),
      group = node('div', undefined, 'prob-cell'),
      bar = node('span', undefined, 'prob-bar'),
      fill = node('i');
    fill.style.width = active ? a.p_up * 100 + '%' : '0%';
    bar.append(fill);
    group.append(node('strong', percent(a?.p_up)), bar);
    prob.append(group);
    row.append(prob, node('td', active ? percent(f?.weights?.[a.agent]) : '—'));
    const status = node('td'),
      label = node('span', undefined, 'status-cell' + (active ? ' active' : ''));
    label.append(
      node('i'),
      document.createTextNode(
        running
          ? 'Evaluating'
          : progress?.status === 'not_run'
            ? 'Not reached'
            : queued
              ? 'Queued'
              : active
                ? 'Active'
                : progress?.status === 'failed'
                  ? 'Failed'
                  : 'Abstaining',
      ),
    );
    status.append(label);
    row.append(status);
    const evidence = node('td'),
      button = node('button', a ? 'View report ↗' : 'Requirements ↗', 'report-button');
    button.addEventListener('click', () => openEvidence(role, perspective, a));
    evidence.append(button);
    row.append(evidence);
    body.append(row);
  });
  if (!shown) {
    const row = node('tr'),
      cell = node('td', 'No specialists match this filter.', 'empty-inline');
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
  }
  setText(
    'table-caption',
    f
      ? shown + ' specialists shown · forecast ' + utc(f.created_at)
      : state?.activity?.status === 'running'
        ? shown + ' specialists shown · results arrive as each returns'
        : shown + ' registered specialists · no forecast yet',
  );
}
function openEvidence(role, perspective, a) {
  $('evidence-title').replaceChildren(agentIcon(role), node('span', role));
  const body = $('evidence-body');
  body.replaceChildren(node('p', perspective));
  const summary = node('div', undefined, 'evidence-summary');
  summary.append(
    node('span', 'P(up) ' + percent(a?.p_up)),
    node(
      'span',
      a?.audit?.passed
        ? 'Evidence checks passed'
        : a
          ? 'No validated forecast'
          : 'Not yet evaluated',
    ),
  );
  body.append(summary);
  for (const [title, items] of [
    [
      'Evidence',
      a?.evidence ?? [
        'Collect the required source data and run a forecast to inspect this specialist.',
      ],
    ],
    ['Limitations', a?.limitations ?? []],
    ['Validation', a?.audit?.reasons ?? []],
  ]) {
    if (!items.length) continue;
    const section = node('section', undefined, 'evidence-section'),
      list = node('ul');
    section.append(node('h3', title));
    for (const item of items) list.append(node('li', String(item)));
    section.append(list);
    body.append(section);
  }
  body.append(
    node(
      'p',
      'Rules are experimental and uncalibrated. Evidence checks do not independently prove that a claim is true.',
    ),
  );
  $('evidence-dialog').showModal();
}
function renderSources() {
  const sources = Object.entries(state?.sources ?? {}),
    count = sources.filter(([, v]) => v.status === 'collected').length;
  setText('strip-sources', sources.length ? count + ' / ' + sources.length : '—');
  setText('source-summary', sources.length ? count + ' RETRIEVED' : 'NOT COLLECTED');
  const names = {
    btc_prices: [['Chart'], 'Bitcoin price history', 'Binance · completed daily candles'],
    chain: [['Network', 'Miner'], 'Network & miner metrics', 'Coin Metrics · daily observations'],
    macro: [['World economy'], 'Macro market context', 'Yahoo · dollar index & Treasury yield'],
    news: [['News'], 'Bitcoin news', 'Yahoo · timestamped articles'],
    mood: [['Mood'], 'Market sentiment', 'Alternative.me · Fear & Greed'],
    borrowing: [['Borrowing'], 'Funding & open interest', 'Binance USD-M · completed settlements'],
    stablecoin: [['Stablecoin'], 'Stablecoin live quotes', 'CoinGecko · 15-minute snapshots'],
    stablecoin_history: [
      ['Stablecoin'],
      'Stablecoin peg history',
      'CoinGecko · 365 daily prices & volume (peg-stress model)',
    ],
    stablecoin_long_history: [
      ['Stablecoin'],
      'Stablecoin supply history',
      'DefiLlama + Coinbase · daily since 2019 (liquidity model)',
    ],
    etf_flows: [['ETF'], 'ETF fund flows', 'SoSoValue (last month) + reviewed data/etf_flows.csv'],
    betting_odds: [
      ['Betting odds'],
      'Prediction markets',
      'Recent timestamped Kalshi BTC trade required',
    ],
  };
  const container = $('sources');
  container.replaceChildren();
  if (!sources.length) {
    container.append(
      node('div', 'Source freshness and collection status appear here.', 'empty-inline'),
    );
    return;
  }
  for (const [name, value] of sources) {
    const [identities, title, description] = names[name] ?? [[], name, ''];
    const ok = value.status === 'collected',
      row = node('div', undefined, 'source-row'),
      main = node('div', undefined, 'source-main');
    main.append(
      node('strong', title),
      node(
        'small',
        description +
          (ok
            ? ' · ' + utc(value.received_at)
            : name === 'betting_odds' && value.reason
              ? ' · ' + value.reason
              : ''),
      ),
    );
    const status = node('span', undefined, 'source-state' + (ok ? '' : ' missing'));
    status.append(
      node('i', undefined, 'status-dot'),
      document.createTextNode(ok ? 'Retrieved' : 'Needs setup'),
    );
    const icons = node('span', undefined, 'source-icons');
    identities.forEach((role) => icons.append(agentIcon(role)));
    row.append(icons, main, status);
    if (value.reason) row.title = String(value.reason);
    container.append(row);
  }
}
function renderPaper() {
  const p = state?.paper;
  if (!p) {
    setText('equity', '\u2014');
    setText('portfolio-sub', 'No paper decision displayed');
    setText('decision', 'No recorded decision yet.');
    setText('decision-action', 'Ready when you are');
    setText('decision-reason', 'Generate a forecast, then record a guarded paper decision.');
    setText(
      'breaker-state',
      state?.paper_settings?.configured ? 'Armed · saved policy' : 'Not configured',
    );
    setText('risk-label', state?.paper_settings?.configured ? 'PAPER ENABLED' : 'PAPER ONLY');
    return;
  }
  setText('equity', money(p.portfolio.equity_usd));
  setText(
    'portfolio-sub',
    p.portfolio.trade_count +
      ' paper trades \u00b7 ' +
      money(p.portfolio.total_fees_usd) +
      ' in fees',
  );
  const status =
    p.risk.status ?? (p.trade ? 'executed' : p.requested !== 'HOLD' ? 'blocked' : 'no_trade');
  const label =
    status === 'executed'
      ? p.trade.action + ' executed'
      : status === 'blocked' || status === 'halted'
        ? p.requested + ' blocked'
        : 'No trade executed';
  setText(
    'decision-action',
    label + (p.synthetic ? ' \u00b7 synthetic paper example' : ' \u00b7 paper decision'),
  );
  setText('decision-reason', p.risk.reason);
  setText(
    'breaker-state',
    p.breaker.halted
      ? 'PAUSED \u00b7 ' + p.breaker.reason
      : p.breaker.configured
        ? 'Armed \u00b7 configured policy'
        : 'Not configured',
  );
  setText(
    'risk-label',
    p.breaker.halted
      ? 'TRADING PAUSED'
      : p.breaker.configured
        ? 'RISK CHECKED'
        : 'RISK NOT CONFIGURED',
  );
  setText('decision', JSON.stringify(p, null, 2));
}
function renderReplay() {
  renderReplayCoins();
  const r = state.replay,
    archive = r.mode.startsWith('ARCHIVED'),
    real = archive || r.mode.startsWith('REAL');
  setText(
    'replay-badge',
    archive
      ? 'ARCHIVED LIVE FORECASTS'
      : real
        ? 'REAL BTC HISTORY · PRICE-BASED AGENTS ONLY'
        : 'SYNTHETIC WALKTHROUGH · FAKE AGENTS & PRICES',
  );
  $('replay-badge').className = 'replay-mode ' + (real ? 'real' : 'synthetic');
  $('replay-empty').hidden = !!r.results?.length;
  setText(
    'replay',
    JSON.stringify(
      {
        mode: r.mode,
        summary: r.summary,
        baselines: r.baselines,
        pending: r.pending,
        problem: r.problem,
        output: r.output,
      },
      null,
      2,
    ),
  );
  const box = $('replay-results');
  box.replaceChildren();
  for (const result of Array.isArray(r.summary) ? r.summary : []) {
    const card = node('div', undefined, 'research-result'),
      names = { exchange: 'Agent Exchange', average: 'Simple average', vote: 'Majority vote' };
    card.append(
      node('span', names[result.method] ?? result.method),
      node('strong', typeof result.brier === 'number' ? result.brier.toFixed(4) : '—'),
      node('small', 'Brier score · lower is better · ' + result.evaluated_days + ' evaluated days'),
    );
    box.append(card);
  }
}
function ema(values, period) {
  let last = values[0] ?? 0;
  const alpha = 2 / (period + 1);
  return values.map((v) => (last = v * alpha + last * (1 - alpha)));
}
function canvasContext(id) {
  const c = $(id),
    rect = c.getBoundingClientRect(),
    ratio = Math.min(window.devicePixelRatio || 1, 3),
    w = Math.max(1, rect.width),
    h = Math.max(1, rect.height);
  if (c.width !== Math.round(w * ratio) || c.height !== Math.round(h * ratio)) {
    c.width = Math.round(w * ratio);
    c.height = Math.round(h * ratio);
  }
  const x = c.getContext('2d');
  x.setTransform(ratio, 0, 0, ratio, 0, 0);
  x.clearRect(0, 0, w, h);
  return { x, w, h };
}
function drawPrice() {
  const { x, w, h } = canvasContext('price-chart'),
    all = market.candles || [],
    bars = all.slice(-range),
    left = 12,
    right = Math.max(left + 10, w - 65),
    top = 14,
    bottom = h - 45,
    priceBottom = bottom - 40,
    volumeTop = priceBottom + 18,
    n = bars.length;
  plot = null;
  x.font = '8px ui-monospace, SFMono-Regular, monospace';
  if (!n) {
    x.strokeStyle = '#dce3df';
    for (let k = 0; k < 5; k++) {
      const y = top + (k * (priceBottom - top)) / 4;
      x.beginPath();
      x.moveTo(left, y);
      x.lineTo(right, y);
      x.stroke();
    }
    return;
  }
  const full20 = ema(
      all.map((b) => b.close),
      20,
    ),
    full50 = ema(
      all.map((b) => b.close),
      50,
    ),
    e20 = full20.slice(-n),
    e50 = full50.slice(-n),
    useCandles =
      chartStyle === 'candles' && bars.every((b) => b.high !== undefined && b.low !== undefined);
  const vals = bars.flatMap((b, i) => [
    useCandles ? b.low : b.close,
    useCandles ? b.high : b.close,
    ...(showEma ? [e20[i], e50[i]] : []),
  ]);
  let lo = Math.min(...vals),
    hi = Math.max(...vals),
    span = hi - lo || hi * 0.02;
  lo -= span * 0.08;
  hi += span * 0.08;
  span = hi - lo;
  const py = (v) => top + ((hi - v) / span) * (priceBottom - top),
    step = (right - left) / n,
    px = (i) => left + (i + 0.5) * step;
  for (let k = 0; k < 5; k++) {
    const v = hi - (k * span) / 4,
      y = py(v);
    x.strokeStyle = '#dce3df';
    x.lineWidth = 0.7;
    x.beginPath();
    x.moveTo(left, y);
    x.lineTo(right, y);
    x.stroke();
    x.fillStyle = '#6f7f7a';
    x.fillText(num(v, 0), right + 9, y + 3);
  }
  const ticks = Math.min(5, n);
  for (let k = 0; k < ticks; k++) {
    const i = Math.round((k * (n - 1)) / Math.max(1, ticks - 1)),
      xx = px(i);
    x.strokeStyle = '#e4e9e4';
    x.beginPath();
    x.moveTo(xx, top);
    x.lineTo(xx, bottom + 8);
    x.stroke();
    x.fillStyle = '#77867f';
    const text = bars[i].date.slice(5).replace('-', '/');
    x.fillText(text, Math.max(left, Math.min(right - 25, xx - 13)), h - 15);
  }
  const maxVolume = Math.max(0, ...bars.map((b) => b.volume ?? 0));
  if (maxVolume) {
    bars.forEach((b, i) => {
      if (b.volume === undefined) return;
      x.fillStyle = b.close >= b.open ? '#4a91783b' : '#b66d7338';
      const y = bottom - (b.volume / maxVolume) * (bottom - volumeTop);
      x.fillRect(
        px(i) - Math.max(1, step * 0.55) / 2,
        y,
        Math.max(1, step * 0.55),
        Math.max(1, bottom - y),
      );
    });
    x.fillStyle = '#7b8a82';
    x.fillText('VOL · BTC', left + 3, volumeTop + 3);
  }
  if (useCandles) {
    for (let i = 0; i < n; i++) {
      const b = bars[i],
        xx = px(i),
        up = b.close >= b.open;
      x.strokeStyle = up ? '#34866d' : '#b86c76';
      x.fillStyle = x.strokeStyle;
      x.lineWidth = Math.max(0.6, Math.min(1, step * 0.4));
      x.beginPath();
      x.moveTo(xx, py(b.high));
      x.lineTo(xx, py(b.low));
      x.stroke();
      const y = Math.min(py(b.open), py(b.close)),
        height = Math.max(1, Math.abs(py(b.open) - py(b.close))),
        bw = Math.max(0.7, Math.min(8, step * 0.62));
      x.fillRect(xx - bw / 2, y, bw, height);
    }
  } else {
    const gradient = x.createLinearGradient(0, top, 0, priceBottom);
    gradient.addColorStop(0, '#6e8fb026');
    gradient.addColorStop(1, '#6e8fb000');
    x.beginPath();
    bars.forEach((b, i) => (i ? x.lineTo(px(i), py(b.close)) : x.moveTo(px(i), py(b.close))));
    x.lineTo(px(n - 1), priceBottom);
    x.lineTo(px(0), priceBottom);
    x.closePath();
    x.fillStyle = gradient;
    x.fill();
    line(
      bars.map((b) => b.close),
      '#5c7f9f',
      1.5,
    );
  }
  function line(values, color, width) {
    x.strokeStyle = color;
    x.lineWidth = width;
    x.beginPath();
    values.forEach((v, i) => (i ? x.lineTo(px(i), py(v)) : x.moveTo(px(i), py(v))));
    x.stroke();
  }
  if (showEma) {
    line(e20, '#a88b50', 1);
    line(e50, '#8e83a9', 1);
  }
  const last = bars.at(-1),
    lastY = py(last.close);
  x.setLineDash([3, 4]);
  x.strokeStyle = '#59857466';
  x.beginPath();
  x.moveTo(left, lastY);
  x.lineTo(right, lastY);
  x.stroke();
  x.setLineDash([]);
  x.fillStyle = '#dfebe2';
  x.fillRect(right + 3, lastY - 8, 61, 16);
  x.fillStyle = '#3b6b55';
  x.fillText(num(last.close, 0), right + 7, lastY + 3);
  if (hover !== null) {
    const i = Math.max(0, Math.min(n - 1, hover)),
      xx = px(i),
      b = bars[i],
      yy = py(b.close);
    x.setLineDash([3, 4]);
    x.strokeStyle = '#8b9d9399';
    x.beginPath();
    x.moveTo(xx, top);
    x.lineTo(xx, bottom + 5);
    x.moveTo(left, yy);
    x.lineTo(right, yy);
    x.stroke();
    x.setLineDash([]);
    x.fillStyle = '#65817a';
    x.beginPath();
    x.arc(xx, yy, 2.5, 0, Math.PI * 2);
    x.fill();
    readout(b);
  }
  plot = { left, right, step, bars, px, py };
}
function drawReplay() {
  const { x, w, h } = canvasContext('replay-chart'),
    rows = state?.replay?.results ?? [],
    left = 25,
    right = w - 15,
    top = 15,
    bottom = h - 27;
  x.font = '8px ui-monospace,monospace';
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    const y = bottom - v * (bottom - top);
    x.strokeStyle = v === 0.5 ? '#c1cec5' : '#dfe6df';
    x.setLineDash(v === 0.5 ? [3, 4] : []);
    x.beginPath();
    x.moveTo(left, y);
    x.lineTo(right, y);
    x.stroke();
    x.setLineDash([]);
    x.fillStyle = '#7c8c82';
    x.fillText(String(Math.round(v * 100)), 0, y + 3);
  }
  for (const [key, color] of [
    ['exchange', '#5d7c9d'],
    ['average', '#9a87ab'],
    ['vote', '#b0945e'],
  ]) {
    x.strokeStyle = color;
    x.lineWidth = 1.6;
    x.beginPath();
    let pen = false;
    rows.forEach((r, i) => {
      const v = r[key] ?? r[key + '_clean'];
      if (typeof v !== 'number' || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      const a = left + (i * (right - left)) / Math.max(1, rows.length - 1),
        b = bottom - v * (bottom - top);
      pen ? x.lineTo(a, b) : x.moveTo(a, b);
      pen = true;
    });
    x.stroke();
  }
  if (rows.length) {
    x.fillStyle = '#718579';
    x.fillText(String(rows[0].date).slice(0, 10), left, h - 7);
    x.fillText(String(rows.at(-1).date).slice(0, 10), Math.max(left, right - 70), h - 7);
  }
}
function renderKeyStatus() {
  const status = state?.gemini ?? { configured: state?.gemini_configured, source: 'environment' };
  setText(
    'key-current',
    status.configured
      ? status.source === 'session'
        ? 'Session key configured · stored in server memory only'
        : 'Using a key from the local environment'
      : 'No Gemini key configured',
  );
  $('clear-key').hidden = status.source !== 'session';
}
document.querySelectorAll('[data-action]').forEach((button) =>
  button.addEventListener('click', async () => {
    document.querySelectorAll('[data-action]').forEach((b) => (b.disabled = true));
    try {
      await post(
        button.dataset.action,
        button.dataset.action === 'paper' && $('paper-action').value
          ? { action: $('paper-action').value }
          : undefined,
      );
      if (button.dataset.action === 'paper') await refreshPortfolio();
      if (button.dataset.action === 'forecast')
        $('activity').scrollIntoView({ behavior: 'smooth', block: 'start' });
      await refresh();
    } catch (error) {
      toast(error.message);
      if (state) render(state);
    }
  }),
);
function openGeminiDialog() {
  setText('key-feedback', '');
  $('gemini-key').value = '';
  renderKeyStatus();
  $('key-dialog').showModal();
}
document
  .querySelectorAll('[data-open-key]')
  .forEach((b) => b.addEventListener('click', openGeminiDialog));
document
  .querySelectorAll('[data-close-dialog]')
  .forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));
$('key-dialog').addEventListener('close', () => {
  $('gemini-key').value = '';
  setText('key-feedback', '');
});
$('key-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('gemini-key');
  if (!input.value.trim()) {
    input.focus();
    return;
  }
  $('save-key').disabled = true;
  $('clear-key').disabled = true;
  try {
    await post('gemini', { api_key: input.value });
    setText('key-feedback', 'Configured for this server session. Run a forecast to use Gemini.');
    $('key-feedback').classList.remove('error');
    await refresh();
  } catch (error) {
    setText('key-feedback', error.message);
    $('key-feedback').classList.add('error');
  } finally {
    input.value = '';
    $('save-key').disabled = false;
    $('clear-key').disabled = false;
  }
});
$('clear-key').addEventListener('click', async () => {
  try {
    await post('gemini/clear');
    $('gemini-key').value = '';
    setText('key-feedback', 'Session key cleared.');
    await refresh();
  } catch (error) {
    setText('key-feedback', error.message);
  }
});
document.querySelectorAll('[data-filter]').forEach((b) =>
  b.addEventListener('click', () => {
    filter = b.dataset.filter;
    document
      .querySelectorAll('[data-filter]')
      .forEach((x) => x.classList.toggle('selected', x === b));
    renderAgents();
  }),
);
$('agent-search').addEventListener('input', (e) => {
  search = e.target.value.trim().toLowerCase();
  renderAgents();
});
document.querySelectorAll('[data-range]').forEach((b) =>
  b.addEventListener('click', () => {
    range = Number(b.dataset.range);
    hover = null;
    document.querySelectorAll('[data-range]').forEach((x) => {
      x.classList.toggle('selected', x === b);
      x.setAttribute('aria-pressed', String(x === b));
    });
    drawPrice();
  }),
);
for (const [id, style] of [
  ['candles-button', 'candles'],
  ['line-button', 'line'],
])
  $(id).addEventListener('click', () => {
    chartStyle = style;
    for (const [key, value] of [
      ['candles-button', 'candles'],
      ['line-button', 'line'],
    ]) {
      $(key).classList.toggle('selected', chartStyle === value);
      $(key).setAttribute('aria-pressed', String(chartStyle === value));
    }
    drawPrice();
  });
$('ema-toggle').addEventListener('click', () => {
  showEma = !showEma;
  $('ema-toggle').classList.toggle('selected', showEma);
  $('ema-toggle').setAttribute('aria-pressed', String(showEma));
  drawPrice();
});
$('expand-chart').addEventListener('click', () => {
  $('price-panel').classList.toggle('expanded');
  $('expand-chart').setAttribute(
    'aria-label',
    $('price-panel').classList.contains('expanded') ? 'Collapse chart' : 'Expand chart',
  );
  requestAnimationFrame(drawPrice);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('price-panel').classList.contains('expanded')) {
    $('price-panel').classList.remove('expanded');
    $('expand-chart').setAttribute('aria-label', 'Expand chart');
    drawPrice();
  }
});
$('price-chart').addEventListener('pointermove', (e) => {
  if (!plot) return;
  const rect = e.currentTarget.getBoundingClientRect(),
    i = Math.floor((e.clientX - rect.left - plot.left) / plot.step);
  hover = Math.max(0, Math.min(plot.bars.length - 1, i));
  const b = plot.bars[hover];
  $('chart-tooltip').hidden = false;
  setText(
    'chart-tooltip',
    b.date +
      ' UTC\nClose ' +
      num(b.close) +
      ' USDT' +
      (b.volume !== undefined ? '\nVolume ' + num(b.volume, 0) + ' BTC' : ''),
  );
  drawPrice();
});
$('price-chart').addEventListener('pointerleave', () => {
  hover = null;
  $('chart-tooltip').hidden = true;
  readout(market.candles?.at(-1));
  drawPrice();
});
document.querySelectorAll('.rail nav a').forEach((a) =>
  a.addEventListener('click', () => {
    document
      .querySelectorAll('.rail nav a')
      .forEach((x) => x.classList.toggle('selected', x === a));
  }),
);
const resize = new ResizeObserver(() => {
  drawPrice();
  drawReplay();
  drawPortfolio();
});
resize.observe($('price-chart').parentElement);
resize.observe($('replay-chart').parentElement);
resize.observe($('portfolio-chart').parentElement);
renderAgents();
renderActivity(null);
drawPrice();
drawReplay();
refresh();
function requiresGemini(reason) {
  return (
    reason?.startsWith('No model client was supplied') ||
    reason?.startsWith('No explicit Betting Odds model callable')
  );
}

function forecastReason(role, reason) {
  if (requiresGemini(reason))
    return state?.gemini_configured
      ? 'Gemini is connected. Generate a new forecast to use it.'
      : 'Connect Gemini, then generate a new forecast.';
  const book = state?.sources?.betting_odds;
  if (
    role === 'Betting odds' &&
    book?.status === 'unavailable' &&
    reason?.startsWith('Missing or invalid timezone-aware student2_markets_observed_at')
  ) {
    return book.reason;
  }
  return reason;
}

function renderLiveCoins({ learning, forecast }) {
  const rows = $('live-coin-rows');
  rows.replaceChildren();
  if (!learning) return;
  const models = {
    News: 'News Specialist',
    Mood: 'Mood Specialist',
    'Betting odds': 'Betting Odds Specialist',
  };
  const bets = new Map((learning.last_settled?.bets ?? []).map((bet) => [bet.agent, bet]));
  setText(
    'live-coin-status',
    learning.settled +
      ' settled · ' +
      learning.pending +
      ' pending. ' +
      'Balances update after settlement. Forecast weights show each agent’s influence in the latest run.',
  );
  for (const [role] of roles) {
    const result = forecast?.agents?.find((agent) => agent.role === role);
    const id = models[role] ?? role.toLowerCase().replaceAll(' ', '_').replaceAll('-', '_');
    const balance = learning.coins[id] ?? learning.start_coins;
    const stakeResult = bets.get(id);
    const weight = forecast?.weights?.[id];
    const participation =
      weight != null
        ? 'Active'
        : result
          ? 'Abstained · ' + forecastReason(role, result.evidence?.[0] ?? 'No validated forecast')
          : 'Awaiting forecast';
    const row = node('tr');
    const name = node('td'),
      identity = node('div', undefined, 'agent-name');
    identity.append(agentIcon(role), node('span', role));
    name.append(identity);
    row.append(
      name,
      node('td', num(balance)),
      node(
        'td',
        stakeResult ? formatCoinChange(stakeResult.coins_after - stakeResult.coins_before) : '—',
      ),
      node('td', percent(result && weight == null ? 0 : weight)),
      node('td', participation),
    );
    rows.append(row);
  }
}

function formatCoinChange(value) {
  const rounded = Number(value.toFixed(2));
  return (rounded > 0 ? '+' : '') + rounded.toFixed(2);
}

function renderReplayCoins() {
  const history = state.replay?.coins ?? [];
  const dates = $('replay-coin-date');
  dates.replaceChildren(...history.map((day) => node('option', day.date)));
  dates.selectedIndex = history.length - 1;
  $('replay-coin-history').hidden = history.length === 0;
  dates.onchange = showDay;
  showDay();

  function showDay() {
    const day = history[dates.selectedIndex];
    const previous = history[dates.selectedIndex - 1];
    const rows = $('replay-coin-rows');
    rows.replaceChildren();
    setText(
      'replay-coin-status',
      !day
        ? (state.replay.problem?.reason ?? 'No settled coin history is available for this replay.')
        : (state.replay.mode.startsWith('ARCHIVED')
            ? 'Coin changes are replayed in order. Weight is the original live forecast weight.'
            : 'Balances update after settlement. Weight is the influence used before settlement.') +
            (day.cap_feasible ? '' : ' The 20% cap was infeasible on this day.'),
    );
    if (!day) return;
    const result = state.replay.results?.[dates.selectedIndex];
    const abstentions = JSON.parse(result?.abstentions ?? '{}');
    const active = new Set(JSON.parse(result?.active_agents ?? '[]'));
    for (const key of Object.keys(day).filter((key) => key.startsWith('coins_'))) {
      const agent = key.slice('coins_'.length);
      const opening = previous?.[key] ?? 100;
      const closing = day[key];
      const change = Number((closing - opening).toFixed(2));
      const row = node('tr');
      row.append(
        node('td', agent),
        node('td', opening.toFixed(2)),
        node(
          'td',
          formatCoinChange(change),
          change > 0 ? 'positive' : change < 0 ? 'negative' : '',
        ),
        node('td', closing.toFixed(2)),
        node('td', percent(day['weight_' + agent])),
        node('td', active.has(agent) ? 'Active' : (abstentions[agent] ?? 'Unavailable')),
      );
      rows.append(row);
    }
  }
}

function renderPaperSettings() {
  const config = state?.paper_settings,
    ready = !!config?.configured;
  for (const [id, key] of [
    ['paper-swing', 'max_forecast_swing'],
    ['paper-loss', 'max_cumulative_loss'],
    ['paper-buy-size', 'buy_fraction'],
  ]) {
    const input = $(id);
    input.disabled = ready || !!state?.busy;
    if (ready) input.value = Number(config[key]) * 100;
  }
  $('enable-paper').hidden = ready;
  $('enable-paper').disabled = !!state?.busy || !state?.forecast;
  $('paper-action').disabled = !!state?.busy || !ready;
  setText(
    'paper-settings-summary',
    ready
      ? 'Enabled: ' +
          Number(config.max_forecast_swing) * 100 +
          ' pp forecast change · ' +
          Number(config.max_cumulative_loss) * 100 +
          '% loss limit · ' +
          Number(config.buy_fraction) * 100 +
          '% of cash per BUY. Total BTC cap: 25%.'
      : 'Generate a forecast, then enable paper trading. Total BTC holdings remain capped at 25% of equity.',
  );
}
$('paper-settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('enable-paper').disabled = true;
  setText('paper-feedback', '');
  try {
    await post('paper/settings', {
      max_forecast_swing: Number($('paper-swing').value) / 100,
      max_cumulative_loss: Number($('paper-loss').value) / 100,
      buy_fraction: Number($('paper-buy-size').value) / 100,
    });
    setText(
      'paper-feedback',
      'Paper trading enabled. Choose a decision mode and record your next fresh forecast.',
    );
    await refresh();
  } catch (error) {
    setText('paper-feedback', error.message);
    renderPaperSettings();
  }
});

let portfolioRefreshTask = null;
async function refreshPortfolio() {
  if (portfolioRefreshTask) return portfolioRefreshTask;
  portfolioRefreshTask = (async () => {
    try {
      const response = await fetch('/api/portfolio');
      if (!response.ok) throw new Error('Portfolio unavailable');
      portfolioValuation = await response.json();
      portfolioFetchError = false;
      renderPortfolio();
    } catch {
      portfolioFetchError = true;
      setText('portfolio-status', 'CONNECTION LOST');
      setText(
        'portfolio-price-status',
        'Valuation could not be refreshed. Displayed values may be out of date.',
      );
      $('portfolio-price-status').classList.add('stale');
      setText('portfolio-sub', 'Last known account value · connection lost');
    }
  })();
  try {
    await portfolioRefreshTask;
  } finally {
    portfolioRefreshTask = null;
  }
}
function renderPortfolio() {
  const value = portfolioValuation;
  if (!value) return;
  const p = value.portfolio;
  const signed = (v) =>
    v == null ? '—' : (Number(v) < 0 ? '−' : '+') + money(Math.abs(Number(v)));
  setText('portfolio-equity', money(p.equity_usd));
  setText('portfolio-cash', money(p.cash_usd));
  setText('portfolio-btc', p.btc == null ? '—' : num(Number(p.btc), 8) + ' BTC');
  setText('portfolio-btc-value', money(p.btc_value_usd));
  setText('portfolio-realized', signed(p.realized_pnl_usd));
  setText('portfolio-unrealized', signed(p.unrealized_pnl_usd));
  setText('portfolio-fees', money(p.total_fees_usd));
  setText(
    'portfolio-pnl',
    signed(p.total_pnl_usd) +
      (p.return_pct == null ? '' : ' (' + num(Number(p.return_pct)) + '%)') +
      ' from ' +
      money(p.initial_cash_usd) +
      ' starting cash',
  );
  $('portfolio-pnl').className =
    p.total_pnl_usd == null ? '' : Number(p.total_pnl_usd) < 0 ? 'negative' : 'positive';
  const age = value.quote
    ? Math.max(0, (Date.now() - Date.parse(value.quote.bar_close_time)) / 1000)
    : null;
  const stale = portfolioFetchError || value.status === 'stale' || (age !== null && age > 180);
  setText(
    'portfolio-status',
    portfolioFetchError
      ? 'CONNECTION LOST'
      : stale
        ? 'STALE PRICE'
        : value.status === 'live'
          ? 'UPDATES EVERY MINUTE'
          : value.status === 'unavailable'
            ? 'PRICE UNAVAILABLE'
            : 'WAITING FOR PRICE',
  );
  setText(
    'portfolio-price-status',
    value.quote
      ? (stale ? 'Showing the last available price. ' : '') +
          'BTC reference ' +
          num(Number(value.quote.price)) +
          ' USDT per BTC · price time ' +
          utc(value.quote.bar_close_time) +
          ' · last checked ' +
          utc(value.last_attempt || value.quote.retrieved_at) +
          (value.error ? ' · latest refresh failed' : '')
      : value.error
        ? 'Price unavailable. The server will retry automatically; balances are unchanged.'
        : 'Waiting for the first Binance price. No forecast or trade is needed.',
  );
  $('portfolio-price-status').classList.toggle('stale', stale || !!value.error);
  setText('equity', money(p.equity_usd));
  setText(
    'portfolio-sub',
    p.trade_count +
      ' paper trades · ' +
      (stale
        ? 'last available price'
        : value.quote
          ? 'updated automatically'
          : 'waiting for price'),
  );
  const points = value.history || [];
  $('portfolio-chart-empty').hidden = points.length > 0;
  setText(
    'portfolio-history-status',
    points.length
      ? points.length +
          ' saved observations · ' +
          utc(points[0].time) +
          ' to ' +
          utc(points.at(-1).time)
      : 'No valuation observations yet',
  );
  drawPortfolio();
}
function drawPortfolio() {
  const { x, w, h } = canvasContext('portfolio-chart');
  const rows = (portfolioValuation?.history || []).filter((r) =>
    Number.isFinite(Number(r.equity_usd)),
  );
  const left = 70,
    right = Math.max(left + 1, w - 12),
    top = 16,
    bottom = h - 28;
  x.font = '10px ui-monospace, SFMono-Regular, monospace';
  if (!rows.length) return;
  const values = rows.map((r) => Number(r.equity_usd)),
    times = rows.map((r) => Date.parse(r.time));
  const lo = Math.min(...values),
    hi = Math.max(...values),
    pad = Math.max((hi - lo) * 0.15, 1);
  const py = (v) => top + ((hi + pad - v) / (hi - lo + 2 * pad)) * (bottom - top);
  const px = (i) =>
    rows.length === 1
      ? (left + right) / 2
      : left + ((times[i] - times[0]) / Math.max(1, times.at(-1) - times[0])) * (right - left);
  for (let i = 0; i < 4; i++) {
    const v = lo - pad + (i * (hi - lo + 2 * pad)) / 3,
      y = py(v);
    x.strokeStyle = '#dce3df';
    x.beginPath();
    x.moveTo(left, y);
    x.lineTo(right, y);
    x.stroke();
    x.fillStyle = '#66766e';
    x.fillText(money(v), 2, y + 3);
  }
  x.strokeStyle = '#34866d';
  x.lineWidth = 2;
  x.beginPath();
  rows.forEach((r, i) => {
    if (!i || times[i] - times[i - 1] > 180000) x.moveTo(px(i), py(values[i]));
    else x.lineTo(px(i), py(values[i]));
  });
  x.stroke();
  x.fillStyle = '#34866d';
  rows.forEach((r, i) => {
    x.beginPath();
    x.arc(px(i), py(values[i]), rows.length < 5 ? 3 : 1.5, 0, Math.PI * 2);
    x.fill();
  });
  x.fillStyle = '#66766e';
  x.fillText(utc(rows[0].time), left, h - 7);
  if (rows.length > 1) x.fillText(utc(rows.at(-1).time), Math.max(left, right - 120), h - 7);
}
(async function pollPortfolio() {
  await refreshPortfolio();
  setTimeout(pollPortfolio, 5000);
})();

/* ---------- Group chat: replays the latest saved forecast as a team chat with a live poll ---------- */
const chatLLM=new Set(['News','Mood']);
const chatQuips={
    'Chart':['Candles looking spicy 🕯️📈','Chart says nope 📉','Flat chart, flat vibes'],
    'Borrowing':['Traders are borrowing to go long 💸','Leverage is unwinding, careful 🧯','Funding is meh'],
    'Network':['The chain is buzzing ⛓️','The chain is kinda quiet rn','On-chain looks neutral'],
    'Miner':['Miners are chilling, not dumping ⛏️','Miners might be selling 😬','Miners look neutral'],
    'Stablecoin':['Fresh stablecoin cash is coming in 💵','Stablecoin liquidity is drying up 🏜️','Stablecoins are steady'],
    'ETF':['ETFs are buying 🏦','ETF money is walking out the door 🚪','ETF flows are flat'],
    'News':['Headlines look good 📰','Headlines are rough 📰','News is mixed'],
    'Mood':['Vibes are bullish 😎','Vibes are fearful 😨','Vibes are neutral 😐'],
    'World economy':['Dollar is easing, good for BTC 🌍','Strong dollar, not great for BTC 🌍','Macro looks neutral'],
    'Betting odds':['Bettors lean up 🎲','Bettors lean down 🎲','Bettors are split'],
    'Crash':['No crash signal, relax 🛟','Tail-risk alert 🚨','No strong crash signal'],
    'Long-term':['Zooming out, BTC looks cheap 🔭','Zooming out, still stretched 🔭','Long-term picture is neutral'],
    'Contrarian':['You all agree too much… so I disagree 🙃','You all agree too much… so I disagree 🙃','Not enough consensus to fight. Staying at 50% 🤷'],
};
let chatTimer=null,chatForecastId;
function chatVote(a){if(!a||typeof a.p_up!=='number'||a.data_missing===true)return 'abstain';return a.p_up>.5?'up':a.p_up<.5?'down':'neutral'}
function chatClip(text,max){text=String(text??'').replace(/\s+/g,' ').trim();return text.length>max?text.slice(0,max-1)+'…':text}
function chatAvatar(role,emoji){const box=node('span',undefined,'chat-avatar');const icon=role?agentIcon(role):null;if(icon)box.append(icon);else box.textContent=emoji||'A';return box}
function chatScript(s){
    const f=s.forecast,msgs=[],time=f.created_at;
    if(chatIsBacktest(f))msgs.push({kind:'system',text:'🕰️ Backtest'+(f.scenario?' · '+f.scenario.label:'')+': rebuilt from data published by the end of '+chatDayLabel(f).replace(' close','')+' (UTC). Nobody gets to see anything from later. News, Mood and Betting odds sit out: there is no archive of what they could have read that day.'});
    if(f.events?.today)msgs.push({kind:'system',text:'📅 That day: '+f.events.today+(f.events.pinned?' 📌':'')});
    else if(f.events?.pinned)msgs.push({kind:'system',text:'📌 One of the dates you asked about'});
    msgs.push({kind:'system',text:'📊 Agent Exchange started a poll: will BTC be higher 24 hours from now? Everyone post your evidence, then vote.'});
    for(const [role] of roles){
        const a=f.agents.find(x=>x.role===role),vote=chatVote(a),missing=vote==='abstain';
        const llm=chatLLM.has(role);let quip,evidence;
        if(missing){
            const reason=String((a?.limitations||a?.evidence||[])[0]??'No usable data for this run');
            const noKey=/model client/i.test(reason);
            quip=llm&&noKey?"No Gemini key connected, so I can't read anything this round. Sitting this one out 😴":"Can't see fresh data, sitting this one out 😴";
            evidence=noKey?'':reason;
            if(chatIsBacktest(f)&&/leak the future/i.test(reason)){quip="🙈 No peeking: there's no archive of what I could have read that day, so I sit this one out.";evidence=reason}
            else if(chatIsBacktest(f)&&/training window|in-sample/i.test(reason)){quip="📚 That day is inside my model's training data, so using it would be grading my own homework. Sitting out.";evidence=''}
        }else{
            quip=chatQuips[role]?.[vote==='up'?0:vote==='down'?1:2]??'';
            const lines=(Array.isArray(a.evidence)?a.evidence:[a.evidence]).map(String);evidence=lines.find(x=>!x.trim().toLowerCase().startsWith('source '))??lines[0]??'';
        }
        msgs.push({kind:'agent',role,llm,vote,p:missing?null:a.p_up,quip,evidence:chatClip(evidence,190),time});
    }
    const tally={up:0,down:0,neutral:0,abstain:0};msgs.filter(m=>m.kind==='agent').forEach(m=>tally[m.vote]++);
    const m=f.methods||{};
    msgs.push({kind:'admin',time,text:'Poll closed: '+tally.up+' up · '+tally.down+' down'+(tally.neutral?' · '+tally.neutral+' neutral at 50%':'')+' · '+tally.abstain+' abstained (no data). A plain majority vote counts only the up and down votes: '+tally.up+' of '+(tally.up+tally.down)+' = '+percent(m.vote)+' chance of up. But the Exchange weights each agent by the coins it has won on past bets'+(f.cap_feasible?', with no agent above 20% of the say':'')+', so the team forecast is '+percent(m.exchange)+' up. (Simple average: '+percent(m.average)+'.)'});
    const d=s.debate;
    if(d?.available&&Array.isArray(d.turns)){
        msgs.push({kind:'system',text:'🧵 Side thread: the Bull / Bear / Judge debate (Gemini) read the same reports.'});
        const em={Bull:'🐂',Bear:'🐻',Judge:'⚖️'};
        for(const t of d.turns)msgs.push({kind:'debate',name:t.role,emoji:em[t.role]||'💬',text:chatClip(t.argument,320)+(t.role==='Judge'&&typeof t.p_up==='number'?' → '+percent(t.p_up)+' up':''),time});
    }
    if(s.live&&chatBossMode==='sens')msgs.push({kind:'system',text:'⚖️ The 52/46 sensitivity switch only applies to replays. Live paper trades always use the dashboard\'s 55/45 rule.'});
    msgs.push({kind:'boss',time,first:true,text:(s.live?'':'(Back then) ')+bossIntro(f,s.live)});
    if(s.live)msgs.push({kind:'bossTrade',time});
    else if(chatOutcome(s.settlement)){msgs.push(chatResultMsg(f,s.settlement));msgs.push({kind:'boss',time:f.contract?.target_time??time,text:bossReview(f,s.settlement)})}
    else msgs.push({kind:'system',text:'⏳ Not settled yet: the 24-hour result is due '+utc(f.contract?.target_time)+'. It is scored on the next refresh or forecast after that.'});
    return msgs;
}
function chatMessage(msg){
    const li=node('li',undefined,'chat-msg chat-'+msg.kind);
    if(msg.kind==='system'){li.append(node('span',msg.text,'chat-system'));return li}
    const avatar=msg.kind==='agent'?chatAvatar(msg.role):msg.kind==='admin'||msg.kind==='result'?chatAvatar(null,'A'):msg.kind==='boss'?chatAvatar(null,'👔'):chatAvatar(null,msg.emoji);
    if(msg.kind==='boss')avatar.classList.add('chat-boss-avatar');
    if(msg.kind==='admin'||msg.kind==='result')avatar.classList.add('chat-admin-avatar');
    const bubble=node('div',undefined,'chat-bubble'),head=node('div',undefined,'chat-head');
    head.append(node('strong',msg.kind==='agent'?msg.role:msg.kind==='admin'||msg.kind==='result'?'Agent Exchange':msg.kind==='boss'?'Paper Trader':msg.name));
    if(msg.kind==='boss')head.append(node('span','boss','chat-badge boss'));
    if(msg.kind==='agent')head.append(node('span',msg.llm?'🤖 LLM · Gemini':'⚙️ code','chat-badge'+(msg.llm?' llm':'')));
    if(msg.kind==='admin')head.append(node('span','admin','chat-badge admin'));
    if(msg.kind==='result')head.append(node('span','📢 results','chat-badge admin'));
    if(msg.kind==='debate')head.append(node('span','🤖 Gemini','chat-badge llm'));
    head.append(node('time',utc(msg.time).replace(/^\d+ \w+,? /,'')));
    bubble.append(head);
    if(msg.kind==='agent'){
        bubble.append(node('p',msg.quip));
        if(msg.evidence)bubble.append(node('blockquote',msg.evidence));
        bubble.append(node('span',msg.vote==='abstain'?'🗳️ abstained (no data)':msg.vote==='neutral'?'🗳️ neutral · '+percent(msg.p):'🗳️ voted '+msg.vote.toUpperCase()+' · '+percent(msg.p),'chat-vote '+msg.vote));
    }else bubble.append(node('p',msg.text));
    if(msg.lines){const list=node('ul',undefined,'chat-lines');msg.lines.forEach(x=>list.append(node('li',x)));bubble.append(list)}
    li.append(avatar,bubble);return li;
}
function chatTyping(name){const li=node('li',undefined,'chat-msg chat-typing');li.append(node('span',name+' is typing'),node('i'),node('i'),node('i'));return li}
function chatRenderPoll(f,votes){
    const poll=$('chat-poll');poll.replaceChildren();
    poll.append(node('div','POLL','eyebrow'),node('h3','Will BTC be higher in 24h?'));
    const total=votes.length;
    for(const [key,label] of [['up','📈 Up'],['down','📉 Down'],['neutral','🤷 Neutral (50%)'],['abstain','😴 Abstain (no data)']]){
        const who=votes.filter(v=>v.vote===key),row=node('div',undefined,'poll-option '+key);
        const top=node('div',undefined,'poll-top');top.append(node('span',label),node('strong',String(who.length)));
        const bar=node('div',undefined,'poll-bar'),fill=node('i');fill.style.width=(who.length/13*100)+'%';bar.append(fill);
        const faces=node('div',undefined,'poll-faces');who.forEach(v=>{const icon=agentIcon(v.role);if(icon){icon.title=v.role;faces.append(icon)}});
        row.append(top,bar,faces);poll.append(row);
    }
    poll.append(node('div',total+' of 13 voted','poll-count'));
    if(f&&total===13){const res=node('div',undefined,'poll-result');const m=f.methods||{};
        for(const [label,v] of [['Majority vote',m.vote],['Simple average',m.average],['Agent Exchange',m.exchange]]){const r=node('div');r.append(node('span',label),node('strong',percent(v)+' up'));res.append(r)}
        poll.append(res)}
}
function chatReset(){
    clearTimeout(chatTimer);chatTimer=null;chatRun++;
    const s=chatSource(),f=s?.forecast,feed=$('chat-feed');feed.replaceChildren();
    $('chat-play').disabled=!f;$('chat-skip').disabled=!f;
    setText('chat-sub',f?(s.live?'Latest forecast '+utc(f.created_at):chatIsBacktest(f)?'🕰️ Backtest · '+chatDayLabel(f):'Archived forecast '+utc(f.created_at))+' · press play':'No forecast yet');
    feed.append(node('li',f?'▶ Press "Play chat" to watch the agents talk it out.':chatSel==='live'?'Generate a 24h forecast, or pick a past day from the archive.':'That forecast is no longer available.','chat-empty'));
    chatRenderPoll(f,[]);
}
function chatPollNote(label,value,cls){const box=node('div',undefined,'poll-boss'+(cls?' '+cls:''));box.append(node('span',label),node('strong',value));$('chat-poll').append(box)}
/* Boss paper trader: same thresholds as PaperStore.decide in dashboard/service.py (BUY >= 55%, SELL <= 45%). */
const BOSS_BUY=.55,BOSS_SELL=.45;
// Replay-only sensitivity: thresholds matched to the Exchange's observed range (chosen after seeing the data).
const BOSS_SENS={buy:.52,sell:.46};
let chatBossMode='live';   // replay-only switch; not stored in the browser (team rule: no browser storage in app.js)
const chatThresholds=live=>chatBossMode==='sens'&&!live?BOSS_SENS:{buy:BOSS_BUY,sell:BOSS_SELL};
let chatRun=0;
const chatSleep=ms=>new Promise(r=>setTimeout(r,ms));
function bossIntro(f,live){
    const p=f.methods?.exchange,th=chatThresholds(live);
    if(typeof p!=='number')return "Alright team 👔 No usable team forecast this round, so I'm not touching the money. HOLD 🤚";
    const settings=state?.paper_settings,size=settings?.configured?Number(settings.buy_fraction)*100:null;
    const requested=p>=th.buy?'BUY':p<=th.sell?'SELL':'HOLD';
    let text='Alright team 👔 I read the poll. The Exchange says '+percent(p)+' up. My rule'+(th===BOSS_SENS?' (sensitivity check, picked after seeing the data)':'')+': BUY at '+num(th.buy*100,0)+'% or more, SELL at '+num(th.sell*100,0)+'% or less, otherwise HOLD. ';
    if(requested==='BUY'){
        text+=percent(p)+' clears the bar → BUY 🟢'+(size!==null?' ('+num(size,1)+'% of our cash)':'')+'. BTC can never be more than 25% of the account.';
        if(!f.cap_feasible)text+=" …but fewer than 5 agents are active, so the 20% influence cap can't be enforced, and the risk desk blocks new BUYs. HOLD instead.";
    }else if(requested==='SELL')text+=percent(p)+' is low enough → SELL 🔴 whatever BTC we hold.';
    else text+=percent(p)+' is in the no-trade zone → HOLD 🤚';
    return text;
}
async function bossExecute(f){
    const before=portfolioValuation?.portfolio??null;
    if(state?.paper?.forecast_id===f.id)return {result:state.paper,before:null};
    if(!state?.paper_settings?.configured)return {blocked:'Paper trading is not switched on yet. Enable it under "Paper decision & risk controls", then press play again.',before};
    const age=(Date.now()-Date.parse(f.created_at))/1000;
    if(age>170)return {blocked:'This forecast is '+Math.max(3,Math.round(age/60))+' minutes old. I only trade on forecasts less than 3 minutes old, so no one trades on stale information. Generate a fresh forecast and press play.',before};
    if(state?.busy)return {blocked:'The dashboard is busy with another action. Try again in a moment.',before};
    try{await post('paper')}catch(error){return {blocked:error.message,before}}
    for(let i=0;i<40&&state?.paper?.forecast_id!==f.id;i++){await chatSleep(250);await refreshState()}
    await refreshPortfolio();
    return state?.paper?.forecast_id===f.id?{result:state.paper,before}:{blocked:'The paper decision did not come back in time; check the Paper decision panel.',before};
}
function bossReceipt(outcome){
    const li=node('li',undefined,'chat-msg chat-boss'),avatar=chatAvatar(null,'👔');avatar.classList.add('chat-boss-avatar');
    const bubble=node('div',undefined,'chat-bubble'),head=node('div',undefined,'chat-head');
    head.append(node('strong','Paper Trader'),node('span','boss','chat-badge boss'));bubble.append(head);
    const r=outcome.result,usd=v=>v==null?'—':money(Number(v)),btc=v=>v==null?'—':num(Number(v),6)+' BTC';
    let headline,quip,cls;
    if(!r){headline='🚫 No trade: '+outcome.blocked;quip='Account unchanged.';cls='blocked'}
    else{
        const status=r.risk?.status??(r.trade?'executed':r.requested!=='HOLD'?'blocked':'no_trade'),t=r.trade;
        if(status==='executed'&&t){
            headline='✅ '+t.action+' executed: '+num(Number(t.quantity_btc),6)+' BTC at '+usd(t.execution_price_usd)+' (fee '+usd(t.fee_usd)+')';
            quip=t.action==='BUY'?"Money's in. Now we wait 24 hours to see who was right 🍿":'Took some chips off the table 💰';cls='executed';
        }else if(status==='blocked'||status==='halted'){headline='🚫 '+r.requested+' blocked by the risk desk: '+(r.risk?.reason??'risk check failed');quip='Rules are rules. Account unchanged.';cls='blocked'}
        else{headline='🤚 '+(r.requested==='SELL'?'SELL requested, but there was no BTC to sell':'HOLD: no order placed')+'.';quip='Cash is a position too 😎';cls='hold'}
    }
    bubble.append(node('p',headline,'boss-headline '+cls));
    const after=r?.portfolio??null,before=outcome.before;
    if(after||before){
        const table=node('div',undefined,'boss-ledger'),row=(a,b,c,d)=>{const x=node('div');x.append(node('span',a),node('span',b),node('span',c),node('strong',d));return x};
        table.append(row('', 'Before','→','After'));
        table.append(row('💵 Cash',before?usd(before.cash_usd):'—','→',after?usd(after.cash_usd):usd(before?.cash_usd)));
        table.append(row('₿ BTC held',before?btc(before.btc):'—','→',after?btc(after.btc):btc(before?.btc)));
        table.append(row('📊 Account value',before?usd(before.equity_usd):'—','→',after?usd(after.equity_usd):usd(before?.equity_usd)));
        bubble.append(table);
        const p=after??before;
        if(p)bubble.append(node('small','Since the 10,000 USD start: '+(Number(p.total_pnl_usd)<0?'−':'+')+money(Math.abs(Number(p.total_pnl_usd)))+' ('+num(Number(p.return_pct))+'%) · fees so far '+usd(p.total_fees_usd)+' · simulated money only','boss-foot'));
    }
    bubble.append(node('p',quip));
    li.append(avatar,bubble);return li;
}
function chatPollDecision(outcome){
    const poll=$('chat-poll'),box=node('div',undefined,'poll-boss'),r=outcome.result,t=r?.trade;
    const label=!r?'No trade':t?t.action+' '+num(Number(t.quantity_btc),6)+' BTC':r.risk?.status==='blocked'?r.requested+' blocked':'HOLD';
    box.append(node('span','👔 Boss decision'),node('strong',label));poll.append(box);
}
async function chatPlay(instant){
    const s=chatSource();if(!s?.forecast)return;
    clearTimeout(chatTimer);const run=++chatRun,f=s.forecast,msgs=chatScript(s),feed=$('chat-feed'),votes=[];
    feed.replaceChildren();chatRenderPoll(f,votes);
    setText('chat-sub',(s.live?'Latest forecast '+utc(f.created_at):chatIsBacktest(f)?'🕰️ Backtest · '+chatDayLabel(f):'Archived forecast '+utc(f.created_at))+' · '+(instant?'full transcript':'replay'));
    const scroll=()=>{feed.scrollTop=feed.scrollHeight};
    const show=msg=>{feed.append(chatMessage(msg));if(msg.kind==='agent'){votes.push(msg);chatRenderPoll(f,votes)}
        if(msg.kind==='boss'&&msg.first&&!s.live)chatPollNote('👔 Boss decision (then)',chatBossRule(f));
        if(msg.kind==='result'){const o=chatOutcome(s.settlement);chatPollNote('📢 24h result',(o.up?'UP ':'DOWN ')+chatSigned(o.change)+'%',o.up?'result-up':'result-down')}
        scroll()};
    const wait=ms=>instant?Promise.resolve():new Promise(r=>{chatTimer=setTimeout(r,ms)});
    for(const msg of msgs){
        if(run!==chatRun)return;
        if(msg.kind==='system'){show(msg);await wait(700);continue}
        const name=msg.kind==='agent'?msg.role:msg.kind==='admin'||msg.kind==='result'?'Agent Exchange':msg.kind==='boss'||msg.kind==='bossTrade'?'Paper Trader':msg.name;
        const typing=chatTyping(msg.kind==='bossTrade'?name+' is placing the order':name);
        if(!instant){feed.append(typing);scroll()}
        if(msg.kind==='bossTrade'){
            const outcome=await bossExecute(f);await wait(600);
            if(run!==chatRun)return;typing.remove();feed.append(bossReceipt(outcome));chatPollDecision(outcome);scroll();continue;
        }
        await wait(['admin','boss','result'].includes(msg.kind)?1400:850);
        if(run!==chatRun)return;typing.remove();show(msg);
        await wait(['admin','boss','result'].includes(msg.kind)?900:450);
    }
    chatTimer=null;
}
/* ---------- Archive: replay any saved forecast and compare settled days ---------- */
let chatArchive=[],chatSel='live';
const chatIsBacktest=f=>f?.mode==='backtest';
const chatDayLabel=f=>chatIsBacktest(f)?new Date(f.decision_day+'T00:00:00Z').toLocaleDateString('en-GB',{timeZone:'UTC',day:'2-digit',month:'short',year:'numeric'})+' close':utc(f.created_at);
const chatScenario=f=>chatIsBacktest(f)?(f.scenario?.label??'Backtest'):null;
const chatGroupOf=f=>chatIsBacktest(f)?'🕰️ '+chatScenario(f):'Live forecasts';
let chatSummaries=[],chatFilter='Live forecasts';
const chatEventTag=(f,o)=>{const e=f?.events||{},bits=[];if(e.pinned)bits.push('📌');if(o&&o.change<=-5)bits.push('💥');else if(o&&o.change>=5)bits.push('🚀');if(e.next_day)bits.push('next day: '+e.next_day);else if(e.next_day_headlines)bits.push('🗞️ next day: '+e.next_day_headlines);else if(e.today)bits.push(e.today);return bits.join(' ')};
function chatSource(){
    if(chatSel==='live')return state?.forecast?{forecast:state.forecast,debate:state.debate,settlement:null,live:true}:null;
    const item=chatArchive.find(i=>i.forecast.id===chatSel);
    return item?{forecast:item.forecast,debate:null,settlement:item.settlement,live:false}:null;
}
function chatBossRule(f){
    const p=f.methods?.exchange;if(typeof p!=='number')return 'HOLD';
    const th=chatThresholds(false),r=p>=th.buy?'BUY':p<=th.sell?'SELL':'HOLD';
    return r==='BUY'&&!f.cap_feasible?'HOLD':r;
}
function chatOutcome(st){
    if(!st?.settled||typeof st.reference_price!=='number'||typeof st.target_price!=='number')return null;
    return {up:!!st.went_up,start:st.reference_price,end:st.target_price,change:(st.target_price/st.reference_price-1)*100};
}
const chatBrier=(p,up)=>typeof p==='number'?(p-(up?1:0))**2:null;
const chatSigned=(v,d=2)=>(v<0?'−':'+')+num(Math.abs(v),d);
function chatResultMsg(f,st){
    const o=chatOutcome(st),m=f.methods||{},right=[],wrong=[];
    for(const a of f.agents){const v=chatVote(a);if(v==='up'||v==='down')((v==='up')===o.up?right:wrong).push(a.role)}
    const roleOf=Object.fromEntries(f.agents.map(a=>[a.agent,a.role]));
    const bets=(st.bets||[]).map(b=>({role:roleOf[b.agent]??b.agent,change:Number(b.change)})).filter(b=>Number.isFinite(b.change)).sort((a,b)=>b.change-a.change);
    const lines=['✅ Called it: '+(right.join(', ')||'nobody'),'❌ Missed: '+(wrong.join(', ')||'nobody')];
    if(f.events?.next_day)lines.unshift('📰 What happened: '+f.events.next_day);
    else if(f.events?.next_day_headlines)lines.unshift('🗞️ From that day\'s headlines: '+f.events.next_day_headlines);
    if(bets.length)lines.push('🪙 Coins after settling: biggest winner '+bets[0].role+' ('+chatSigned(bets[0].change)+'), biggest loser '+bets.at(-1).role+' ('+chatSigned(bets.at(-1).change)+'). Winners get more say next time.');
    lines.push('🎯 Scores (Brier, lower is better): Exchange '+num(chatBrier(m.exchange,o.up),3)+' · Vote '+num(chatBrier(m.vote,o.up),3)+' · Average '+num(chatBrier(m.average,o.up),3));
    return {kind:'result',time:f.contract?.target_time??st.settled_at,
        text:'⏰ 24 hours later: BTC went from '+money(o.start)+' to '+money(o.end)+' ('+chatSigned(o.change)+'%) → it went '+(o.up?'UP 📈':'DOWN 📉')+'.',lines};
}
function bossReview(f,st){
    const rule=chatBossRule(f),o=chatOutcome(st);
    if(!o)return "Waiting for the result like everyone else 🍿";
    const settings=state?.paper_settings,frac=settings?.configured?Number(settings.buy_fraction):.10;
    if(rule==='BUY'){const pnl=10000*frac*o.change/100;return 'Hindsight 👔: a '+num(frac*100,0)+'% BUY on a 10,000 USD account would have '+(pnl>=0?'made':'lost')+' about '+money(Math.abs(pnl))+' before fees (hypothetical; no trade was placed from this replay).'}
    if(rule==='SELL')return 'Hindsight 👔: a fresh account has no BTC to sell, so we sat in cash while BTC moved '+chatSigned(o.change)+'%. '+(o.change<0?'Dodged that drop 😎':'Missed that move 🤷');
    return 'Hindsight 👔: we sat in cash while BTC moved '+chatSigned(o.change)+'%. '+(o.change>0?'Missed a bit of upside, but took no risk 🤷':'Dodged that drop 😎');
}
async function chatLoadArchive(){
    try{const r=await fetch('/api/archive');if(!r.ok)throw new Error();const j=await r.json();chatArchive=j.items||[];chatSummaries=j.summaries||[]}catch{chatArchive=[];chatSummaries=[]}
    const sel=$('chat-day'),keep=chatSel;sel.replaceChildren();
    const live=node('option','Latest forecast (live)');live.value='live';sel.append(live);
    const groups=new Map([['Live forecasts',node('optgroup')]]);
    for(const item of chatArchive){const f=item.forecast,o=chatOutcome(item.settlement),key=chatGroupOf(f);
        if(!groups.has(key))groups.set(key,node('optgroup'));
        const tag=chatEventTag(f,o),opt=node('option',chatDayLabel(f)+' · '+(o?(o.up?'UP ':'DOWN ')+chatSigned(o.change,1)+'%':'pending')+(tag?' · '+tag:'')+' · Exchange '+percent(f.methods?.exchange));
        opt.value=f.id;groups.get(key).append(opt)}
    const fsel=$('chat-archive-filter'),fkeep=chatFilter;fsel.replaceChildren();
    for(const [label,g] of groups){if(!g.children.length)continue;g.label=label;sel.append(g);const o=node('option',label);o.value=label;fsel.append(o)}
    chatSel=[...sel.options].some(o=>o.value===keep)?keep:'live';sel.value=chatSel;
    chatFilter=[...fsel.options].some(o=>o.value===fkeep)?fkeep:(fsel.options[0]?.value??'');fsel.value=chatFilter;
    chatRenderArchive();
}
function chatRenderArchive(){
    const body=$('chat-archive-rows');body.replaceChildren();
    const list=chatArchive.filter(i=>chatGroupOf(i.forecast)===chatFilter),settled=[];
    for(const item of list){
        const f=item.forecast,m=f.methods||{},o=chatOutcome(item.settlement),row=node('tr');
        if(o)settled.push({m,o});
        const tag=chatEventTag(f,o);
        const cells=[chatIsBacktest(f)?'🕰️ Backtest':'Live',chatDayLabel(f)+(tag?'  '+tag:''),String(f.active_count??'—')+' / 13',percent(m.exchange),percent(m.vote),percent(m.average),chatBossRule(f),
            o?money(o.start)+' → '+money(o.end):'—'];
        cells.forEach(c=>row.append(node('td',c)));
        const res=node('td');res.append(node('span',o?(o.up?'📈 UP ':'📉 DOWN ')+chatSigned(o.change)+'%'+(o.change<=-5?' 💥':''):'⏳ due '+utc(f.contract?.target_time),'chat-vote '+(o?(o.up?'up':'down'):'abstain')));row.append(res);
        row.append(node('td',o?num(chatBrier(m.exchange,o.up),3):'—'),node('td',o?num(chatBrier(m.vote,o.up),3):'—'));
        const act=node('td'),btn=node('button','▶ Replay','report-button');
        btn.addEventListener('click',()=>{chatSel=f.id;$('chat-day').value=f.id;chatReset();$('groupchat').scrollIntoView({behavior:'smooth'});chatPlay(false)});
        act.append(btn);row.append(act);body.append(row);
    }
    if(!list.length){const row=node('tr'),cell=node('td','No saved forecasts yet.','empty-inline');cell.colSpan=12;row.append(cell);body.append(row)}
    const summary=chatSummaries.find(s=>'🕰️ '+s.label===chatFilter);
    if(summary){
        const b=summary.brier,boss=summary.boss_paper,big=summary.big_down_days||[];
        setText('chat-archive-note',summary.days+' days ('+summary.start+' → '+summary.end+') · BTC '+chatSigned(summary.btc_change_pct,1)+'%, worst drawdown '+num(summary.max_drawdown_pct,1)+'%'+
            ' · score (Brier, lower is better): Exchange '+num(b.exchange,3)+', vote '+num(b.vote,3)+', average '+num(b.average,3)+', always-50% 0.250'+
            ' · Exchange right on direction '+summary.exchange_direction_hits+' of '+summary.days+' (always saying "down": '+summary.always_down_hits+')'+
            ' · '+big.length+' 💥 days (BTC −5% or worse): Exchange averaged '+(summary.mean_exchange_before_big_drops==null?'—':percent(summary.mean_exchange_before_big_drops))+' up the day before'+
            (chatBossMode==='sens'?(summary.boss_paper_sensitivity?
              ' · Boss ⚖️ sensitivity 52/46 (picked after seeing the data): '+summary.boss_paper_sensitivity.trades+' trades, '+chatSigned(summary.boss_paper_sensitivity.return_pct,1)+'% vs buy-and-hold '+chatSigned(boss.buy_and_hold_pct,1)+'%':
              ' · Boss ⚖️ sensitivity 52/46: result not computed for this window yet (rerun the backtest)'):
              ' · Boss 55/45: '+boss.trades+' trades, '+chatSigned(boss.return_pct,1)+'% vs buy-and-hold '+chatSigned(boss.buy_and_hold_pct,1)+'%'));
        return;
    }
    const mean=k=>settled.length?settled.reduce((s,x)=>s+chatBrier(x.m[k],x.o.up),0)/settled.length:null;
    const ups=settled.filter(x=>x.o.up).length,hits=settled.filter(x=>typeof x.m.exchange==='number'&&(x.m.exchange>.5)===x.o.up).length,pending=list.length-settled.length;
    setText('chat-archive-note',settled.length?settled.length+' settled'+(pending?' · '+pending+' pending':'')+' · BTC up '+ups+' · Exchange right on direction '+hits+' · mean score Exchange '+num(mean('exchange'),3)+', vote '+num(mean('vote'),3)+', average '+num(mean('average'),3)+' (Brier, lower is better; always saying 50% scores 0.250)':list.length+' saved · none settled yet (each settles 24 hours after it was made)');
}
(function chatInit(){
    if(!$('chat-feed'))return;
    $('chat-play').addEventListener('click',()=>chatPlay(false));
    $('chat-skip').addEventListener('click',()=>chatPlay(true));
    $('chat-day').addEventListener('change',e=>{chatSel=e.target.value;chatReset();const it=chatArchive.find(i=>i.forecast.id===chatSel);if(it){chatFilter=chatGroupOf(it.forecast);$('chat-archive-filter').value=chatFilter;chatRenderArchive()}});
    $('chat-archive-filter').addEventListener('change',e=>{chatFilter=e.target.value;chatRenderArchive()});
    const syncBoss=()=>document.querySelectorAll('[data-boss]').forEach(b=>{const on=b.dataset.boss===chatBossMode;b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on))});
    document.querySelectorAll('[data-boss]').forEach(b=>b.addEventListener('click',()=>{chatBossMode=b.dataset.boss;syncBoss();chatReset();chatRenderArchive()}));
    syncBoss();
    chatReset();chatLoadArchive();
    const base=render;
    render=function(s){base(s);const id=s?.forecast?.id??null;if(id!==chatForecastId){chatForecastId=id;chatLoadArchive();if(chatSel==='live')chatReset()}};
})();
