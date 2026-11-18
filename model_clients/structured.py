"""Decode one model JSON object without guessing at incomplete model output."""
import json
import re
from collections.abc import Mapping


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Model JSON contains duplicate keys')
        result[key] = value
    return result


def parse_model_object(response):
    """Accept SDK parsed models, messages, text blocks, or a single JSON fence.

    Prose, multiple objects, truncated JSON and nonfinite constants fail closed.
    Reasoning/thought blocks are never interpreted as the final answer.
    """
    if hasattr(response, 'model_dump') and not hasattr(response, 'content'):
        # A native SDK response is also a Pydantic model: prefer its parsed/text
        # payload before treating a standalone result model as the answer.
        if not hasattr(response, 'parsed') and not hasattr(response, 'text'):
            response = response.model_dump(mode='json')
    if isinstance(response, Mapping):
        return dict(response)
    if getattr(response, 'parsed', None) is not None:
        return parse_model_object(response.parsed)
    if hasattr(response, 'content'):
        return parse_model_object(response.content)
    if hasattr(response, 'text'):
        return parse_model_object(response.text)
    if isinstance(response, list):
        response = ''.join(
            block if isinstance(block, str) else block.get('text', '')
            for block in response
            if isinstance(block, str) or (isinstance(block, dict)
                and block.get('type') in ('text', 'output_text') and not block.get('thought'))
        )
    if not isinstance(response, str) or not response.strip():
        raise ValueError('Model returned no supported JSON content')
    text = response.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    def reject_constant(value):
        raise ValueError('Model JSON contains a nonfinite number')
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        # Do not include raw output, prompts, or provider error bodies in logs.
        raise ValueError('Model returned invalid or incomplete JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('Model output must be one JSON object')
    return value
