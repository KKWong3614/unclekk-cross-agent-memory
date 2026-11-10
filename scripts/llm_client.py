#!/usr/bin/env python3
"""
LLM 客户端工具 - LLM API Client for Fact Refinement

支持 OpenAI 和 Anthropic API，用于提炼用户输入的事实。

用法:
    from llm_client import refine_fact

    # 使用 OpenAI
    result = refine_fact("用户输入", provider="openai", model="gpt-4o-mini")

    # 使用 Anthropic
    result = refine_fact("用户输入", provider="anthropic", model="claude-3-5-haiku-20241022")

    # 降级到本地提取
    result = refine_fact("用户输入", provider="local")
"""

import json
import os
import re
import sys
from shared_utils import validate_agent_id, extract_tags
from typing import Optional, Dict, Any
from datetime import datetime




def refine_fact(
    text: str,
    agent_id: str = "unknown",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    # 【安全修复】移除 api_key 参数，禁止通过函数参数传递 API Key
    # 统一从环境变量读取，避免敏感信息出现在调用栈或日志中
    fallback_to_local: bool = True
) -> Dict[str, Any]:
    """
    提炼用户输入的事实

    Args:
        text: 原始文本
        agent_id: Agent ID
        provider: LLM 提供商 ("openai", "anthropic", "local")
        model: 模型名称（可选）
        fallback_to_local: 失败时是否降级到本地提取

    Note:
        API Key 强制从环境变量读取（OPENAI_API_KEY / ANTHROPIC_API_KEY），
        禁止通过函数参数传递，避免敏感信息出现在进程参数列表中。

    Returns:
        提炼后的事实字典
    """
    # 从环境变量或配置中获取提供商
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "local")

    # 根据提供商调用对应的 API
    try:
        if provider == "openai":
            return refine_fact_openai(text, agent_id, model)
        elif provider == "anthropic":
            return refine_fact_anthropic(text, agent_id, model)
        elif provider == "local":
            return refine_fact_local(text, agent_id)
        else:
            print(f"警告: 未知的 LLM 提供商 '{provider}'，使用本地提取", file=sys.stderr)
            return refine_fact_local(text, agent_id)
    except Exception as e:
        print(f"警告: LLM 提炼失败 ({e})，{'降级到本地提取' if fallback_to_local else '返回原始文本'}", file=sys.stderr)
        if fallback_to_local:
            return refine_fact_local(text, agent_id)
        else:
            # 返回原始文本
            return {
                "fact": text.strip(),
                "original": text,
                "agents": [agent_id],
                "date": datetime.now().strftime("%Y-%m-%d"),
                "refined_at": datetime.now().isoformat(),
                "refinement_method": "failed",
                "error": str(e)
            }


