'use strict';
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="demo-token"]').content;
const roles=[
    ['Chart','chart','Technical trends','agent-chart'],
    ['Borrowing','borrowing','Funding & open interest','agent-borrowing'],
    ['Network','network','On-chain activity','agent-network'],
    ['Miner','miner','Hash rate & fees','agent-miner'],
    ['Stablecoin','stablecoin','Liquidity & peg stability','agent-stablecoin'],
    ['ETF','etf','Institutional flows','agent-etf'],
    ['News','news','News & events','agent-news'],
    ['Mood','mood','Market sentiment','agent-mood'],
    ['World economy','world-economy','Dollar & Treasury yields','agent-world-economy'],
    ['Betting odds','betting','Prediction-market evidence','agent-betting'],
    ['Crash','crash','Tail risk & drawdown','agent-crash'],
    ['Long-term','long-term','Long-horizon structure','agent-long-term'],
    ['Contrarian','contrarian','Consensus reversal','agent-contrarian'],
];
let state=null,market={candles:[]},signature='',marketVersion=null,marketFetching=false,filter='all',search='',range=90,chartStyle='candles',showEma=true,hover=null,plot=null,replayVersion=null;
const percent=v=>typeof v==='number'&&Number.isFinite(v)?(v*100).toFixed(1)+'%':'—';
const num=(v,places=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{minimumFractionDigits:places,maximumFractionDigits:places}):'—';
const money=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v))?'$'+num(Number(v)):'—';
const utc=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.valueOf())?'—':d.toLocaleString('en-GB',{timeZone:'UTC',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false})+' UTC'};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function agentIcon(role){
    const spec=roles.find(r=>r[0]===role);
    if(!spec)return null;
    const icon=node('span',undefined,'agent-icon '+spec[3]);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const use=document.createElementNS('http://www.w3.org/2000/svg','use');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    use.setAttribute('href','#i-agent-'+spec[1]);svg.append(use);icon.append(svg);
    return icon;
}
function setText(id,text){$(id).textContent=text}
function toast(text){setText('toast',text);$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,5500)}
async function post(path,body){const r=await fetch('/api/'+path,{method:'POST',headers:{'X-Demo-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});let result;try{result=await r.json()}catch{throw new Error('The local service could not complete this request.')}if(!r.ok)throw new Error(result.error||'Request failed');return result}
async function refreshMarket(){if(marketFetching)return;marketFetching=true;try{const r=await fetch('/api/market');if(!r.ok)throw new Error();market=await r.json();marketVersion=state?.sources?.btc_prices?.received_at??null;hover=null;renderMarket();drawPrice()}catch{toast('Chart data could not be loaded from the local service.')}finally{marketFetching=false}}
async function refreshState(){try{const r=await fetch('/api/state');if(!r.ok)throw new Error();const next=await r.json(),sig=JSON.stringify(next);state=next;if(sig!==signature){signature=sig;render(next)}const version=next.sources?.btc_prices?.received_at??null;if(version!==marketVersion||!refresh.initialMarket){refresh.initialMarket=true;await refreshMarket()}$('connection').replaceChildren(node('i'),document.createTextNode('Local service connected'))}catch{$('connection').replaceChildren(node('i'),document.createTextNode('Service disconnected'));$('connection').style.color='#ed929f';setText('status-text','Local service disconnected. Restart python -m dashboard to reconnect.')}}
let refreshTask=null,refreshTimer=null;
async function refresh(){
    if(refreshTask)return refreshTask;
    clearTimeout(refreshTimer);
    refreshTask=refreshState();
    try{await refreshTask}finally{refreshTask=null;refreshTimer=setTimeout(refresh,state?.busy?200:2000)}
}
function duration(ms){return typeof ms==='number'?(ms<1?ms.toFixed(2)+' ms':ms<1000?ms.toFixed(1)+' ms':(ms/1000).toFixed(2)+' s'):'—'}
let activitySignature='';
function renderActivity(activity){
    const a=activity??{status:'idle',phase:'Ready for a forecast',agents:[],events:[],evaluated_count:0};
    const statusNames={idle:'READY',running:'IN PROGRESS',completed:'COMPLETE',failed:'INTERRUPTED'};
    setText('activity-state',statusNames[a.status]??'READY');
    $('activity-state').className='label activity-label '+a.status;
    setText('activity-phase',a.phase);
    $('activity-indicator').className='activity-indicator '+a.status;
    setText('activity-time',a.status==='idle'?'—':duration(a.elapsed_ms));
    setText('activity-caption',a.status==='idle'?'All 13 agents are queued until you generate a forecast.':
        a.evaluated_count+' of 13 evaluated · '+(a.forecasted_count??0)+' forecasts · '+(a.abstained_count??0)+' abstentions'+(a.failed_count?' · '+a.failed_count+' failures':''));
    $('activity-progress-fill').style.width=(a.evaluated_count/13*100)+'%';
    $('activity-progress').setAttribute('aria-valuenow',String(a.evaluated_count));
    const sig=JSON.stringify([a.run_id,a.status,a.agents,a.events,state?.busy,state?.gemini_configured,state?.sources?.betting_odds]);
    if(sig===activitySignature)return;
    activitySignature=sig;
    const grid=$('activity-agents');grid.replaceChildren();
    const labels={queued:'Queued',running:'Evaluating',forecasted:'Forecast returned',abstained:'Abstained',failed:'Failed',not_run:'Not reached'};
    roles.forEach(([role,glyph,,color])=>{
        const record=a.agents.find(x=>x.role===role)??{role,status:'queued'},card=node('div',undefined,'activity-agent '+color+' '+record.status);
        const top=node('div',undefined,'activity-agent-top');top.append(agentIcon(role),node('strong',role));
        const stamp=node('span',labels[record.status]??record.status,'activity-agent-status');
        const value=node('div',undefined,'activity-agent-value');
        value.append(node('span',typeof record.p_up==='number'?percent(record.p_up)+' P(up)':record.status==='running'?'Evaluating evidence…':record.status==='abstained'?'No usable forecast':'—'),
            node('small',duration(record.duration_ms??record.running_ms)));
        card.append(top,stamp,value);
        const connectGemini = !state?.gemini_configured && requiresGemini(record.summary);
        const summary = forecastReason(role, record.summary);
        if(summary){card.title=summary;card.append(node('p',summary,'activity-agent-reason'))}
        if(connectGemini){
            const connect=node('button','Connect Gemini','report-button');
            connect.disabled=!!state?.busy;
            connect.addEventListener('click',openGeminiDialog);
            card.append(connect);
        }
        if(record.result){const button=node('button','View returned evidence ↗','report-button');button.addEventListener('click',()=>openEvidence(role,roles.find(r=>r[0]===role)[2],record.result));card.append(button)}
        grid.append(card);
    });
    const methodBox=$('activity-methods');methodBox.replaceChildren();
    if(a.methods){for(const [key,title]of [['exchange','Agent Exchange'],['average','Simple average'],['vote','Majority vote']]){const item=node('div');item.append(node('span',title),node('strong',percent(a.methods[key])));methodBox.append(item)}}
    const list=$('activity-log'),atBottom=list.scrollHeight-list.scrollTop-list.clientHeight<45;
    list.replaceChildren();
    const events=a.events??[];
    if(!events.length)list.append(node('li','Execution events will appear here.','activity-empty'));
    for(const event of events){
        const item=node('li',undefined,event.kind),time=node('time','+'+duration(event.elapsed_ms)),body=node('div');
        const name=node('strong',undefined,'activity-event-name'),icon=agentIcon(event.role);
        if(icon)name.append(icon);
        name.append(node('span',event.role??(event.kind.startsWith('aggregation')?'Agent Exchange':'Forecast run')));
        body.append(name,node('span',event.message));
        if(event.duration_ms!==undefined)body.append(node('small',labels[event.status]+' · '+duration(event.duration_ms)+(typeof event.p_up==='number'?' · '+percent(event.p_up)+' P(up)':'')));
        item.append(time,body);list.append(item);
    }
    if(atBottom)list.scrollTop=list.scrollHeight;
    setText('activity-event-count',events.length+' events');
    setText('activity-summary',a.status==='completed'?'All specialists evaluated. Only validated forecasts contribute; the 24-hour outcome is still pending.':
        a.status==='failed'?'This run stopped before completion. Unreached agents are not presented as completed.':
        a.current_agent==='Contrarian'?'Contrarian reads the completed peer forecasts and runs last.':
        a.status==='running'?'Specialists run sequentially; fast numerical agents may finish between screen updates. Their events and timings stay visible.':
        'Queued → evaluate evidence → validate forecast → combine.');
}

function render(s){renderActivity(s.activity);renderLiveCoins(s);renderFakeNewsReplay(s);setText('status-text',s.message);$('status').classList.toggle('busy',s.busy);setText('notice-end',s.busy?'WORKING':'PAPER MODE');document.querySelectorAll('[data-action]').forEach(b=>{const a=b.dataset.action;b.disabled=s.busy||(['paper','debate'].includes(a)&&!s.forecast)||(a==='debate'&&!s.gemini_configured)||(a==='real-replay'&&!s.sources?.btc_prices)||(a==='fake-news-replay'&&(!s.gemini_configured||!s.forecast?.news_capture))});document.querySelectorAll('[data-open-key]').forEach(b=>b.disabled=s.busy);const f=s.forecast,p=f?.methods?.exchange??null,count=f?.active_count??s.activity?.forecasted_count??0;setText('prob',percent(p));setText('summary-prob',percent(p));setText('strip-coverage',count+' / 13');setText('active-count',count);$('count').replaceChildren(document.createTextNode(String(count)+' '),node('small','/ 13'));setText('filter-active-count',count);setText('filter-missing-count',f?13-count:(s.activity?.abstained_count??0)+(s.activity?.failed_count??0));setText('agent-summary',s.activity?.status==='running'?(s.activity.evaluated_count+' / 13 EVALUATED'):f||s.activity?.status==='failed'?count+' ACTIVE · '+(s.activity?.abstained_count??0)+' ABSTAINED · '+(s.activity?.failed_count??0)+' FAILED':'13 QUEUED');$('coverage-fill').style.width=(count/13*100)+'%';const dots=$('coverage-dots');dots.replaceChildren(...roles.map((_,i)=>node('i',undefined,i<count?'on':'')));$('gauge-arc').setAttribute('stroke-dasharray',p===null?'0 100':(p*100)+' 100');const bias=p===null?(f?'No usable forecast':'Awaiting forecast'):p>.5?'Upward bias':p<.5?'Downward bias':'Neutral outlook';setText('bias',bias);$('bias').className='signal-pill'+(p===null?'':p>.5?' up':p<.5?' down':'');setText('summary-signal',p===null?(f?'No validated forecasts available':'Run a forecast to reveal the signal'):bias+' · experimental probability');setText('coverage-note',!f?'Evidence determines participation':f.cap_feasible?'20% influence cap is feasible':'Displayed forecast: fewer than 5 active · BUY blocked');for(const method of ['exchange','average','vote'])setText('method-'+method,percent(f?.methods?.[method]));const window=$('forecast-window').querySelector('span');window.textContent=f?'Target: '+utc(f.contract.target_time)+' · strict higher price':'Outcome available 24 hours after forecast.';setText('cap-state',f?(f.cap_feasible?'Feasible for displayed forecast':'Infeasible: forecast BUY blocked'):'Requires ≥ 5 active');const configured=s.gemini_configured;setText('key-label',configured?'Gemini configured':'Connect Gemini');setText('model-state',configured?(s.gemini?.source==='session'?'Session key configured':'Environment key configured'):'Session key not configured');renderKeyStatus();renderAgents();renderSources();renderMarket();renderPaper();setText('contract',f?JSON.stringify({contract:f.contract,reference:f.quote,methods:f.methods,weights:f.weights,coins:f.coins,coins_basis:f.coins_basis,live_learning:s.learning,limitations:f.limitations,usage:f.usage,debate:s.debate},null,2):'Generate a forecast to inspect its event definition.');if(s.replay&&replayVersion!==s.replay.output){replayVersion=s.replay.output;renderReplay()}drawReplay()}
function renderMarket(){const bars=market.candles||[],last=bars.at(-1),prior=bars.at(-2),f=state?.forecast;const price=f?.quote?.price??last?.close??null;$('btc-price').replaceChildren(document.createTextNode(price===null?'—':num(price)),node('small','USDT'));let change=null,basis='Waiting for market data';if(f&&last){change=(f.quote.price/last.close-1)*100;basis='since last daily close'}else if(last&&prior){change=(last.close/prior.close-1)*100;basis='last completed UTC day'}setText('price-change',change===null?basis:(change>=0?'↗ +':'↘ ')+num(change)+'% '+basis);$('price-change').className=change===null?'':change>=0?'positive':'negative';setText('price-basis',f?'Reference '+utc(f.quote.bar_close_time):last?'Closed '+last.date:'No observation yet');setText('daily-high',last?.high!==undefined?num(last.high):'—');setText('daily-low',last?.low!==undefined?'Low '+num(last.low):'Low —');setText('daily-volume',last?.volume!==undefined?num(last.volume,0)+' BTC':'—');$('chart-empty').hidden=bars.length>0;setText('chart-date',last?'Last candle · '+last.date+' UTC':'No candle history loaded');readout(last);$('candles-button').disabled=bars.length>0&&!bars.every(b=>b.high!==undefined&&b.low!==undefined);}
function readout(bar){const r=$('chart-readout');if(!bar){r.replaceChildren(document.createTextNode('Completed UTC daily candles '),node('span','· Refresh market data to begin'));return}r.replaceChildren(node('span',bar.date+'  '),document.createTextNode('O '+num(bar.open)+'  '+(bar.high!==undefined?'H '+num(bar.high)+'  L '+num(bar.low)+'  ':'')+'C '+num(bar.close)),node('span','  · 1D / UTC'))}
function renderAgents(){const f=state?.forecast,body=$('agent-rows');body.replaceChildren();let shown=0;roles.forEach(([role,glyph,perspective,color],i)=>{const progress=state?.activity?.agents?.find(a=>a.role===role),a=f?.agents.find(a=>a.role===role)??progress?.result,active=!!a&&a.p_up!==null&&a.p_up!==undefined;const queued=!a&&(!progress||['queued','not_run'].includes(progress.status)),running=progress?.status==='running';if(filter==='active'&&!active||filter==='missing'&&(active||running||queued)||search&&!((role+' '+perspective).toLowerCase().includes(search)))return;shown++;const row=node('tr');row.append(node('td',String(i+1).padStart(2,'0')));const name=node('td'),wrap=node('div',undefined,'agent-name');wrap.append(agentIcon(role),node('span',role));name.append(wrap);row.append(name,node('td',perspective,'agent-perspective'));const direction=!active?'—':a.p_up>.5?'Bullish':a.p_up<.5?'Bearish':'Neutral',signal=node('td');signal.append(node('span',direction,'signal '+(!active?'missing':a.p_up>.5?'up':a.p_up<.5?'down':'neutral')));row.append(signal);const prob=node('td'),group=node('div',undefined,'prob-cell'),bar=node('span',undefined,'prob-bar'),fill=node('i');fill.style.width=active?a.p_up*100+'%':'0%';bar.append(fill);group.append(node('strong',percent(a?.p_up)),bar);prob.append(group);row.append(prob,node('td',active?percent(f?.weights?.[a.agent]):'—'));const status=node('td'),label=node('span',undefined,'status-cell'+(active?' active':''));label.append(node('i'),document.createTextNode(running?'Evaluating':progress?.status==='not_run'?'Not reached':queued?'Queued':active?'Active':progress?.status==='failed'?'Failed':'Abstaining'));status.append(label);row.append(status);const evidence=node('td'),button=node('button',a?'View report ↗':'Requirements ↗','report-button');button.addEventListener('click',()=>openEvidence(role,perspective,a));evidence.append(button);row.append(evidence);body.append(row)});if(!shown){const row=node('tr'),cell=node('td','No specialists match this filter.','empty-inline');cell.colSpan=8;row.append(cell);body.append(row)}setText('table-caption',f?shown+' specialists shown · forecast '+utc(f.created_at):state?.activity?.status==='running'?shown+' specialists shown · results arrive as each returns':shown+' registered specialists · no forecast yet')}
function openEvidence(role,perspective,a){$('evidence-title').replaceChildren(agentIcon(role),node('span',role));const body=$('evidence-body');body.replaceChildren(node('p',perspective));const summary=node('div',undefined,'evidence-summary');summary.append(node('span','P(up) '+percent(a?.p_up)),node('span',a?.audit?.passed?'Evidence checks passed':a?'No validated forecast':'Not yet evaluated'));body.append(summary);for(const [title,items]of [['Evidence',a?.evidence??['Collect the required source data and run a forecast to inspect this specialist.']],['Limitations',a?.limitations??[]],['Validation',a?.audit?.reasons??[]]]){if(!items.length)continue;const section=node('section',undefined,'evidence-section'),list=node('ul');section.append(node('h3',title));for(const item of items)list.append(node('li',String(item)));section.append(list);body.append(section)}body.append(node('p','Rules are experimental and uncalibrated. Evidence checks do not independently prove that a claim is true.'));$('evidence-dialog').showModal()}
function renderSources(){
    const sources=Object.entries(state?.sources??{}),count=sources.filter(([,v])=>v.status==='collected').length;
    setText('strip-sources',sources.length?count+' / '+sources.length:'—');
    setText('source-summary',sources.length?count+' RETRIEVED':'NOT COLLECTED');
    const names={
        btc_prices:[['Chart'],'Bitcoin price history','Binance · completed daily candles'],
        chain:[['Network','Miner'],'Network & miner metrics','Coin Metrics · daily observations'],
        macro:[['World economy'],'Macro market context','Yahoo · dollar index & Treasury yield'],
        news:[['News'],'Bitcoin news','Yahoo · timestamped articles'],
        mood:[['Mood'],'Market sentiment','Alternative.me · Fear & Greed'],
        borrowing:[['Borrowing'],'Funding & open interest','Binance USD-M · completed settlements'],
        stablecoin:[['Stablecoin'],'Stablecoin live quotes','CoinGecko · 15-minute snapshots'],
        stablecoin_history:[['Stablecoin'],'Stablecoin peg history','CoinGecko · 365 daily prices & volume (peg-stress model)'],
        stablecoin_long_history:[['Stablecoin'],'Stablecoin supply history','DefiLlama + Coinbase · daily since 2019 (liquidity model)'],
        etf_flows:[['ETF'],'ETF fund flows','Reviewed data/etf_flows.csv required'],
        betting_odds:[['Betting odds'],'Prediction markets','Recent timestamped Kalshi BTC trade required'],
    };
    const container=$('sources');container.replaceChildren();
    if(!sources.length){container.append(node('div','Source freshness and collection status appear here.','empty-inline'));return}
    for(const [name,value]of sources){
        const [identities,title,description]=names[name]??[[],name,''];
        const ok=value.status==='collected',row=node('div',undefined,'source-row'),main=node('div',undefined,'source-main');
        main.append(node('strong',title),node('small',description+(ok?' · '+utc(value.received_at):name==='betting_odds'&&value.reason?' · '+value.reason:'')));
        const status=node('span',undefined,'source-state'+(ok?'':' missing'));
        status.append(node('i',undefined,'status-dot'),document.createTextNode(ok?'Retrieved':'Needs setup'));
        const icons=node('span',undefined,'source-icons');identities.forEach(role=>icons.append(agentIcon(role)));
        row.append(icons,main,status);
        if(value.reason)row.title=String(value.reason);container.append(row);
    }
}
function renderPaper(){
    const p=state?.paper;
    if(!p){
        setText('equity','\u2014');setText('portfolio-sub','No paper decision displayed');
        setText('decision','No recorded decision yet.');setText('decision-action','Ready when you are');
        setText('decision-reason','Generate a forecast, then record a guarded paper decision.');
        setText('breaker-state','Not configured');setText('risk-label','PAPER ONLY');return;
    }
    setText('equity',money(p.portfolio.equity_usd));
    setText('portfolio-sub',p.portfolio.trade_count+' paper trades \u00b7 '+money(p.portfolio.total_fees_usd)+' in fees');
    const status=p.risk.status??(p.trade?'executed':p.requested!=='HOLD'?'blocked':'no_trade');
    const label=status==='executed'?p.trade.action+' executed':status==='blocked'||status==='halted'?p.requested+' blocked':'No trade executed';
    setText('decision-action',label+(p.synthetic?' \u00b7 synthetic paper example':' \u00b7 paper decision'));setText('decision-reason',p.risk.reason);
    setText('breaker-state',p.breaker.halted?'PAUSED \u00b7 '+p.breaker.reason:
        p.breaker.configured?'Armed \u00b7 configured policy':'Not configured');
    setText('risk-label',p.breaker.halted?'TRADING PAUSED':p.breaker.configured?'RISK CHECKED':'RISK NOT CONFIGURED');
    setText('decision',JSON.stringify(p,null,2));
}
function renderReplay(){renderReplayCoins();const r=state.replay,archive=r.mode.startsWith('ARCHIVED'),real=archive||r.mode.startsWith('REAL');setText('replay-badge',archive?'ARCHIVED LIVE FORECASTS':real?'REAL BTC HISTORY · PRICE-BASED AGENTS ONLY':'SYNTHETIC WALKTHROUGH · FAKE AGENTS & PRICES');$('replay-badge').className='replay-mode '+(real?'real':'synthetic');$('replay-empty').hidden=!!r.results?.length;setText('replay',JSON.stringify({mode:r.mode,summary:r.summary,baselines:r.baselines,pending:r.pending,problem:r.problem,output:r.output},null,2));const box=$('replay-results');box.replaceChildren();for(const result of Array.isArray(r.summary)?r.summary:[]){const card=node('div',undefined,'research-result'),names={exchange:'Agent Exchange',average:'Simple average',vote:'Majority vote'};card.append(node('span',names[result.method]??result.method),node('strong',typeof result.brier==='number'?result.brier.toFixed(4):'—'),node('small','Brier score · lower is better · '+result.evaluated_days+' evaluated days'));box.append(card)}}
function ema(values,period){let last=values[0]??0;const alpha=2/(period+1);return values.map(v=>(last=v*alpha+last*(1-alpha)))}
function canvasContext(id){const c=$(id),rect=c.getBoundingClientRect(),ratio=Math.min(window.devicePixelRatio||1,3),w=Math.max(1,rect.width),h=Math.max(1,rect.height);if(c.width!==Math.round(w*ratio)||c.height!==Math.round(h*ratio)){c.width=Math.round(w*ratio);c.height=Math.round(h*ratio)}const x=c.getContext('2d');x.setTransform(ratio,0,0,ratio,0,0);x.clearRect(0,0,w,h);return{x,w,h}}
function drawPrice(){const{x,w,h}=canvasContext('price-chart'),all=market.candles||[],bars=all.slice(-range),left=12,right=Math.max(left+10,w-65),top=14,bottom=h-45,priceBottom=bottom-40,volumeTop=priceBottom+18,n=bars.length;plot=null;x.font='8px ui-monospace, SFMono-Regular, monospace';if(!n){x.strokeStyle='#dce3df';for(let k=0;k<5;k++){const y=top+k*(priceBottom-top)/4;x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke()}return}const full20=ema(all.map(b=>b.close),20),full50=ema(all.map(b=>b.close),50),e20=full20.slice(-n),e50=full50.slice(-n),useCandles=chartStyle==='candles'&&bars.every(b=>b.high!==undefined&&b.low!==undefined);const vals=bars.flatMap((b,i)=>[useCandles?b.low:b.close,useCandles?b.high:b.close,...(showEma?[e20[i],e50[i]]:[])]);let lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||hi*.02;lo-=span*.08;hi+=span*.08;span=hi-lo;const py=v=>top+(hi-v)/span*(priceBottom-top),step=(right-left)/n,px=i=>left+(i+.5)*step;for(let k=0;k<5;k++){const v=hi-k*span/4,y=py(v);x.strokeStyle='#dce3df';x.lineWidth=.7;x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.fillStyle='#6f7f7a';x.fillText(num(v,0),right+9,y+3)}const ticks=Math.min(5,n);for(let k=0;k<ticks;k++){const i=Math.round(k*(n-1)/Math.max(1,ticks-1)),xx=px(i);x.strokeStyle='#e4e9e4';x.beginPath();x.moveTo(xx,top);x.lineTo(xx,bottom+8);x.stroke();x.fillStyle='#77867f';const text=bars[i].date.slice(5).replace('-','/');x.fillText(text,Math.max(left,Math.min(right-25,xx-13)),h-15)}const maxVolume=Math.max(0,...bars.map(b=>b.volume??0));if(maxVolume){bars.forEach((b,i)=>{if(b.volume===undefined)return;x.fillStyle=b.close>=b.open?'#4a91783b':'#b66d7338';const y=bottom-(b.volume/maxVolume)*(bottom-volumeTop);x.fillRect(px(i)-Math.max(1,step*.55)/2,y,Math.max(1,step*.55),Math.max(1,bottom-y))});x.fillStyle='#7b8a82';x.fillText('VOL · BTC',left+3,volumeTop+3)}if(useCandles){for(let i=0;i<n;i++){const b=bars[i],xx=px(i),up=b.close>=b.open;x.strokeStyle=up?'#34866d':'#b86c76';x.fillStyle=x.strokeStyle;x.lineWidth=Math.max(.6,Math.min(1,step*.4));x.beginPath();x.moveTo(xx,py(b.high));x.lineTo(xx,py(b.low));x.stroke();const y=Math.min(py(b.open),py(b.close)),height=Math.max(1,Math.abs(py(b.open)-py(b.close))),bw=Math.max(.7,Math.min(8,step*.62));x.fillRect(xx-bw/2,y,bw,height)}}else{const gradient=x.createLinearGradient(0,top,0,priceBottom);gradient.addColorStop(0,'#6e8fb026');gradient.addColorStop(1,'#6e8fb000');x.beginPath();bars.forEach((b,i)=>i?x.lineTo(px(i),py(b.close)):x.moveTo(px(i),py(b.close)));x.lineTo(px(n-1),priceBottom);x.lineTo(px(0),priceBottom);x.closePath();x.fillStyle=gradient;x.fill();line(bars.map(b=>b.close),'#5c7f9f',1.5)}function line(values,color,width){x.strokeStyle=color;x.lineWidth=width;x.beginPath();values.forEach((v,i)=>i?x.lineTo(px(i),py(v)):x.moveTo(px(i),py(v)));x.stroke()}if(showEma){line(e20,'#a88b50',1);line(e50,'#8e83a9',1)}const last=bars.at(-1),lastY=py(last.close);x.setLineDash([3,4]);x.strokeStyle='#59857466';x.beginPath();x.moveTo(left,lastY);x.lineTo(right,lastY);x.stroke();x.setLineDash([]);x.fillStyle='#dfebe2';x.fillRect(right+3,lastY-8,61,16);x.fillStyle='#3b6b55';x.fillText(num(last.close,0),right+7,lastY+3);if(hover!==null){const i=Math.max(0,Math.min(n-1,hover)),xx=px(i),b=bars[i],yy=py(b.close);x.setLineDash([3,4]);x.strokeStyle='#8b9d9399';x.beginPath();x.moveTo(xx,top);x.lineTo(xx,bottom+5);x.moveTo(left,yy);x.lineTo(right,yy);x.stroke();x.setLineDash([]);x.fillStyle='#65817a';x.beginPath();x.arc(xx,yy,2.5,0,Math.PI*2);x.fill();readout(b)}plot={left,right,step,bars,px,py}}
function drawReplay(){const{x,w,h}=canvasContext('replay-chart'),rows=state?.replay?.results??[],left=25,right=w-15,top=15,bottom=h-27;x.font='8px ui-monospace,monospace';for(const v of [0,.25,.5,.75,1]){const y=bottom-v*(bottom-top);x.strokeStyle=v===.5?'#c1cec5':'#dfe6df';x.setLineDash(v===.5?[3,4]:[]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.setLineDash([]);x.fillStyle='#7c8c82';x.fillText(String(Math.round(v*100)),0,y+3)}for(const [key,color]of [['exchange','#5d7c9d'],['average','#9a87ab'],['vote','#b0945e']]){x.strokeStyle=color;x.lineWidth=1.6;x.beginPath();let pen=false;rows.forEach((r,i)=>{const v=r[key]??r[key+'_clean'];if(typeof v!=='number'||!Number.isFinite(v)){pen=false;return}const a=left+i*(right-left)/Math.max(1,rows.length-1),b=bottom-v*(bottom-top);pen?x.lineTo(a,b):x.moveTo(a,b);pen=true});x.stroke()}if(rows.length){x.fillStyle='#718579';x.fillText(String(rows[0].date).slice(0,10),left,h-7);x.fillText(String(rows.at(-1).date).slice(0,10),Math.max(left,right-70),h-7)}}
function renderKeyStatus(){const status=state?.gemini??{configured:state?.gemini_configured,source:'environment'};setText('key-current',status.configured?(status.source==='session'?'Session key configured · stored in server memory only':'Using a key from the local environment'):'No Gemini key configured');$('clear-key').hidden=status.source!=='session'}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',async()=>{const repeats=$('fake-news-repeats');const attack=button.dataset.action==='fake-news-replay';if(attack&&!repeats.reportValidity())return;const replayRequest=attack?{repeats:repeats.valueAsNumber}:undefined;document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);try{await post(button.dataset.action,replayRequest);if(button.dataset.action==='forecast')$('activity').scrollIntoView({behavior:'smooth',block:'start'});await refresh()}catch(error){toast(error.message);if(state)render(state)}}));
function openGeminiDialog(){setText('key-feedback','');$('gemini-key').value='';renderKeyStatus();$('key-dialog').showModal()}
document.querySelectorAll('[data-open-key]').forEach(b=>b.addEventListener('click',openGeminiDialog));
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));$('key-dialog').addEventListener('close',()=>{$('gemini-key').value='';setText('key-feedback','')});
$('key-form').addEventListener('submit',async e=>{e.preventDefault();const input=$('gemini-key');if(!input.value.trim()){input.focus();return}$('save-key').disabled=true;$('clear-key').disabled=true;try{await post('gemini',{api_key:input.value});setText('key-feedback','Configured for this server session. Run a forecast to use Gemini.');$('key-feedback').classList.remove('error');await refresh()}catch(error){setText('key-feedback',error.message);$('key-feedback').classList.add('error')}finally{input.value='';$('save-key').disabled=false;$('clear-key').disabled=false}});
$('clear-key').addEventListener('click',async()=>{try{await post('gemini/clear');$('gemini-key').value='';setText('key-feedback','Session key cleared.');await refresh()}catch(error){setText('key-feedback',error.message)}});
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));renderAgents()}));$('agent-search').addEventListener('input',e=>{search=e.target.value.trim().toLowerCase();renderAgents()});
document.querySelectorAll('[data-range]').forEach(b=>b.addEventListener('click',()=>{range=Number(b.dataset.range);hover=null;document.querySelectorAll('[data-range]').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-pressed',String(x===b))});drawPrice()}));
for(const [id,style]of [['candles-button','candles'],['line-button','line']])$(id).addEventListener('click',()=>{chartStyle=style;for(const [key,value]of [['candles-button','candles'],['line-button','line']]){$(key).classList.toggle('selected',chartStyle===value);$(key).setAttribute('aria-pressed',String(chartStyle===value))}drawPrice()});
$('ema-toggle').addEventListener('click',()=>{showEma=!showEma;$('ema-toggle').classList.toggle('selected',showEma);$('ema-toggle').setAttribute('aria-pressed',String(showEma));drawPrice()});$('expand-chart').addEventListener('click',()=>{$('price-panel').classList.toggle('expanded');$('expand-chart').setAttribute('aria-label',$('price-panel').classList.contains('expanded')?'Collapse chart':'Expand chart');requestAnimationFrame(drawPrice)});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('price-panel').classList.contains('expanded')){$('price-panel').classList.remove('expanded');$('expand-chart').setAttribute('aria-label','Expand chart');drawPrice()}});
$('price-chart').addEventListener('pointermove',e=>{if(!plot)return;const rect=e.currentTarget.getBoundingClientRect(),i=Math.floor((e.clientX-rect.left-plot.left)/plot.step);hover=Math.max(0,Math.min(plot.bars.length-1,i));const b=plot.bars[hover];$('chart-tooltip').hidden=false;setText('chart-tooltip',b.date+' UTC\nClose '+num(b.close)+' USDT'+(b.volume!==undefined?'\nVolume '+num(b.volume,0)+' BTC':''));drawPrice()});$('price-chart').addEventListener('pointerleave',()=>{hover=null;$('chart-tooltip').hidden=true;readout(market.candles?.at(-1));drawPrice()});
document.querySelectorAll('.rail nav a').forEach(a=>a.addEventListener('click',()=>{document.querySelectorAll('.rail nav a').forEach(x=>x.classList.toggle('selected',x===a))}));const resize=new ResizeObserver(()=>{drawPrice();drawReplay()});resize.observe($('price-chart').parentElement);resize.observe($('replay-chart').parentElement);renderAgents();renderActivity(null);drawPrice();drawReplay();refresh();
$('fake-news-repeats').addEventListener('input', event => {
  const input = event.target;
  setText('fake-news-calls', input.validity.valid
    ? (8 * input.valueAsNumber) + ' Gemini calls across four scenarios.'
    : 'Enter 1 to 100 repeats per scenario.');
});

