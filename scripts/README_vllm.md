# 本地调用算力自由上的 vLLM

## 本地测试

测试脚本只使用 Python 3 标准库，无需安装依赖。在项目根目录运行：

```bash
python3 scripts/test_vllm_api.py
```

脚本依次调用模型列表和非流式对话接口，显示回答、耗时及 Token 用量。全部成功退出码为 0，任一失败退出码为 1。即使模型列表检查失败，也会继续检查对话接口。

默认配置：

- API 基址：`https://lryd5kgi-bwbkdaqq-8000.ahrestapi.gpufree.cn:8443/v1`
- 模型：`Qwen/Qwen3-32B-AWQ`，与 `--served-model-name` 一致。
- 单次请求网络超时：60 秒。
- 请求明确设置 `chat_template_kwargs.enable_thinking=false`。

可覆盖默认配置：

```bash
python3 scripts/test_vllm_api.py \
  --base-url 'https://lryd5kgi-bwbkdaqq-8000.ahrestapi.gpufree.cn:8443/v1' \
  --model 'Qwen/Qwen3-32B-AWQ' \
  --timeout 120 \
  --prompt '请用一句话介绍你自己。'
```

也可用 `VLLM_BASE_URL`、`VLLM_MODEL` 环境变量配置。服务端启用 `--api-key` 时，将相同密钥放入本地 `VLLM_API_KEY` 环境变量，脚本会添加 Bearer 请求头。当前提供的启动命令没有启用 API key，测试时无需设置。

## 修复端口与监听地址

原命令监听 `127.0.0.1:8001`，平台公网地址对应 `8000`。如果已配置 `8000 → 127.0.0.1:8001` 的反向代理，可以保留原命令并检查代理日志；否则，建议停止原 vLLM 进程，然后让 vLLM 直接监听平台开放的端口：

```bash
vllm serve /root/gpufree-data/models/Qwen3-32B-AWQ \
  --served-model-name Qwen/Qwen3-32B-AWQ \
  --host 0.0.0.0 \
  --port 8000 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 1 \
  --default-chat-template-kwargs '{"enable_thinking": false}'
```

先在服务器检查监听情况，确认 8000 没有被其他服务占用：

```bash
ss -lntp | grep -E ':8000|:8001'
curl -sS http://127.0.0.1:8001/v1/models
```

上面的第二条命令用于检查原服务。改为 8000 并等待模型加载完成后，检查新服务：

```bash
curl -sS http://127.0.0.1:8000/v1/models
```

服务器本机检查成功后，再从本地运行测试脚本。`0.0.0.0` 是服务器监听地址，本地客户端仍使用公网 HTTPS 地址。

## 与已有程序集成

vLLM 提供 OpenAI 兼容接口。已有兼容客户端应配置上述 API 基址、模型名称，以及服务端实际启用的密钥。对话请求是 `POST /v1/chat/completions`；基址已含 `/v1` 时，不要重复拼接。

本项目测试脚本中的 `request_json()` 也可直接复用，完整请求体见 `check_chat()`。

官方接口说明：[vLLM OpenAI 兼容服务](https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/)。
