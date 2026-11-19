#!/usr/bin/env python3
"""从本地检查远程 vLLM 的模型列表及实际推理；只使用 Python 标准库。"""

import argparse
import json
import os
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://lryd5kgi-bwbkdaqq-8000.ahrestapi.gpufree.cn:8443/v1"
DEFAULT_MODEL = "Qwen/Qwen3-32B-AWQ"


def request_json(url, api_key, timeout, payload=None):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        hints = {
            401: "检查 VLLM_API_KEY 是否与服务端 --api-key 一致。",
            403: "检查平台访问权限或鉴权配置。",
            404: "检查 /v1 路径和模型名称是否正确。",
            502: "网关未取得有效上游响应；检查开放的 8000 端口、vLLM 的 8001 端口及转发配置。",
            503: "检查模型是否加载完成及平台实例是否正在运行。",
            504: "网关等待上游超时；检查 vLLM 日志和推理耗时。",
        }
        raise RuntimeError(
            f"HTTP {error.code}: {detail}\n{hints.get(error.code, '请检查 vLLM 服务日志。')}"
        ) from error
    except (URLError, TimeoutError, socket.timeout) as error:
        raise RuntimeError(f"网络连接失败：{error}；检查域名、网络、代理及超时设置。") from error
    try:
        result = json.loads(raw)
    except ValueError as error:
        raise RuntimeError(f"接口未返回 JSON，可能访问到了平台页面：{raw[:300]}") from error
    if not isinstance(result, dict):
        raise RuntimeError("接口返回的 JSON 不是对象。")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout", type=float, default=60, help="每次请求的网络超时秒数")
    parser.add_argument("--prompt", default="你好，这是接口连通测试。请简短回答：1+1等于几？")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    base_url = args.base_url.rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        parser.error("--base-url 必须为完整的 http/https 地址")
    if parts.query or parts.fragment or parts.username or parts.password:
        parser.error("--base-url 不应包含查询参数、片段或用户名密码")
    if parts.path.rstrip("/") not in ("", "/v1"):
        parser.error("--base-url 请使用服务根地址或以 /v1 结尾的地址")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    api_key = os.getenv("VLLM_API_KEY", "")
    print(f"API 地址：{base_url}\n请求模型：{args.model}", flush=True)
    checks = []

    def run_check(name, check):
        print(f"\n{name}", flush=True)
        started = time.monotonic()
        try:
            check()
        except (RuntimeError, KeyError, IndexError, TypeError, ValueError) as error:
            message = str(error)
            if api_key:
                message = message.replace(api_key, "<redacted>")
            print(f"失败（{time.monotonic() - started:.2f}s）：{message}", flush=True)
            checks.append(False)
        else:
            print(f"通过（{time.monotonic() - started:.2f}s）", flush=True)
            checks.append(True)

    def check_models():
        result = request_json(f"{base_url}/models", api_key, args.timeout)
        models = [item["id"] for item in result["data"]]
        print("服务端模型：" + json.dumps(models, ensure_ascii=False))
        if args.model not in models:
            raise RuntimeError("目标模型未出现在列表中，请将 --model 设置为服务端返回的名称。")

    def check_chat():
        result = request_json(
            f"{base_url}/chat/completions", api_key, args.timeout,
            {
                "model": args.model,
                "messages": [{"role": "user", "content": args.prompt}],
                "temperature": 0,
                "max_tokens": 128,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        choice = result["choices"][0]
        content = choice["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("未取得非空模型回答：" + json.dumps(result, ensure_ascii=False)[:1000])
        print(f"模型回答：{content}")
        print(f"结束原因：{choice.get('finish_reason')}")
        print("Token 用量：" + json.dumps(result.get("usage"), ensure_ascii=False))

    run_check("[1/2] GET /v1/models", check_models)
    run_check("[2/2] POST /v1/chat/completions", check_chat)
    if all(checks):
        print("\n验证成功：本地可以通过公网接口获取模型列表并调用模型。")
        return 0
    print("\n验证未通过：请按以上错误提示排查，再运行相同命令。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
