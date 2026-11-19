#!/usr/bin/env python3
"""测试中转服务的模型调用与思考字段；密钥从 ZRSHUIWU_API_KEY 或隐藏输入读取。"""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 不将 Authorization 转发给重定向目标。
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='deepseek-v4.1-flash-1017')
    parser.add_argument('--identity-checks', action='store_true',
                        help='额外检查不存在的模型名是否被拒绝；不能据此认证底层模型')
    parser.add_argument('--prompt', default='hi')
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_tokens <= 0:
        parser.error('timeout 和 max-tokens 必须大于 0')
    key = os.environ.get('ZRSHUIWU_API_KEY')
    if not key and sys.stdin.isatty():
        key = getpass.getpass('ZRSHUIWU_API_KEY（隐藏输入）: ')
    if not key:
        parser.error('请设置 ZRSHUIWU_API_KEY 环境变量')
    base = 'https://llm.zrshuiwu.com/v1'
    opener = build_opener(NoRedirect())

    def request(path, payload=None):
        headers = {'Authorization': f'Bearer {key}', 'Accept': 'application/json',
                   'User-Agent': 'climatekg-api-probe/1.0'}
        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload).encode()
        started = time.monotonic()
        result = {'endpoint': base + path}
        try:
            with opener.open(Request(base + path, data=body, headers=headers),
                             timeout=args.timeout) as response:
                result['http_status'] = response.status
                result['response_headers'] = {name: value for name, value in response.headers.items()
                    if name.lower() in ('server', 'content-type', 'x-request-id', 'x-oneapi-request-id',
                                        'x-new-api-request-id', 'cf-ray')}
                raw = response.read().decode('utf-8', errors='replace')
            try:
                result['response'] = json.loads(raw)
            except ValueError:
                result['error'] = '响应不是 JSON'
                result['body_preview'] = raw[:2000]
        except HTTPError as error:
            result['http_status'] = error.code
            result['error'] = error.read().decode('utf-8', errors='replace')[:4000]
        except (URLError, TimeoutError, OSError) as error:
            result['error'] = str(error)
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        return result

    report = {'tested_at_utc': datetime.now(timezone.utc).isoformat(),
              'requested_model': args.model, 'prompt': args.prompt, 'checks': []}

    def record(result):
        # 即使服务端在错误消息中回显密钥，也不输出或保存它。
        safe = json.loads(json.dumps(result, ensure_ascii=False).replace(key, '<redacted>'))
        report['checks'].append(safe)
        print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)

    models = request('/models')
    models['test'] = 'models'
    data = models.get('response', {})
    if isinstance(data, dict) and isinstance(data.get('data'), list):
        models['target_listed'] = any(isinstance(m, dict) and m.get('id') == args.model
                                      for m in data['data'])
        models['model_count'] = len(data['data'])
        # 模型列表只保留目标条目，避免报告过长。
        models['response'] = {'data': [m for m in data['data']
                                      if isinstance(m, dict) and m.get('id') == args.model]}
    record(models)

    for mode in ('default', 'thinking_disabled', 'thinking_enabled'):
        payload = {'model': args.model, 'messages': [{'role': 'user', 'content': args.prompt}],
                   'max_tokens': args.max_tokens, 'stream': False}
        if mode != 'default':
            payload['thinking'] = {'type': mode.removeprefix('thinking_')}
        result = request('/chat/completions', payload)
        result.update(test=mode, request=payload)
        response = result.get('response', {})
        choices = response.get('choices') if isinstance(response, dict) else None
        message = choices[0].get('message', {}) if choices and isinstance(choices[0], dict) else {}
        fields = {name: value for name, value in message.items()
                  if 'reason' in name.lower() or 'think' in name.lower()}
        result['reasoning_fields'] = fields
        result['has_reasoning_content'] = any(bool(value) for value in fields.values())
        content = message.get('content')
        result['has_think_tags'] = isinstance(content, str) and '<think>' in content
        result['chat_ok'] = result.get('http_status') == 200 and bool(choices) and bool(content)
        record(result)

    if args.identity_checks:
        # 用可独立计算的题目检查思考开关；答对也不能用于认证模型身份。
        for mode in ('disabled', 'enabled'):
            payload = {'model': args.model, 'max_tokens': 1024, 'stream': False,
                       'thinking': {'type': mode},
                       'messages': [{'role': 'user', 'content':
                           'How many binary strings of length 12 contain exactly five 1s '
                           'and no adjacent 1s? Return only JSON with one integer field "answer".'}],
                       'response_format': {'type': 'json_object'}}
            result = request('/chat/completions', payload)
            result.update(test='reasoning_task_' + mode, request=payload, expected_answer=56)
            response = result.get('response', {})
            choices = response.get('choices', []) if isinstance(response, dict) else []
            message = choices[0].get('message', {}) if choices else {}
            result['has_reasoning_content'] = bool(message.get('reasoning_content'))
            try:
                result['answer_correct'] = json.loads(message.get('content') or '') == {'answer': 56}
            except (ValueError, TypeError):
                result['answer_correct'] = False
            record(result)

        payload = {'model': 'identity-negative-control-does-not-exist-20261116',
                   'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 16}
        result = request('/chat/completions', payload)
        result.update(test='nonexistent_model_control', request=payload)
        record(result)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'报告：{args.output}', flush=True)
    return 0 if any(c.get('chat_ok') for c in report['checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