def refine_fact_openai(
    text: str,
    agent_id: str,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    使用 OpenAI API 提炼事实

    Args:
        text: 原始文本
        agent_id: Agent ID
        model: 模型名称（默认 gpt-4o-mini）
        # API Key 强制从环境变量读取，不接受参数传递

    Returns:
        提炼后的事实字典
    """
    try:
        import openai
    except ImportError:
        raise ImportError("请安装 openai: pip install openai")

    # 获取 API Key（强制从环境变量读取，函数参数中已移除 api_key 防止泄露）
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到 OpenAI API Key。"
            "请设置环境变量: setx OPENAI_API_KEY \"your-key\" /M  (Windows)"
            "或 export OPENAI_API_KEY=\"your-key\"  (Linux/macOS)"
        )

    # 默认模型
    if model is None:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # 创建客户端
    client = openai.OpenAI(api_key=api_key)

    # 构造提示词
    prompt = f"""请从以下用户输入中提取客观事实，要求：
1. 去除主观情绪、语气词、冗余表达
2. 提取核心事实，保留关键信息（时间、地点、人物、事件）
3. 输出 JSON 格式，包含以下字段：
   - fact: 提炼后的事实（简洁、客观）
   - tags: 相关标签（数组，最多 5 个）
   - confidence: 置信度（0.0-1.0）

用户输入：
{text}

请直接输出 JSON，不要包含其他内容："""

    # 调用 API
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个专业的事实提取助手，擅长从用户输入中提炼客观事实。"},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=500
    )

    # 解析结果
    result_text = response.choices[0].message.content
    result = json.loads(result_text)

    # 返回标准格式
    return {
        "fact": result.get("fact", text.strip()),
        "original": text,
        "agents": [agent_id],
        "date": datetime.now().strftime("%Y-%m-%d"),
        "refined_at": datetime.now().isoformat(),
        "refinement_method": "openai",
        "model": model,
        "tags": result.get("tags", []),
        "confidence": result.get("confidence", 0.8)
    }


def refine_fact_anthropic(
    text: str,
    agent_id: str,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    使用 Anthropic API 提炼事实

    Args:
        text: 原始文本
        agent_id: Agent ID
        model: 模型名称（默认 claude-3-5-haiku-20241022）
        # API Key 强制从环境变量读取，不接受参数传递

    Returns:
        提炼后的事实字典
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("请安装 anthropic: pip install anthropic")

    # 获取 API Key（强制从环境变量读取，函数参数中已移除 api_key 防止泄露）
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到 Anthropic API Key。"
            "请设置环境变量: setx ANTHROPIC_API_KEY \"your-key\" /M  (Windows)"
            "或 export ANTHROPIC_API_KEY=\"your-key\"  (Linux/macOS)"
        )

    # 默认模型
    if model is None:
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

    # 创建客户端
    client = anthropic.Anthropic(api_key=api_key)

    # 构造提示词
    prompt = f"""请从以下用户输入中提取客观事实，要求：
1. 去除主观情绪、语气词、冗余表达
2. 提取核心事实，保留关键信息（时间、地点、人物、事件）
3. 输出 JSON 格式，包含以下字段：
   - fact: 提炼后的事实（简洁、客观）
   - tags: 相关标签（数组，最多 5 个）
   - confidence: 置信度（0.0-1.0）

用户输入：
{text}

请直接输出 JSON，不要包含其他内容："""

    # 调用 API
    response = client.messages.create(
        model=model,
        max_tokens=500,
        temperature=0.3,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    # 解析结果
    result_text = response.content[0].text
    result = json.loads(result_text)

    # 返回标准格式
    return {
        "fact": result.get("fact", text.strip()),
        "original": text,
        "agents": [agent_id],
        "date": datetime.now().strftime("%Y-%m-%d"),
        "refined_at": datetime.now().isoformat(),
        "refinement_method": "anthropic",
        "model": model,
        "tags": result.get("tags", []),
        "confidence": result.get("confidence", 0.8)
    }


def refine_fact_local(text: str, agent_id: str) -> Dict[str, Any]:
    """
    使用本地正则表达式提炼事实（降级方案）

    Args:
        text: 原始文本
        agent_id: Agent ID

    Returns:
        提炼后的事实字典
    """
    # 简化提炼：去除语气词、提取核心事实
    cleaned = re.sub(r"[吗吧呀啊呢的哈哦呃呃噢哇呀啰咧哇哈嘻哼]", "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    # 提取关键词标签
    tags = extract_tags(cleaned)

    return {
        "fact": cleaned,
        "original": text,
        "agents": [agent_id],
        "date": datetime.now().strftime("%Y-%m-%d"),
        "refined_at": datetime.now().isoformat(),
        "refinement_method": "local",
        "tags": tags,
        "confidence": 0.6  # 本地提取置信度较低
    }



# 测试代码
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM 客户端测试")
    parser.add_argument("--text", default="我今天买了一只股票，感觉还不错，希望明天能涨", help="测试文本")
    parser.add_argument("--provider", default="local", choices=["openai", "anthropic", "local"], help="LLM 提供商")
    parser.add_argument("--model", help="模型名称")
    args = parser.parse_args()

    print(f"原始文本: {args.text}")
    print(f"提供商: {args.provider}")
    print("-" * 50)

    result = refine_fact(
        text=args.text,
        agent_id="test",
        provider=args.provider,
        model=args.model
    )

    print("提炼结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 安全说明
    # API Key 强制从环境变量读取，禁止通过函数参数传递
    # Windows: setx OPENAI_API_KEY "your-key" /M
    # Linux/macOS: export OPENAI_API_KEY="your-key"
