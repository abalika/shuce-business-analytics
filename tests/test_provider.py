import asyncio
import httpx
from app import ai


def test_invalid_json_retried_and_recorded(monkeypatch):
    monkeypatch.setenv('SHUCE_OFFLINE', '0')
    monkeypatch.setattr(ai,"config",lambda:{"key":"test-only","base":"http://provider.test/v1","model":"test-model"})
    responses=["not JSON",'```JSON\n{"ok":true}\n```']
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            return httpx.Response(200,json={"choices":[{"message":{"content":responses.pop(0)}}], "usage": None},request=httpx.Request("POST",url))
    monkeypatch.setattr(ai.httpx,"AsyncClient",Client)
    payload,trace=asyncio.run(ai.complete_json("system","user"))
    assert payload=={"ok":True} and len(trace["attempts"])==2
    assert trace["attempts"][0]["code"]=="invalid_json_response"
    assert trace['tokens'] is None


def test_gateway_deepseek_thinking_disabled(monkeypatch):
    monkeypatch.setenv('SHUCE_OFFLINE', '0')
    monkeypatch.setattr(ai,"config",lambda:{"key":"test-only","base":"http://local-gateway.test/v1","model":"cn:deepseek-v4.1-flash"})
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append(kwargs["json"])
            return httpx.Response(200,json={"choices":[{"message":{"content":'{"ok":true}'}}]},request=httpx.Request("POST",url))
    monkeypatch.setattr(ai.httpx,"AsyncClient",Client)
    asyncio.run(ai.complete_json("system","user"))
    assert calls[0]["thinking"]=={"type":"disabled"}