function renderFakeNewsReplay(state) {
  const replay = state.attack_replay;
  const ready = state.gemini_configured && state.forecast?.news_capture;
  setText('fake-news-replay-status', ready
    ? 'Compare the saved forecast with four fabricated inputs.'
    : 'Generate a forecast with News articles and connect Gemini.');
  $('fake-news-results').hidden = !replay;
  const body = $('fake-news-rows');
  body.replaceChildren();
  if (!replay) return;
  const names = {fake_etf_approval: 'Fake ETF approval', hidden_text: 'Hidden text',
    fake_whale_alert: 'Fake whale alert', poisoned_memory: 'Simulated poisoned memory'};
  const methods = {exchange: 'Agent Exchange', average: 'Simple average', vote: 'Majority vote'};
  for (const [attack, title] of Object.entries(names)) {
    const rows = replay.rows.filter(row => row.attack === attack);
    for (const [method, label] of Object.entries({news: 'News', ...methods})) {
      const comparable = rows.filter(row => row.method === (method === 'news' ? 'exchange' : method) && row.comparable);
      const shifts = comparable.map(row => method === 'news' ? Math.abs(row.news_attacked - row.news_clean) : row.shift);
      const mean = shifts.length ? shifts.reduce((sum, shift) => sum + shift, 0) / shifts.length : null;
      const row = node('tr');
      row.append(node('td', title), node('td', label),
        node('td', shifts.length + ' / ' + replay.repeats),
        node('td', mean == null ? '—' : (mean * 100).toFixed(1) + ' pp'));
      body.append(row);
    }
  }
  setText('fake-news-output', 'Saved results: ' + replay.output);
}

