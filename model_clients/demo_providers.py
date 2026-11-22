"""Claude and OpenAI clients with the same small interface as DemoGemini.

They exist so the language-model agents can be re-run with other providers on
the same saved inputs (supervisor request: compare results across model APIs).
Plain HTTPS through the standard library, so no extra packages are needed.
Keys come only from the environment or the local .env file, never from code.
"""
import json
import os
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

from model_clients.structured import parse_model_object

JSON_ONLY = 'Reply with exactly one JSON object and nothing else.'


def _schema_note(schema):
    if schema is None:
        return ''
    try:
        shape = json.dumps(schema.model_json_schema())
    except Exception:
        return ''
    return '\n\nReturn one JSON object that matches this JSON schema:\n' + shape


def _post(url, headers, body, timeout=60):
    request = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                     headers={'content-type': 'application/json', **headers}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


class ProviderError(RuntimeError):
    """A provider refused the request; the message never contains the key or the prompt."""


class _DemoProvider:
    provider = None
    key_names = ()
    model_env = None
    default_model = None

    def __init__(self, *, api_key=None, model=None, post=_post):
        key = api_key or next((os.environ.get(n) for n in self.key_names if os.environ.get(n)), None)
        if not key:
            raise ValueError('Configure ' + ' or '.join(self.key_names) + ' locally')
        self._key = key
        self.model = model or os.environ.get(self.model_env) or self.default_model
        self._post = post
        self.usage = []
        self.responses = []

    def _call(self, prompt):  # returns (text, input_tokens, output_tokens)
        raise NotImplementedError

    def _generate(self, prompt, schema=None):
        prompt = prompt + _schema_note(schema)
        start = time.monotonic()
        record = {'provider': self.provider, 'model': self.model}
        try:
            text, tokens_in, tokens_out = self._call(prompt)
            record.update(input_tokens=tokens_in, output_tokens=tokens_out)
            saved = {'provider': self.provider, 'model': self.model, 'prompt': prompt, 'response_text': text}
            self.responses.append(saved)
            value = parse_model_object(text)
            saved['parsed'] = value
            return value, text
        except urllib.error.HTTPError as exc:
            record.update(error_type='HTTPError', error_code=exc.code)
            raise ProviderError(f'{self.provider} returned HTTP {exc.code}; check the key, credit and model name') from None
        except Exception as exc:
            record['error_type'] = type(exc).__name__
            raise
        finally:
            record['seconds'] = round(time.monotonic() - start, 3)
            self.usage.append(record)

    def __call__(self, prompt):
        return self._generate(prompt)[1]

    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content=self('\n'.join(str(m.content) for m in messages)))

    def with_structured_output(self, schema, **kwargs):
        owner = self

        class Structured:
            def invoke(self, messages, **kwargs):
                value, _ = owner._generate('\n'.join(str(m.content) for m in messages), schema)
                return schema.model_validate(value)
        return Structured()

    def close(self):
        pass


class DemoClaude(_DemoProvider):
    provider = 'claude'
    key_names = ('ANTHROPIC_API_KEY',)
    model_env = 'CLAUDE_MODEL'
    default_model = 'claude-haiku-5-5'

    def _call(self, prompt):
        data = self._post('https://api.anthropic.com/v1/messages',
                          {'x-api-key': self._key, 'anthropic-version': '2023-06-01'},
                          {'model': self.model, 'max_tokens': 2048, 'temperature': 0, 'system': JSON_ONLY,
                           'messages': [{'role': 'user', 'content': prompt}]})
        text = ''.join(block.get('text', '') for block in data.get('content', []) if block.get('type') == 'text')
        usage = data.get('usage') or {}
        return text, usage.get('input_tokens'), usage.get('output_tokens')


class DemoOpenAI(_DemoProvider):
    provider = 'openai'
    key_names = ('OPENAI_API_KEY',)
    model_env = 'OPENAI_MODEL'
    default_model = 'gpt-5-mini'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._send_temperature = True

    def _call(self, prompt):
        body = {'model': self.model, 'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': JSON_ONLY}, {'role': 'user', 'content': prompt}]}
        if self._send_temperature:
            body['temperature'] = 0
        try:
            data = self._post('https://api.openai.com/v1/chat/completions',
                              {'authorization': 'Bearer ' + self._key}, body)
        except urllib.error.HTTPError as exc:
            # Some newer OpenAI models accept only their default temperature.
            if exc.code == 400 and self._send_temperature:
                self._send_temperature = False
                body.pop('temperature', None)
                data = self._post('https://api.openai.com/v1/chat/completions',
                                  {'authorization': 'Bearer ' + self._key}, body)
            else:
                raise
        text = data['choices'][0]['message'].get('content') or ''
        usage = data.get('usage') or {}
        return text, usage.get('prompt_tokens'), usage.get('completion_tokens')


PROVIDERS = ('gemini', 'claude', 'openai')


def make_client(provider):
    if provider == 'gemini':
        from model_clients.demo_gemini import DemoGemini
        return DemoGemini()
    if provider == 'claude':
        return DemoClaude()
    if provider == 'openai':
        return DemoOpenAI()
    raise ValueError('Provider must be one of: ' + ', '.join(PROVIDERS))
