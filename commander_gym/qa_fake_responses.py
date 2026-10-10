"""Deterministic schema fixtures, never an SDK or network client.

These are synthetic choices, not model judgment or inferred native legality. The
real policy validators and native server still reject unsupported/illegal values.
Unsupported schemas fail rather than contacting a provider.
"""
import json
from types import SimpleNamespace


def fixture_value(schema, depth=0):
    if depth > 20 or not isinstance(schema, dict):
        raise ValueError('qa_fixture_schema')
    if 'const' in schema: return schema['const']
    if schema.get('enum'): return schema['enum'][0]
    if schema.get('anyOf'): return fixture_value(schema['anyOf'][0], depth+1)
    kind = schema.get('type')
    if kind == 'object':
        properties = schema.get('properties', {})
        return {key: fixture_value(properties[key], depth+1) for key in schema.get('required', [])}
    if kind == 'array':
        count = schema.get('minItems', 0)
        if type(count) is not int or not 0 <= count <= 100: raise ValueError('qa_fixture_array')
        return [fixture_value(schema['items'], depth+1) for _ in range(count)]
    if kind == 'string': return 'qa-fixture'
    if kind in ('integer','number'): return schema.get('minimum', 0)
    if kind == 'boolean': return False
    if kind == 'null': return None
    raise ValueError('qa_fixture_schema_unsupported')


class SchemaFixtureClient:
    def __init__(self):
        self.responses = self
        self.calls = 0

    def create(self, **request):
        value = fixture_value(request['text']['format']['schema'])
        self.calls += 1
        return SimpleNamespace(id=f'qa-fixture-{self.calls}', output_text=json.dumps(value),
            usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0))
