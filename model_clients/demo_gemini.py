"""Small native Gemini bridge used only after an explicit dashboard action."""
import os
import time
from types import SimpleNamespace

class DemoGemini:
    def __init__(self, *, api_key=None):
        from google import genai
        key = api_key or os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
        if not key:
            raise ValueError('Configure GOOGLE_API_KEY or GEMINI_API_KEY locally')
        self.model = os.environ.get('GEMINI_MODEL','gemini-3.8-flash')
        self.client = genai.Client(api_key=key, http_options={'timeout':60000})
        self.usage = []

    def __call__(self, prompt):
        start = time.monotonic()
        result = self.client.models.generate_content(model=self.model,contents=prompt,
            config={'temperature':0,'response_mime_type':'application/json'})
        meta = result.usage_metadata
        self.usage.append({'model':self.model,'seconds':round(time.monotonic()-start,3),
            'input_tokens':getattr(meta,'prompt_token_count',None),
            'output_tokens':getattr(meta,'candidates_token_count',None)})
        return result.text

    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content=self('\n'.join(str(m.content) for m in messages)))
