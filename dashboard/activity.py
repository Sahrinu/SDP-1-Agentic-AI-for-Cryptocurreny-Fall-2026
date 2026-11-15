"""Thread-safe, observable forecast events; no fabricated thinking or delays."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
import uuid

from agents.registry import CATALOG


class ForecastActivity:
    def __init__(self, *, clock=time.monotonic, wall_clock=None):
        self._lock = threading.Lock()
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.reset()

    def reset(self):
        with self._lock:
            self._started = None
            self._agent_starts = {}
            self._state = {'run_id':None,'status':'idle','phase':'Ready for a forecast',
                'started_at':None,'finished_at':None,'elapsed_ms':0,'current_agent':None,
                'agents':[{'role':s.name,'status':'queued','p_up':None,'duration_ms':None,
                    'summary':'Waiting for this forecast run','result':None} for s in CATALOG],
                'events':[],'methods':None}

    def _event(self, kind, message, role=None, **fields):
        elapsed = round((self._clock()-self._started)*1000,3) if self._started is not None else 0
        self._state['events'].append({'sequence':len(self._state['events'])+1,
            'kind':kind,'role':role,'at':self._wall_clock(),'elapsed_ms':elapsed,
            'message':message,**fields})

    def begin(self):
        self.reset()
        with self._lock:
            self._started = self._clock()
            self._state.update(run_id=uuid.uuid4().hex,status='running',
                phase='Preparing the forecast and BTC reference',started_at=self._wall_clock())
            self._event('run_started','Forecast requested; preparing the 24-hour event and fresh BTC reference.')

    def record(self, event):
        with self._lock:
            kind = event['event']
            role = event.get('role')
            agent = next((a for a in self._state['agents'] if a['role']==role),None)
            if kind == 'agent_started' and agent is not None:
                self._agent_starts[role] = self._clock()
                summary = ('Reading completed peer forecasts' if role=='Contrarian' else
                    'Checking timestamped evidence and model readiness' if role in {'News','Mood','Betting odds'} else
                    'Checking source availability and calculating its signal')
                agent.update(status='running',started_at=self._wall_clock(),summary=summary)
                self._state.update(phase=role+' is evaluating',current_agent=role)
                self._event(kind,summary,role)
            elif kind == 'agent_completed' and agent is not None:
                status = event['status']
                if status not in {'forecasted','abstained','failed'}:
                    raise ValueError('Invalid agent activity status')
                result = event['result']
                duration = round((self._clock()-self._agent_starts[role])*1000,3)
                # Persist the same public result fields the dashboard already exposes.
                public = {k:deepcopy(result[k]) for k in (
                    'agent','day','role','p_up','data_missing','evidence','audit','limitations') if k in result}
                evidence = public.get('evidence')
                summary = next((x for x in evidence if isinstance(x,str) and x.strip()),
                    'No usable evidence was supplied') if isinstance(evidence,list) else 'No usable evidence was supplied'
                summary = summary[:1200]
                agent.update(status=status,p_up=public.get('p_up'),duration_ms=duration,
                    finished_at=self._wall_clock(),summary=summary,result=public)
                self._state['current_agent'] = None
                self._event(kind,summary[:280],role,status=status,p_up=public.get('p_up'),duration_ms=duration)
            elif kind == 'aggregation_started':
                self._state.update(phase='Combining audited forecasts',current_agent=None)
                self._event(kind,'Calculating Agent Exchange, majority vote and simple average from usable forecasts.')
            elif kind == 'aggregation_completed':
                self._state.update(phase='Saving the forecast and evidence',methods=deepcopy(event['methods']))
                self._event(kind,'Method comparisons calculated. Preparing the saved forecast.')

    def finish(self):
        with self._lock:
            if any(a['status'] not in {'forecasted','abstained','failed'} for a in self._state['agents']):
                raise ValueError('Cannot complete before all specialists return')
            self._state.update(status='completed',phase='Forecast complete',finished_at=self._wall_clock(),
                elapsed_ms=round((self._clock()-self._started)*1000,3),current_agent=None)
            self._event('run_completed','Forecast complete. The real 24-hour outcome is still pending.')

    def fail(self, error_type):
        with self._lock:
            for agent in self._state['agents']:
                if agent['status']=='running':
                    agent.update(status='failed',summary='Run interrupted before this agent returned a result')
                elif agent['status']=='queued':
                    agent.update(status='not_run',summary='Run stopped before this agent was reached')
            self._state.update(status='failed',phase='Forecast interrupted',current_agent=None,
                finished_at=self._wall_clock(),elapsed_ms=round((self._clock()-self._started)*1000,3))
            # Caller supplies an exception class name, never exception text or keys.
            self._event('run_failed','The run could not finish ('+error_type+'). Check sources and configuration.')

    def snapshot(self):
        with self._lock:
            value = deepcopy(self._state)
            if value['status']=='running':
                value['elapsed_ms'] = round((self._clock()-self._started)*1000,3)
            for agent in value['agents']:
                if agent['status']=='running':
                    agent['running_ms'] = round((self._clock()-self._agent_starts[agent['role']])*1000,3)
            value['evaluated_count'] = sum(a['status'] in {'forecasted','abstained','failed'} for a in value['agents'])
            value['forecasted_count'] = sum(a['status']=='forecasted' for a in value['agents'])
            value['abstained_count'] = sum(a['status']=='abstained' for a in value['agents'])
            value['failed_count'] = sum(a['status']=='failed' for a in value['agents'])
            return value
