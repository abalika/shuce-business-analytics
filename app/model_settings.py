"""Local-only model settings. Secrets are never part of a settings response."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values, set_key
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

ROOT=Path(__file__).resolve().parents[1]
ENV_FILE=ROOT/'.env'
_lock=threading.RLock()
_test_lock=threading.Lock()
_override=None
_source='none'
_revision=0
router=APIRouter(prefix='/api/model-settings')


def offline():
    return os.getenv('SHUCE_OFFLINE')=='1'


def runtime_override():
    """None means use legacy environment/pointer configuration; blank means disabled."""
    global _override,_source
    with _lock:
        if _override is None and not offline() and ENV_FILE.is_file():
            values=dotenv_values(ENV_FILE)
            if values.get('SHUCE_MODEL_MANAGED')=='1':
                _override={'key':values.get('OPENAI_API_KEY') or '',
                           'base':values.get('OPENAI_BASE_URL') or '',
                           'model':values.get('OPENAI_MODEL') or ''}
                _source='local_file'
        return dict(_override) if _override is not None else None


def effective():
    from .ai import config
    return config()


def public_settings():
    with _lock:
        value=effective()
        base=value['base'];model=value['model'];key=value['key']
        # Legacy environment settings may contain a mistakenly pasted secret.
        # Never turn an unsafe URL or model field into a settings disclosure.
        try:
            url=urlsplit(base)
            if url.username is not None or url.password is not None or url.query or url.fragment:base=''
        except ValueError:base=''
        if key and (model==key or len(key)>=8 and key in model):model=''
        if key and (base==key or len(key)>=8 and key in base):base=''
        return {'base_url':base,'model':model,'has_key':bool(key),
                'configured':bool(base and model and key), 'offline':offline(),'revision':_revision,
                'source':'offline' if offline() else _source if _override is not None else 'environment' if value['key'] else 'none'}


def response(value):
    return JSONResponse(value,headers={'Cache-Control':'no-store','Pragma':'no-cache'})


class SettingsBody(BaseModel):
    base_url:str=Field(min_length=1,max_length=2048)
    model:str=Field(min_length=1,max_length=200)
    api_key:SecretStr|None=Field(default=None,max_length=4096)
    remember:bool=False
    revision:int=Field(ge=0)


class ClearBody(BaseModel):
    revision:int=Field(ge=0)


def available():
    if offline():
        raise HTTPException(409,'当前是离线演示，不读取或测试模型配置。请停止离线服务，再用“启动数策.cmd”进入普通模式。')


def candidate(body):
    base=body.base_url.strip().rstrip('/')
    model=body.model.strip()
    try:
        url=urlsplit(base)
        port=url.port
        if url.scheme not in {'http','https'} or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError()
        if url.scheme=='http' and url.hostname.lower() not in {'localhost','127.0.0.1','::1'}:
            raise ValueError()
        if any(ord(c)<33 for c in base) or '\\' in base:
            raise ValueError()
    except ValueError:
        raise HTTPException(422,'API 地址须为 HTTPS，或本机回环 HTTP 地址；不能包含账号、密码、查询参数或片段。') from None
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}',model):
        raise HTTPException(422,'模型 ID 只支持字母、数字、点、下划线、冒号、斜线及连字符，请按服务商提供的 ID 填写。')
    supplied=body.api_key.get_secret_value() if body.api_key is not None else ''
    old=effective()
    if not supplied.strip() and base!=old['base']:
        raise HTTPException(422,'更改 API 地址时，请重新填写该服务对应的 API Key，避免把旧密钥用于其他服务。')
    key=supplied.strip() or old['key']
    if not key or any(ord(c)<33 or ord(c)>126 for c in key):
        raise HTTPException(422,'请填写有效的 API Key，不能包含空格、换行或控制字符。')
    if len(key)>=8 and (key in base or key in model):
        raise HTTPException(422,'请检查字段位置：密钥只应填写在 API Key 输入框中。')
    return {'key':key,'base':base,'model':model}


def persist(value):
    """Atomic replace; preserve unrelated .env settings, never touch another app."""
    ENV_FILE.parent.mkdir(parents=True,exist_ok=True)
    if ENV_FILE.is_symlink():raise HTTPException(409,'配置文件是符号链接，拒绝覆盖。请检查本机配置路径。')
    handle,name=tempfile.mkstemp(prefix='.env-model-',dir=ENV_FILE.parent)
    temporary=Path(name)
    try:
        os.close(handle)
        os.chmod(temporary,0o600)
        temporary.write_bytes(ENV_FILE.read_bytes() if ENV_FILE.exists() else b'')
        for key,item in {'SHUCE_MODEL_MANAGED':'1','OPENAI_API_KEY':value['key'],
                         'OPENAI_BASE_URL':value['base'],'OPENAI_MODEL':value['model']}.items():
            set_key(temporary,key,item,quote_mode='always')
        os.replace(temporary,ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)


@router.get('')
def get_settings():
    return response(public_settings())


@router.post('')
def save_settings(body:SettingsBody):
    global _override,_source,_revision
    available()
    with _lock:
        if body.revision!=_revision:raise HTTPException(409,'配置已在另一个窗口修改。请重新打开模型设置后再保存。')
        value=candidate(body)
        if body.remember:persist(value)
        _override=value
        _source='local_file' if body.remember else 'session'
        _revision+=1
        return response({**public_settings(),'detail':'配置已保存并立即生效；未自动发送模型请求。'})


@router.delete('')
def clear_settings(body:ClearBody):
    global _override,_source,_revision
    available()
    with _lock:
        if body.revision!=_revision:raise HTTPException(409,'配置已更改，请重新打开设置再清除。')
        empty={'key':'','base':'','model':''}
        # An explicit blank managed config prevents old inherited credentials
        # silently reappearing after restart; unrelated settings are retained.
        persist(empty)
        _override=empty
        _source='local_file'
        _revision+=1
        return response({**public_settings(),'detail':'本工作台模型配置已清除，之后使用规则计算。外部网关及其他软件未修改。'})


@router.post('/test')
async def test_settings(body:SettingsBody):
    available()
    with _lock:
        if body.revision!=_revision:raise HTTPException(409,'配置已修改，请重新打开设置再测试。')
        value=candidate(body)
    if not _test_lock.acquire(blocking=False):raise HTTPException(429,'已有连接测试正在进行，请等待结果后再试。')
    try:
        endpoint=value['base'] if value['base'].endswith('/chat/completions') else value['base']+'/chat/completions'
        payload={'model':value['model'],'messages':[{'role':'user','content':'Only return the JSON object {"ok":true}.'}],
                 'temperature':0,'max_tokens':32,'response_format':{'type':'json_object'}}
        if 'deepseek' in value['model'].lower() or 'api.deepseek.com' in value['base']:
            payload['thinking']={'type':'disabled'}
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=3),follow_redirects=False,trust_env=False) as client:
            result=await client.post(endpoint,headers={'Authorization':'Bearer '+value['key']},json=payload)
        if result.status_code!=200:
            messages={401:'认证失败，请核对 API Key。',402:'账户额度不足，请到服务商后台核对。',403:'服务商拒绝访问，请核对权限或地址。',404:'未找到接口或模型，请核对 API 地址与模型 ID。',429:'服务商限流，请稍后重试。'}
            raise HTTPException(422,messages.get(result.status_code,'服务暂不可用或返回重定向。请核对兼容接口地址后重试；密钥没有被转发到重定向地址。'))
        try:
            content=result.json()['choices'][0]['message']['content'].strip().lstrip('\ufeff')
            fenced=re.fullmatch(r'```(?:json)?\s*(.*?)\s*```',content,re.I|re.S)
            data=json.loads(fenced.group(1) if fenced else content)
            if not isinstance(data,dict) or data.get('ok') is not True:raise ValueError()
        except (ValueError,KeyError,IndexError,TypeError,AttributeError):
            raise HTTPException(422,'服务有响应，但未返回兼容的 JSON 对话内容。请核对模型是否支持 JSON 输出。') from None
        return response({'ok':True,'detail':'连接成功，模型返回了兼容的 JSON 对话结果。测试没有保存配置，也没有发送业务数据。'})
    except httpx.TimeoutException:
        raise HTTPException(422,'连接测试超时，请检查服务状态、网络及地址，稍后重试。原配置未修改。') from None
    except httpx.HTTPError:
        raise HTTPException(422,'无法连接模型服务，请检查网络与 API 地址。原配置未修改。') from None
    finally:
        _test_lock.release()
