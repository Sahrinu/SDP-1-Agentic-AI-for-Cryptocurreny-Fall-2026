"""Bounded Gemini bridge shared by explicit dashboard and experiment actions."""
import os
import time
from types import SimpleNamespace
from model_clients.structured import parse_model_object


class DemoGemini:
    def __init__(self, *, api_key=None, model=None):
        from google import genai
        key = api_key or os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
        if not key:
            raise ValueError('Configure GOOGLE_API_KEY or GEMINI_API_KEY locally')
        self.model = model or os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash')
        self.client = genai.Client(api_key=key, http_options={
            'timeout': 60000, 'retry_options': {'attempts': 1}})
        self.usage = []
        self.responses = []  # Local experiment recorder; never included in dashboard usage.

    def _generate(self, prompt, schema=None):
        from google.genai.errors import APIError, ServerError

        config = {'temperature': 0, 'response_mime_type': 'application/json'}
        if schema is not None:
            config['response_schema'] = schema
        models = [self.model]
        if self.model == 'gemini-3.8-flash':
            models.append('gemini-3.5-flash-lite')
        for index, model in enumerate(models):
            start = time.monotonic()
            record = {'model': model}
            try:
                result = self.client.models.generate_content(model=model, contents=prompt, config=config)
                meta = getattr(result, 'usage_metadata', None)
                record.update(input_tokens=getattr(meta, 'prompt_token_count', None),
                              output_tokens=getattr(meta, 'candidates_token_count', None))
                saved = {'model': model, 'prompt': prompt, 'response_text': result.text,
                         'model_version': getattr(result, 'model_version', model),
                         'response_id': getattr(result, 'response_id', None)}
                self.responses.append(saved)
                # Keep invalid responses in the local recorder too, then fail closed.
                value = parse_model_object(result)
                saved['parsed'] = value
                return value, result.text
            except ServerError as exc:
                record['error_type'] = type(exc).__name__
                record['error_code'] = exc.code
                if index == len(models) - 1:
                    raise
            except APIError as exc:
                record['error_type'] = type(exc).__name__
                record['error_code'] = exc.code
                raise
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
        self.client.close()
