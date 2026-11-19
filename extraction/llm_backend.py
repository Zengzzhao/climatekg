"""Provider configuration and Chat Completions transport for extraction workflows."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI as SDKOpenAI

DEFAULT_VLLM_BASE_URL = "https://lryd5kgi-bwbkdaqq-8000.ahrestapi.gpufree.cn:8443/v1"
DEFAULT_VLLM_MODEL = "Qwen/Qwen3-32B-AWQ"
DEFAULT_ZRSHUIWU_BASE_URL = "https://llm.zrshuiwu.com/v1"
DEFAULT_ZRSHUIWU_MODEL = "deepseek-v4.1-flash-1017"

PROFILES = {
    'deepseek': ('DEEPSEEK', 'deepseek-chat', 'https://api.deepseek.com'),
    'vllm': ('VLLM', DEFAULT_VLLM_MODEL, DEFAULT_VLLM_BASE_URL),
    'zrshuiwu': ('ZRSHUIWU', DEFAULT_ZRSHUIWU_MODEL, DEFAULT_ZRSHUIWU_BASE_URL),
}


@dataclass(frozen=True)
class Backend:
    name: str
    model: str
    base_url: str
    api_key: str = field(repr=False)

    def public_config(self):
        return dict(backend=self.name, model=self.model, base_url=self.base_url)


def add_backend_argument(parser):
    load_dotenv(Path(__file__).resolve().parent/'.env')
    parser.add_argument('--backend', choices=tuple(PROFILES),
                        default=os.getenv('EXTRACTION_BACKEND','deepseek'),
                        help='Service profile: DEEPSEEK_*, VLLM_* or ZRSHUIWU_* settings')


def configure_backend(args):
    load_dotenv(Path(__file__).resolve().parent/'.env')
    name = args.backend
    if name not in PROFILES:
        raise ValueError(f'Unknown model backend: {name}')
    prefix, default_model, default_url = PROFILES[name]
    model = getattr(args,'model',None) or os.getenv(prefix+'_MODEL') or default_model
    url = getattr(args,'base_url',None) or os.getenv(prefix+'_BASE_URL') or default_url
    url = url.rstrip('/')
    parts = urlsplit(url)
    if (parts.scheme not in ('http','https') or not parts.netloc or parts.query
            or parts.fragment or parts.username or parts.password):
        raise ValueError('Invalid model API base URL')
    if name=='vllm':
        if parts.path not in ('','/v1'):
            raise ValueError('vLLM base URL must use the root path or /v1')
        if not parts.path:
            url += '/v1'
    if url.endswith('/chat/completions'):
        raise ValueError('Use an API base URL, without /chat/completions')
    key = os.getenv(prefix+'_API_KEY') or ('EMPTY' if name=='vllm' else '')
    if not key:
        raise ValueError(f'Set {prefix}_API_KEY in extraction/.env')
    # 仅为当前进程和子进程桥接旧入口的变量名，不回写 .env 中原服务商配置。
    os.environ.update(EXTRACTION_BACKEND=name,DEEPSEEK_MODEL=model,
                      DEEPSEEK_BASE_URL=url,DEEPSEEK_API_KEY=key)
    if name=='vllm':
        os.environ.update(VLLM_MODEL=model,VLLM_BASE_URL=url)
    args.model, args.base_url = model, url
    return Backend(name,model,url,key)


def adapt_request(request, backend=None):
    if (backend or os.getenv('EXTRACTION_BACKEND','deepseek'))!='vllm':
        return request
    request = dict(request)
    body = dict(request.get('extra_body') or {})
    thinking = body.pop('thinking',None)
    options = dict(body.get('chat_template_kwargs') or {})
    options.setdefault('enable_thinking',bool(thinking and thinking.get('type')=='enabled'))
    body['chat_template_kwargs'] = options
    request['extra_body'] = body
    return request


def openai_client(*args, **kwargs):
    client = SDKOpenAI(*args,**kwargs)
    backend = os.getenv('EXTRACTION_BACKEND','deepseek')
    if backend=='vllm':
        create = client.chat.completions.create

        def complete(**request):
            return create(**adapt_request(request,backend))

        client.chat.completions.create = complete
    return client