function requiresGemini(reason) {
  return reason?.startsWith('No model client was supplied') ||
    reason?.startsWith('No explicit Betting Odds model callable');
}

function forecastReason(role, reason) {
  if (requiresGemini(reason)) return state?.gemini_configured ?
    'Gemini is connected. Generate a new forecast to use it.' :
    'Connect Gemini, then generate a new forecast.';
  const book = state?.sources?.betting_odds;
  if (role === 'Betting odds' && book?.status === 'unavailable' &&
      reason?.startsWith('Missing or invalid timezone-aware student2_markets_observed_at')) {
    return book.reason;
  }
  return reason;
}

function renderLiveCoins({ learning, forecast }) {
  const rows = $('live-coin-rows');
  rows.replaceChildren();
  if (!learning) return;
  const models = { News: 'News Specialist', Mood: 'Mood Specialist',
    'Betting odds': 'Betting Odds Specialist' };
  const bets = new Map((learning.last_settled?.bets ?? []).map(bet => [bet.agent, bet]));
  setText('live-coin-status', learning.settled + ' settled · ' + learning.pending + ' pending. ' +
    'Balances update after settlement. Forecast weights show each agent’s influence in the latest run.');
  for (const [role] of roles) {
    const result = forecast?.agents?.find(agent => agent.role === role);
    const id = models[role] ?? role.toLowerCase().replaceAll(' ', '_').replaceAll('-', '_');
    const balance = learning.coins[id] ?? learning.start_coins;
    const stakeResult = bets.get(id);
    const weight = forecast?.weights?.[id];
    const participation = weight != null ? 'Active' : result ?
      'Abstained · ' + forecastReason(role, result.evidence?.[0] ?? 'No validated forecast') : 'Awaiting forecast';
    const row = node('tr');
    const name = node('td'), identity = node('div', undefined, 'agent-name');
    identity.append(agentIcon(role), node('span', role));name.append(identity);
    row.append(name, node('td', num(balance)),
      node('td', stakeResult ? formatCoinChange(stakeResult.coins_after - stakeResult.coins_before) : '—'),
      node('td', percent(result && weight == null ? 0 : weight)),
      node('td', participation));
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
  dates.replaceChildren(...history.map(day => node('option', day.date)));
  dates.selectedIndex = history.length - 1;
  $('replay-coin-history').hidden = history.length === 0;
  dates.onchange = showDay;
  showDay();

  function showDay() {
    const day = history[dates.selectedIndex];
    const previous = history[dates.selectedIndex - 1];
    const rows = $('replay-coin-rows');
    rows.replaceChildren();
    setText('replay-coin-status', !day ? (state.replay.problem?.reason ?? 'No settled coin history is available for this replay.') :
      (state.replay.mode.startsWith('ARCHIVED') ? 'Coin changes are replayed in order. Weight is the original live forecast weight.' : 'Balances update after settlement. Weight is the influence used before settlement.') +
      (day.cap_feasible ? '' : ' The 20% cap was infeasible on this day.'));
    if (!day) return;
    const result = state.replay.results?.[dates.selectedIndex];
    const abstentions = JSON.parse(result?.abstentions ?? '{}');
    const active = new Set(JSON.parse(result?.active_agents ?? '[]'));
    for (const key of Object.keys(day).filter(key => key.startsWith('coins_'))) {
      const agent = key.slice('coins_'.length);
      const opening = previous?.[key] ?? 100;
      const closing = day[key];
      const change = Number((closing - opening).toFixed(2));
      const row = node('tr');
      row.append(node('td', agent), node('td', opening.toFixed(2)),
        node('td', formatCoinChange(change),
          change > 0 ? 'positive' : change < 0 ? 'negative' : ''),
        node('td', closing.toFixed(2)), node('td', percent(day['weight_' + agent])),
        node('td', active.has(agent) ? 'Active' : abstentions[agent] ?? 'Unavailable'));
      rows.append(row);
    }
  }
}
