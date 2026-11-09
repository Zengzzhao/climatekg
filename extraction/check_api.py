"""用两个小请求检查抽取JSON和验证标签接口，不处理论文或记录密钥。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from validation_agent import generate_label, parse_label, resolve_model


def main():
    load_dotenv(Path(__file__).resolve().parent / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'))
    parser.add_argument('--base-url', default=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--output', type=Path, help='可选：保存不含密钥的连通报告')
    args = parser.parse_args()
    key = os.getenv('DEEPSEEK_API_KEY')
    if not key:
        parser.error('请在 extraction/.env 中配置 DEEPSEEK_API_KEY')
    if args.timeout <= 0:
        parser.error('timeout必须为正数')
    if args.base_url.rstrip('/').endswith('/chat/completions'):
        parser.error('base-url应填写API基址，例如 https://openai.cuc.edu.cn/v1')
    provider, model = resolve_model(args.model)
    if provider != 'deepseek':
        parser.error('此检查用于DeepSeek或CUC的OpenAI兼容模型')
    report = dict(base_url=args.base_url, requested_model=model,
                  checked_at=datetime.now(timezone.utc).isoformat(), complete=False)
    try:
        with OpenAI(api_key=key, base_url=args.base_url, timeout=args.timeout, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model, temperature=0, max_tokens=16384,
                response_format={'type': 'json_object'},
                extra_body={'thinking': {'type': 'disabled'}},
                messages=[{'role': 'user', 'content': 'API连通测试。请仅返回JSON：{"ok":true}。'}])
            choice = response.choices[0]
            if choice.finish_reason != 'stop' or json.loads(choice.message.content or '') != {'ok': True}:
                raise ValueError('JSON响应未通过检查')
            report['json_extraction'] = dict(
                passed=True, returned_model=response.model, finish_reason=choice.finish_reason,
                usage=response.usage.model_dump() if response.usage else None)
            label = generate_label(client, provider, model,
                                   'API标签测试，请只输出小写单词 valid，不要输出其他内容。')
            if not parse_label(label):
                raise ValueError('Validator/Evaluator标签响应未通过检查')
            report['validator_evaluator_label'] = dict(passed=True, response=label)
            report['complete'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        report['error'] = str(error).replace(key, '<redacted>')[:800]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
