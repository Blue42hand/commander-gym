"""Pinned SDK exercised through an in-process fake HTTP transport only."""
import importlib.util
import json
import os
import unittest


class ManualRuntimeSdkTests(unittest.TestCase):
    def test_pinned_sdk_serializes_luna_request_without_external_io(self):
        if importlib.util.find_spec('openai') is None:
            if os.environ.get('REQUIRE_MANUAL_SDK_TESTS') == '1':
                self.fail('pinned SDK qualification job must install its hashed lock')
            self.skipTest('SDK qualification runs in its separate pinned dependency job')
        import openai
        import httpx2
        self.assertEqual(openai.__version__, '3.27.0')
        requests=[]
        def respond(request):
            self.assertEqual(request.url.host,'qa-provider.example.test')
            body=json.loads(request.content)
            requests.append(body)
            return httpx2.Response(200,json={'id':'qa-response','object':'response','created_at':0,'status':'completed',
                'model':'gpt-6-luna','output':[], 'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}})
        client=openai.OpenAI(api_key='qa-placeholder-never-real',base_url='https://qa-provider.example.test/v1',
            max_retries=0,timeout=90,http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
        try:
            result=client.responses.create(model='gpt-6-luna',input='qa synthetic fixture',max_output_tokens=32,store=False)
            self.assertEqual(result.id,'qa-response')
            self.assertEqual(len(requests),1)
            self.assertEqual(requests[0]['model'],'gpt-6-luna')
            self.assertFalse(requests[0]['store'])
        finally:client.close()
