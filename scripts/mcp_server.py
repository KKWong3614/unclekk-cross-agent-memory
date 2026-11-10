# ─────────────────────────────────────────────────────────────────────────────
# 导入共享工具函数库（消除代码重复）
# ─────────────────────────────────────────────────────────────────────────────
from shared_utils import (
    check_sensitive_info as _check_sensitive_info,
    fact_id_from_text as _fact_id_from_text,
    to_ngrams as _to_ngrams,
    is_duplicate as _is_duplicate,
    extract_tags as _extract_tags,
    refine_local as _refine_local,
    validate_agent_id as _validate_agent_id,
    validate_days as _validate_days,
    validate_limit as _validate_limit,
    validate_fact as _validate_fact,
    validate_query as _validate_query,
    calculate_weight as _calculate_weight,
    today_str as _today_str,
    now_ts as _now_ts,
    MEMORY_LEVELS as _MEMORY_LEVELS,
    MAX_DAYS as _MAX_DAYS,
    MAX_LIMIT as _MAX_LIMIT,
    MAX_FACT_LEN as _MAX_FACT_LEN,
    AGENT_ID_PATTERN as _AGENT_ID_PATTERN,
    MEMORY_BASE,
    DAILY_DIR,
    SHARED_DIR,
    REFINED_DIR,
    AUDIT_DIR,
    INDEX_FILE,
)
import shared_memory
import mysql_store
import argparse
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import hashlib


"""
Cross-Agent-Memory MCP Server
基于 FastMCP 3.2.4 的 Model Context Protocol 服务器
让所有 Agent 通过 MCP 协议调用共享记忆功能

启动方式（stdio 模式，mcporter 集成）:
    python mcp_server.py
"""
import io

# 修复 Windows 编码（防止重复包装导致 buffer 关闭）
if not getattr(sys.stdout, '_cam_utf8', False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdout._cam_utf8 = True
if not getattr(sys.stderr, '_cam_utf8', False):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stderr._cam_utf8 = True

try:
    from fastmcp import FastMCP, Context
except ImportError:
    sys.stderr.write(
        "❌ 缺少依赖 fastmcp，无法启动 MCP 服务。\n"
        "   请先安装：pip install fastmcp\n"
        "   （仅 MCP 模式需要；CLI 与 JSONL 存储模式无需 fastmcp）\n"
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 导入共享工具函数库（消除代码重复）
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 安全：导入文件锁（解决并发竞态条件）
# ─────────────────────────────────────────────────────────────────────────────
import contextlib
try:
    from filelock import FileLock
    HAS_FILE_LOCK = True
except ImportError:
    HAS_FILE_LOCK = False
    # 无锁时打印警告（注意：并发写入可能覆盖数据）
    import warnings
    warnings.warn("未找到 file_lock 模块，并发写入保护未启用，多 Agent 并发写入可能导致数据竞态", UserWarning)

# 路径设置
SKILL_DIR = Path(__file__).parent
# 统一锁文件（解决数据文件+索引文件竞争条件）
MEMORY_LOCK_FILE = MEMORY_BASE / "memory.lock"

# 删除保护：被删除记录先备份到 trash/，可恢复（绝不裸删）
BACKUP_DIR = MEMORY_BASE / "trash"


def _err(code: str, message: str, hint: str = None) -> dict:
    """统一的错误响应结构，避免向 Agent 泄漏原始 traceback（TRACE·R 异常处理 4.0）。"""
    return {"success": False, "error": code, "message": message, "hint": hint}


def _filter_by_agent(facts: list, agent_id: str) -> list:
    """按 agent_id 严格隔离：scope='mine' 时只返回包含该 agent_id 的记录（TRACE·R 功能完善性 4.0）。

    修复 M-1（隔离旁路）：默认/未知身份（unknown / mcp_server / mcp_scheduler）不对应任何真实
    Agent，绝不能返回全量记录——否则 scope='mine' 在默认参数下会泄漏全部跨 Agent 数据。
    故未知身份返回空列表，与 memory_delete 的 scope='mine' 守卫保持对称。
    """
    if not agent_id or agent_id in ("unknown", "mcp_server", "mcp_scheduler"):
        return []
    return [f for f in facts if agent_id in f.get("agents", [])]


# scope='all' 共享读设计声明（P1-2 第四轮审计）：scope='all' 是跨 Agent 共享读模式而非隔离边界，
# 未知身份默认可读全量。隔离边界只有 scope='mine'（要求真实 agent_id）。
# 不把 unknown+scope='all' 改为拒绝，是为了不破坏未传 agent_id 的现有调用方；改为显式 warning 提示。
_SCOPE_ALL_WARNING = (
    "scope='all' 为跨Agent共享读设计（非隔离边界）：当前 agent_id 为未知身份，已返回全量共享记忆。"
    "如需仅读取本 Agent 记录，请传入真实 agent_id 并使用 scope='mine'。"
)


def _scope_all_warning(agent_id: str):
    """scope='all' 且身份未知时返回提示文案，否则返回 None（P1-2 修复：显式声明共享读边界）。"""
    if not agent_id or agent_id in ("unknown", "mcp_server", "mcp_scheduler"):
        return _SCOPE_ALL_WARNING
    return None


def _safe_read_lines(path: Path) -> list:
    """安全读取 jsonl，坏行/IO 错误一律跳过，绝不抛异常。"""
    lines = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(line)
    except (OSError, UnicodeError):
        pass
    return lines


def _with_lock(timeout: float = 15):
    """统一写入锁上下文（所有文件写操作共用一把锁，消除并发重复/覆盖竞态）。

    filelock 缺失时（HAS_FILE_LOCK=False）降级为 contextlib.nullcontext——写入不崩溃，
    但此时无文件锁保护，多 Agent 高频并发同写同一文件存在覆盖竞态（导入处已警告）。
    与 shared_memory.py 的 CLI 降级策略一致。
    """
    if HAS_FILE_LOCK:
        return FileLock(str(MEMORY_LOCK_FILE), timeout=timeout)
    return contextlib.nullcontext()

# ─────────────────────────────────────────────────────────────────────────────
# MySQL/TiDB 存储层（本地数据主权）
# ─────────────────────────────────────────────────────────────────────────────
try:
    from mysql_store import get_store, close_store, HAS_MYSQL
    if HAS_MYSQL:
        print("[INFO] MySQL 存储层已启用", file=sys.stderr)
    else:
        print("[WARN] pymysql 未安装，降级到文件存储", file=sys.stderr)
except ImportError:
    HAS_MYSQL = False
    print("[WARN] mysql_store 模块未找到，降级到文件存储", file=sys.stderr)

# 初始化 FastMCP
mcp = FastMCP(
    "cross-agent-memory",
    instructions=(
        "跨Agent共享记忆系统。存储路径: ~/.hermes/memory_data/  "
        "工具: memory_write(写入) | memory_search(搜索) | memory_read_all(全量读取) | "
        "memory_stats(统计) | memory_delete(删除) | memory_verify(健康检查) | "
        "memory_refine_batch(批量提炼) | memory_boot_context(启动注入)"
    ),
)


# ═══════════════════════════════════════════════════════════════════════════════
# 安全验证
# ═══════════════════════════════════════════════════════════════════════════════

# 合法 agent_id 正则
AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 参数上限
MAX_DAYS = 365  # 最多查询 1 年数据
MAX_LIMIT = 100  # 最多返回 100 条
MAX_FACT_LEN = 10000  # 单条记忆最大 10KB

# 记忆分级配置（v3.0）
MEMORY_LEVELS = {
    "core":     {"half_life": None,  "base_weight": 1.0,  "boot_priority": 100, "label": "核心"},
    "important":{"half_life": 30,    "base_weight": 0.8,  "boot_priority": 80,  "label": "重要"},
    "normal":   {"half_life": 7,     "base_weight": 0.5,  "boot_priority": 50,  "label": "普通"},
    "temp":     {"half_life": 1,     "base_weight": 0.2,  "boot_priority": 20,  "label": "临时"},
}


def _log_audit_secure(action: str, agent_id: str, data: dict, ctx: Optional[Context] = None):
    """安全审计日志：记录真实来源（MCP session）和声称的 agent_id
    
    注意：MCP 协议本身无身份验证层，agent_id 由调用方声称，无法独立验证真伪。
    此函数记录两个字段供审计追踪：
    - agent_id_claimed: 调用方声称的身份
    - mcp_session_id: MCP 会话 ID（可用于关联同一会话内的多次调用）
    """
    _ensure_dirs()
    # 获取真实来源（FastMCP Context 中的 session 信息）
    mcp_session_id = "unknown"
    if ctx:
        try:
            mcp_session_id = getattr(ctx, "session_id", None) or getattr(ctx, "client_id", None) or "mcp_client"
        except Exception as e:
            import sys
            print(f"[WARN] _log_audit_secure: 获取session_id失败: {e}", file=sys.stderr)
            mcp_session_id = "mcp_client"
    
    entry = {
        "ts": _now_ts(),
        "action": action,
        "agent_id_claimed": agent_id,       # 调用方声称的身份（无法独立验证）
        "mcp_session_id": mcp_session_id,  # MCP 会话 ID（用于关联追踪）
        **data
    }
    audit_file = AUDIT_DIR / f"{_today_str()}.jsonl"
    try:
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        import sys
        print(f"[WARN] _log_audit: 审计日志写入失败: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# 核心工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_dirs():
    for d in [DAILY_DIR, SHARED_DIR, AUDIT_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("", encoding="utf-8")


def _read_shared_facts(days: int = 30) -> list[dict]:
    """
    读取共享事实：从共享目录读取最近 N 天的提炼事实。
    
    Args:
        days: 读取最近 N 天的数据（默认 30 天）
    
    Returns:
        list[dict]: 共享事实列表，每个事实包含 fact_id, fact_text, tags, agent_ids, level 等字段
    """
    facts = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if not SHARED_DIR.exists():
        return facts
    for f in sorted(SHARED_DIR.glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    facts.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] mcp_server.py read facts: {e}", file=sys.stderr)
    return facts


def _llm_refine(text: str, agent_id: str) -> dict:
    """
    LLM 提炼：通过 LLM 将原始文本提炼为结构化事实。
    
    根据配置自动选择 OpenAI 或 Anthropic 模型，失败时降级到本地规则。
    
    Args:
        text: 原始记忆文本
        agent_id: Agent 标识
    
    Returns:
        dict: 提炼后的事实，包含 fact_id, fact_text, tags, agent_ids, level 等字段
    
    Note:
        - 优先使用 OpenAI GPT-4o
        - 备选 Anthropic Claude 3.5 Sonnet
        - 失败时降级到 llm_refine_local
    """
    provider = os.getenv("LLM_PROVIDER", "local")
    try:
        if provider == "openai":
            return _refine_openai(text, agent_id)
        elif provider == "anthropic":
            return _refine_anthropic(text, agent_id)
        return _refine_local(text, agent_id)
    except Exception as e:
        print(f"[WARN] _llm_refine: LLM provider error: {e}, fallback to local", file=sys.stderr)
        return _refine_local(text, agent_id)


def _refine_openai(text: str, agent_id: str) -> dict:
    """
    OpenAI 提炼：使用 OpenAI GPT-4o 模型提炼记忆事实。
    
    Args:
        text: 原始记忆文本
        agent_id: Agent 标识
    
    Returns:
        dict: 提炼后的事实，包含 fact_id, fact_text, tags, agent_ids, level 等字段
    
    Raises:
        Exception: OpenAI API 调用失败时抛出异常
    """
    import openai
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _refine_local(text, agent_id)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = openai.OpenAI(api_key=api_key)
    prompt = (
        f"从以下输入中提取客观事实，去除情绪和语气词，保留核心信息。"
        f"输出JSON格式: {{\"fact\": \"...\", \"tags\": [...], \"confidence\": 0.0-1.0}}\n\n输入：{text}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是专业的事实提取助手，输出标准JSON。"},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=500,
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "fact": result.get("fact", text.strip()),
            "original": text,
            "agents": [agent_id],
            "date": _today_str(),
            "refined_at": _now_ts(),
            "refinement_method": "openai",
            "model": model,
            "tags": result.get("tags", _extract_tags(text)),
            "confidence": result.get("confidence", 0.8),
        }
    except Exception as e:
        print(f"[WARN] _llm_refine: LLM provider error: {e}, fallback to local", file=sys.stderr)
        return _refine_local(text, agent_id)


def _refine_anthropic(text: str, agent_id: str) -> dict:
    """
    Anthropic 提炼：使用 Anthropic Claude 3.5 Sonnet 模型提炼记忆事实。
    
    Args:
        text: 原始记忆文本
        agent_id: Agent 标识
    
    Returns:
        dict: 提炼后的事实，包含 fact_id, fact_text, tags, agent_ids, level 等字段
    
    Raises:
        Exception: Anthropic API 调用失败时抛出异常
    """
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _refine_local(text, agent_id)
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f"从以下输入中提取客观事实，去除情绪和语气词，保留核心信息。"
        f"输出JSON格式: {{\"fact\": \"...\", \"tags\": [...], \"confidence\": 0.0-1.0}}\n\n输入：{text}"
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=500, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(resp.content[0].text)
        return {
            "fact": result.get("fact", text.strip()),
            "original": text,
            "agents": [agent_id],
            "date": _today_str(),
            "refined_at": _now_ts(),
            "refinement_method": "anthropic",
            "model": model,
            "tags": result.get("tags", _extract_tags(text)),
            "confidence": result.get("confidence", 0.8),
        }
    except Exception as e:
        print(f"[WARN] _llm_refine: LLM provider error: {e}, fallback to local", file=sys.stderr)
        return _refine_local(text, agent_id)


def _save_index_entry(entry: dict):
    with open(INDEX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# MCP 工具定义
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def memory_write(
    ctx: Context,
    agent_id: str,
    fact: str,
    skip_refine: bool = False,
) -> dict:
    """
    写入跨Agent共享记忆（核心写入工具）

    Args:
        agent_id: 调用方Agent的唯一标识（如 "main", "analyst", "coder"）
        fact: 记忆内容，建议是客观事实描述（会自动提炼）
        skip_refine: True=跳过LLM提炼，直接存储原文

    Returns:
        包含fact_id、提炼结果、去重状态的字典
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_fact(fact)
    if not valid:
        return {"success": False, "error": err}
    
    # 【安全修复】敏感信息检测警告
    warnings = _check_sensitive_info(fact)
    has_sensitive = bool(warnings)
    
    _ensure_dirs()
    fact_text = fact.strip()
    fact_id = _fact_id_from_text(fact_text)
    date_str = _today_str()

    # 【先读后动】MySQL 存储层内部已实现先检查是否存在再写入的逻辑
    # 【MySQL 优先】如果 MySQL 可用，使用 MySQL 存储
    if HAS_MYSQL:
        try:
            store = get_store()
            # 提炼
            refined = _llm_refine(fact_text, agent_id) if not skip_refine else {
                "fact": fact_text, "original": fact_text, "agents": [agent_id],
                "date": date_str, "refined_at": _now_ts(), "refinement_method": "none",
                "tags": _extract_tags(fact_text), "confidence": 0.5,
            }
            result = store.write_memory(
                fact_id=fact_id,
                fact=refined["fact"],
                original=refined.get("original", fact_text),
                agent_id=agent_id,
                level="normal",
                confidence=refined.get("confidence", 0.5),
                tags=refined.get("tags", []),
                refinement_method=refined.get("refinement_method", "local"),
            )
            if result["success"]:
                _log_audit_secure("mcp_write_mysql", agent_id, {
                    "fact_id": fact_id, "action": result["action"]
                }, ctx)
                ctx.info(f"[memory_write] MySQL mode, agent={agent_id} fact_id={fact_id[:16]}")
                return {
                    "success": True,
                    "action": result["action"],
                    "fact_id": fact_id[:16],
                    "fact": refined["fact"],
                    "original": refined.get("original", ""),
                    "agents": refined["agents"],
                    "tags": refined.get("tags", []),
                    "refinement_method": refined.get("refinement_method", "local"),
                    "confidence": refined.get("confidence", 0.5),
                    "sensitive_warning": warnings if has_sensitive else None,
                    "storage": "mysql",
                    "message": f"MySQL写入成功，action={result['action']}",
                }
            else:
                # MySQL 写入失败，降级到文件存储
                print(f"[WARN] MySQL 写入失败，降级到文件存储: {result.get('error')}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] MySQL 存储异常，降级到文件存储: {e}", file=sys.stderr)

    try:
        # 统一写入锁：把「先读(查重) → 后动(写/合并)」包在锁内，消除并发重复/覆盖竞态（TRACE·R 运行稳定性 3.8）
        with _with_lock(timeout=15):
            existing = _read_shared_facts(days=90)
            dup = _is_duplicate(fact_text, existing)

            if dup:
                # 去重命中：原地更新 agents 列表，不重复追加记录
                dup_fact_id = dup.get("fact_id", "")
                dup_date = dup.get("date", date_str)
                out_file = SHARED_DIR / f"{dup_date}.jsonl"
                lines_data = _safe_read_lines(out_file) if out_file.exists() else []
                updated = False
                new_lines = []
                for ln in lines_data:
                    try:
                        rec = json.loads(ln)
                    except json.JSONDecodeError:
                        new_lines.append(ln)
                        continue
                    if rec.get("fact_id") == dup_fact_id:
                        if agent_id not in rec.get("agents", []):
                            rec.setdefault("agents", []).append(agent_id)
                            rec["refined_at"] = _now_ts()
                        updated = True
                        new_lines.append(json.dumps(rec, ensure_ascii=False))
                    else:
                        new_lines.append(ln)
                if updated:
                    out_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    _save_index_entry({
                        "fact_id": dup_fact_id, "date": dup_date,
                        "file": str(out_file), "agents": dup.get("agents", []),
                        "tags": dup.get("tags", []),
                    })
                _log_audit_secure("mcp_write_file", agent_id, {"fact_id": dup_fact_id, "action": "merged"}, ctx)
                ctx.info(f"[memory_write] merged existing, agent={agent_id} fact_id={dup_fact_id[:16]}")
                return {
                    "success": True, "action": "merged", "fact_id": dup_fact_id[:16],
                    "fact": dup.get("fact", ""), "original": dup.get("original", ""),
                    "agents": dup.get("agents", []),
                    "storage": "jsonl",
                    "message": "发现重复记忆，已合并agent",
                }

            # 非重复，写入新记录（同时写索引，修复旧版新写入漏写 index 的问题）
            refined = _llm_refine(fact_text, agent_id) if not skip_refine else {
                "fact": fact_text, "original": fact_text, "agents": [agent_id],
                "date": date_str, "refined_at": _now_ts(), "refinement_method": "none",
                "tags": _extract_tags(fact_text), "confidence": 0.5,
            }
            out_file = SHARED_DIR / f"{date_str}.jsonl"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            new_entry = {
                "fact_id": fact_id, "fact": refined.get("fact", fact_text),
                "original": refined.get("original", fact_text), "agents": refined.get("agents", [agent_id]),
                "date": date_str, "refined_at": _now_ts(), "tags": refined.get("tags", []),
                "confidence": refined.get("confidence", 0.5), "level": "normal",
                "refinement_method": refined.get("refinement_method", "local"),
                "sensitive_warning": warnings if has_sensitive else None,
            }
            with open(out_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(new_entry, ensure_ascii=False) + "\n")
            _save_index_entry({
                "fact_id": fact_id, "date": date_str, "file": str(out_file),
                "agents": refined.get("agents", [agent_id]), "tags": refined.get("tags", []),
            })
            _log_audit_secure("mcp_write_file", agent_id, {"fact_id": fact_id}, ctx)
            ctx.info(f"[memory_write] file mode, agent={agent_id} fact_id={fact_id[:16]}")
            return {
                "success": True, "action": "new", "fact_id": fact_id[:16],
                "fact": refined.get("fact", fact_text), "original": refined.get("original", ""),
                "agents": refined.get("agents", []), "tags": refined.get("tags", []),
                "refinement_method": refined.get("refinement_method", "local"),
                "confidence": refined.get("confidence", 0.5),
                "storage": "jsonl",
                "message": "文件存储写入成功",
            }
    except Exception as e:
        return _err("write_failed", "文件存储写入失败",
                    f"检查存储目录权限或磁盘空间；MySQL 模式下会自动降级到文件。详情: {e}")


@mcp.tool()
def memory_search(
    ctx: Context,
    query: str,
    agent_id: str = "unknown",
    days: int = 30,
    limit: int = 10,
    scope: str = "all",
) -> dict:
    """
    搜索跨Agent共享记忆

    Args:
        query: 搜索关键词（匹配fact和tags字段）
        agent_id: 调用方标识（用于审计）
        days: 搜索近N天记忆（默认30）
        limit: 返回最多N条（默认10）

    Returns:
        匹配的记忆列表，按相关性排序
    """
    # 输入验证
    valid, err = _validate_query(query)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_days(days)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_limit(limit)
    if not valid:
        return {"success": False, "error": err}
    
    # 【MySQL 优先】如果 MySQL 可用，使用 MySQL 搜索
    if HAS_MYSQL:
        try:
            store = get_store()
            results = store.search_memories(
                query=query, days=days, limit=limit,
                agent_id=agent_id if scope == "mine" else None,
            )
            total = len(store.get_all_memories(days=days))
            _log_audit_secure("mcp_search_mysql", agent_id, {
                "query": query, "results": len(results)
            }, ctx)
            ctx.info(f"[memory_search] MySQL mode, query='{query}' results={len(results)}")
            return {
                "success": True,
                "results": results,
                "total_searched": total,
                "warning": _scope_all_warning(agent_id),
                "message": f"MySQL搜索完成，在{total}条记忆中找到{len(results)}条匹配",
            }
        except Exception as e:
            print(f"[WARN] MySQL 搜索异常，降级到文件搜索: {e}", file=sys.stderr)
    
    _ensure_dirs()
    facts = _read_shared_facts(days=days)
    if scope == "mine":
        facts = _filter_by_agent(facts, agent_id)
    query_lower = query.lower()
    scored = []
    for f in facts:
        score = 0
        text = f.get("fact", "") + " " + " ".join(f.get("tags", []))
        text_lower = text.lower()
        for word in query_lower.split():
            if word in text_lower:
                score += 1
        for tag in f.get("tags", []):
            if query_lower in tag.lower():
                score += 3
        if score > 0:
            scored.append((score, len(text), f))
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    results = [{"score": s, **f} for s, _, f in scored[:limit]]
    _log_audit_secure("mcp_search", agent_id, {"query": query, "results": len(results)}, ctx)
    ctx.info(f"[memory_search] query='{query}' results={len(results)}")
    if not results:
        return {
            "success": True, "results": [], "total_searched": len(facts),
            "warning": _scope_all_warning(agent_id),
            "message": f"在近{days}天{len(facts)}条记忆中未找到匹配",
        }
    return {
        "success": True,
        "results": results,
        "total_searched": len(facts),
        "warning": _scope_all_warning(agent_id),
        "message": f"在近{days}天{len(facts)}条记忆中找到{len(results)}条匹配",
    }


@mcp.tool()
def memory_read_all(
    ctx: Context,
    agent_id: str = "unknown",
    days: int = 30,
    scope: str = "all",
) -> dict:
    """
    读取近N天的所有共享记忆（全量返回）

    Args:
        agent_id: 调用方标识
        days: 读取近N天（默认30，最大365）

    Returns:
        所有共享记忆列表
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_days(days)
    if not valid:
        return {"success": False, "error": err}

    # 【MySQL 优先】如果 MySQL 可用，从 MySQL 读取（v6.22 补齐：旧版 read_all 无 MySQL 分支，曾返回空）
    if HAS_MYSQL and hasattr(get_store(), 'get_all_memories'):
        try:
            store = get_store()
            facts = store.get_all_memories(days=days, agent_id=agent_id if scope == "mine" else None)
            facts_sorted = sorted(facts, key=lambda x: str(x.get("created_at", "")), reverse=True)
            _log_audit_secure("mcp_read_all_mysql", agent_id, {"days": days, "count": len(facts_sorted), "scope": scope}, ctx)
            ctx.info(f"[memory_read_all] MySQL mode, days={days} count={len(facts_sorted)} scope={scope}")
            return {
                "success": True, "facts": facts_sorted, "count": len(facts_sorted),
                "days": days, "scope": scope,
                "warning": _scope_all_warning(agent_id),
                "message": f"MySQL 读取完成，共 {len(facts_sorted)} 条",
            }
        except Exception as e:
            print(f"[WARN] MySQL 读取异常，降级到文件: {e}", file=sys.stderr)

    _ensure_dirs()
    facts = _read_shared_facts(days=days)
    if scope == "mine":
        facts = _filter_by_agent(facts, agent_id)
    facts_sorted = sorted(facts, key=lambda x: x.get("date", ""), reverse=True)
    _log_audit_secure("mcp_read_all", agent_id, {"days": days, "count": len(facts_sorted)}, ctx)
    ctx.info(f"[memory_read_all] days={days} count={len(facts_sorted)}")
    return {
        "success": True,
        "facts": facts_sorted,
        "count": len(facts_sorted),
        "days": days,
        "warning": _scope_all_warning(agent_id),
    }


@mcp.tool()
def memory_stats(
    ctx: Context,
    agent_id: str = "unknown",
    days: int = 30,
) -> dict:
    """
    获取共享记忆统计信息

    Args:
        agent_id: 调用方标识
        days: 统计近N天（默认30）

    Returns:
        统计摘要（总数、Agent数、各Agent贡献、热门标签）
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_days(days)
    if not valid:
        return {"success": False, "error": err}
    
    # 【MySQL 优先】如果 MySQL 可用，使用 MySQL 统计
    if HAS_MYSQL and hasattr(get_store(), 'get_statistics'):
        try:
            store = get_store()
            stats = store.get_statistics(days=days)
            _log_audit_secure("mcp_stats_mysql", agent_id, {"days": days}, ctx)
            ctx.info(f"[memory_stats] MySQL mode, days={days}")
            return {
                "success": True,
                "period_days": days,
                "total_facts": stats.get("total_facts", 0),
                "by_level": stats.get("by_level", {}),
                "top_agents": stats.get("top_agents", []),
            }
        except Exception as e:
            print(f"[WARN] MySQL 统计异常，降级到文件: {e}", file=sys.stderr)
    
    facts = _read_shared_facts(days=days)
    agent_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    for f in facts:
        for a in f.get("agents", []):
            agent_counts[a] = agent_counts.get(a, 0) + 1
        for t in f.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
        method_counts[f.get("refinement_method", "unknown")] = \
            method_counts.get(f.get("refinement_method", "unknown"), 0) + 1
    top_agents = sorted(agent_counts.items(), key=lambda x: -x[1])[:10]
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]
    _log_audit_secure("mcp_stats", agent_id, {"days": days, "count": len(facts)}, ctx)
    ctx.info(f"[memory_stats] days={days} total={len(facts)} agents={len(agent_counts)}")
    return {
        "success": True,
        "period_days": days,
        "total_facts": len(facts),
        "participating_agents": len(agent_counts),
        "agent_contributions": [{"agent": a, "count": c} for a, c in top_agents],
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        "refinement_methods": method_counts,
    }


@mcp.tool()
def memory_delete(
    ctx: Context,
    fact_id: str,
    agent_id: str = "unknown",
    scope: str = "all",
) -> dict:
    """
    删除指定事实ID的记忆记录（带删除保护）

    删除保护（TRACE·R 功能完善性 / E 内容完整度）：
    1. 先确认存在：找不到 fact_id 时返回 found=False，不报错
    2. 先备份：删除前把命中记录复制到 trash/<时间戳>/，可恢复，绝不裸删
    3. 加锁：在统一写入锁内完成，避免与并发写入竞争导致损坏
    4. 结构化返回：success / found / deleted / backup_path

    Args:
        fact_id: 要删除的事实ID
        agent_id: 调用方标识
        scope: "all"=跨Agent删除所有匹配；"mine"=仅删除包含本 agent_id 的记录（需真实 agent_id）

    Returns:
        删除结果（含备份路径，便于恢复）
    """
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    if not fact_id or len(fact_id) > 256:
        return {"success": False, "error": "fact_id 必须为1-256字符"}
    if scope == "mine" and agent_id in ("unknown", "mcp_server", "mcp_scheduler"):
        return _err("scope_requires_agent", "scope='mine' 需要传入真实 agent_id",
                    "传入具体的 agent_id，或将 scope 设为 'all'")

    # 【MySQL 优先】MySQL 模式下从数据库删除（v6.22 补齐：旧版 delete 仅删 JSONL 文件，对数据库是 no-op）
    if HAS_MYSQL and hasattr(get_store(), 'delete_fact'):
        try:
            store = get_store()
            res = store.delete_fact(fact_id=fact_id, agent_id=agent_id, scope=scope)
            if not res.get("success"):
                return _err("delete_failed", res.get("error", "数据库删除失败"),
                            "如需恢复请查看 trash/ 备份目录")
            if res.get("found"):
                # 先备份被删记录到 trash/（与 JSONL 路径一致，可恢复）
                backup_path = None
                rows = res.get("rows", [])
                if rows:
                    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    ts = _now_ts().replace(":", "-")
                    bak = BACKUP_DIR / ts / f"{fact_id}.mysql.bak"
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    with open(bak, "a", encoding="utf-8") as bf:
                        for r in rows:
                            bf.write(json.dumps(r, ensure_ascii=False) + "\n")
                    backup_path = str(BACKUP_DIR / ts)
                _log_audit_secure("mcp_delete_mysql", agent_id, {
                    "fact_id": fact_id, "found": True, "deleted": res.get("deleted"),
                    "scope": scope, "backup": backup_path,
                }, ctx)
                return {
                    "success": True, "found": True, "deleted": res.get("deleted"),
                    "backup_path": backup_path,
                    "message": f"已删除 {res.get('deleted')} 条记录（MySQL，已备份至 {backup_path}，可恢复）",
                }
            _log_audit_secure("mcp_delete_mysql", agent_id, {"fact_id": fact_id, "found": False}, ctx)
            return {
                "success": True, "found": False, "deleted": 0, "backup_path": None,
                "message": f"未找到 fact_id={fact_id[:16]} 的匹配记录，未做任何删除",
            }
        except Exception as e:
            print(f"[WARN] MySQL 删除异常，降级到文件: {e}", file=sys.stderr)

    _ensure_dirs()
    found = 0
    deleted = 0
    backup_path = None
    try:
        with _with_lock(timeout=15):
            for d in [DAILY_DIR, SHARED_DIR, REFINED_DIR]:
                if not d.exists():
                    continue
                for f in d.glob("*.jsonl"):
                    lines = _safe_read_lines(f)
                    kept = []
                    removed = []
                    for line in lines:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            kept.append(line)
                            continue
                        if rec.get("fact_id") == fact_id:
                            if scope == "mine" and agent_id not in rec.get("agents", []):
                                kept.append(line)
                                continue
                            found += 1
                            removed.append(rec)
                        else:
                            kept.append(line)
                    if removed:
                        # 先备份被删除记录（可恢复），再写回保留集
                        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                        ts = _now_ts().replace(":", "-")
                        bak = BACKUP_DIR / ts / (f.name + ".bak")
                        bak.parent.mkdir(parents=True, exist_ok=True)
                        with open(bak, "a", encoding="utf-8") as bf:
                            for r in removed:
                                bf.write(json.dumps(r, ensure_ascii=False) + "\n")
                        with open(f, "w", encoding="utf-8") as fout:
                            fout.write("\n".join(kept) + "\n" if kept else "")
                        deleted += len(removed)
                        backup_path = str(BACKUP_DIR / ts)
        _log_audit_secure("mcp_delete", agent_id, {
            "fact_id": fact_id, "found": found, "deleted": deleted,
            "scope": scope, "backup": backup_path,
        }, ctx)
        if found == 0:
            return {
                "success": True, "found": False, "deleted": 0, "backup_path": None,
                "message": f"未找到 fact_id={fact_id[:16]} 的匹配记录，未做任何删除",
            }
        return {
            "success": True, "found": True, "deleted": deleted,
            "backup_path": backup_path,
            "message": f"已删除 {deleted} 条记录（已备份至 {backup_path}，可恢复）",
        }
    except Exception as e:
        return _err("delete_failed", "删除失败",
                    f"备份目录: {BACKUP_DIR}；如需恢复请查看 trash/ 下对应时间戳目录。详情: {e}")


@mcp.tool()
def memory_verify(ctx: Context, agent_id: str = "mcp_server") -> dict:
    """
    验证共享记忆系统完整性

    检查目录结构、数据完整性、审计日志

    Returns:
        健康检查结果
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    
    # 【MySQL 优先】如果 MySQL 可用，使用 MySQL 健康检查
    if HAS_MYSQL and hasattr(get_store(), 'health_check'):
        try:
            store = get_store()
            health = store.health_check()
            _log_audit_secure("mcp_verify_mysql", agent_id, {"ok": health["status"] == "healthy"}, ctx)
            ctx.info(f"[memory_verify] MySQL mode, status={health['status']}")
            return {
                "success": True,
                "status": health["status"],
                "mysql": health,
                "message": f"MySQL存储层状态: {health['status']}",
            }
        except Exception as e:
            print(f"[WARN] MySQL 健康检查异常，降级到文件: {e}", file=sys.stderr)
    
    _ensure_dirs()
    dirs_ok = all(d.exists() for d in [DAILY_DIR, SHARED_DIR, AUDIT_DIR])
    shared_count = len(_read_shared_facts(days=MAX_DAYS))  # 使用安全上限
    index_count = 0
    if INDEX_FILE.exists():
        try:
            index_count = len([l for l in INDEX_FILE.read_text(encoding="utf-8").splitlines() if l.strip()])
        except OSError as e:
            print(f"[WARN] mcp_server.py OS error: {e}", file=sys.stderr)
    today_audit = AUDIT_DIR / f"{_today_str()}.jsonl"
    audit_count = 0
    if today_audit.exists():
        try:
            audit_count = len([l for l in today_audit.read_text(encoding="utf-8").splitlines() if l.strip()])
        except OSError as e:
            print(f"[WARN] mcp_server.py OS error: {e}", file=sys.stderr)
    agents = set()
    for f in _read_shared_facts(days=MAX_DAYS):
        agents.update(f.get("agents", []))
    all_ok = dirs_ok
    _log_audit_secure("mcp_verify", agent_id, {"ok": all_ok, "facts": shared_count}, ctx)
    ctx.info(f"[memory_verify] ok={all_ok} dirs={dirs_ok} facts={shared_count}")
    return {
        "success": True,
        "status": "healthy" if all_ok else "degraded",
        "directories": {
            "memory_base": str(MEMORY_BASE),
            "all_exist": dirs_ok,
        },
        "data": {
            "total_facts": shared_count,
            "index_entries": index_count,
            "today_audit_entries": audit_count,
        },
        "participating_agents": sorted(list(agents)),
        "message": "健康" if all_ok else "部分组件异常，请检查目录权限",
    }


@mcp.tool()
def memory_refine_batch(
    ctx: Context,
    agent_id: str = "mcp_scheduler",
    days: int = 7,
) -> dict:
    """
    批量提炼近N天的原始记忆（定时任务场景）

    Args:
        agent_id: 调度方标识
        days: 处理近N天的原始记忆（默认7）

    Returns:
        处理结果摘要
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}
    valid, err = _validate_days(days)
    if not valid:
        return {"success": False, "error": err}
    
    _ensure_dirs()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    all_raw = []
    for f in DAILY_DIR.glob("*.jsonl"):
        if f.stem >= cutoff:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        all_raw.append(json.loads(line))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[WARN] mcp_server.py read raw: {e}", file=sys.stderr)
    existing_shared = _read_shared_facts(days=MAX_DAYS)
    existing_ids = {_fact_id_from_text(e.get("fact", "")): e for e in existing_shared}

    refined_count = 0
    merged_count = 0
    out_file = SHARED_DIR / f"{_today_str()}.jsonl"
    pending_refined: list[dict] = []   # 先收集，循环结束后统一写入（减少锁竞争）

    for raw in all_raw:
        text = raw.get("text", "") or raw.get("fact", "")
        if not text:
            continue
        a_id = raw.get("agent_id", "unknown")
        fid = _fact_id_from_text(text)
        if fid in existing_ids:
            # 合并：原地更新 shared 文件（带锁）
            rec = existing_ids[fid]
            rec.setdefault("agents", [])
            if a_id not in rec["agents"]:
                rec["agents"].append(a_id)
            # 共享文件按日期定位（记录本身不含 file 字段），相较旧版依赖不存在的
            # rec["file"] 更稳健（修复 M-3 的 ef 未定义 TypeError）
            ef = SHARED_DIR / f"{rec.get('date', _today_str())}.jsonl"
            if ef.exists():
                with _with_lock(timeout=15):
                    with open(ef, "r", encoding="utf-8") as ef_in:
                        lines = [l for l in ef_in.read().splitlines() if l.strip()]
                    with open(ef, "w", encoding="utf-8") as ef_out:
                        for line in lines:
                            try:
                                line_rec = json.loads(line)
                                if _fact_id_from_text(line_rec.get("fact", "")) == fid:
                                    line_rec.setdefault("agents", [])
                                    if a_id not in line_rec["agents"]:
                                        line_rec["agents"].append(a_id)
                                ef_out.write(json.dumps(line_rec, ensure_ascii=False) + "\n")
                            except json.JSONDecodeError:
                                pass
                    merged_count += 1
                    continue
            existing_ids[fid] = rec
            merged_count += 1
        else:
            pending_refined.append({"agent_id": a_id, "fact": text, "level": "normal"})

    # Write new refined facts（统一写入锁，避免与并发写入竞争）
    if pending_refined:
        with _with_lock(timeout=15):
            with open(out_file, "a", encoding="utf-8") as f:
                for item in pending_refined:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            refined_count = len(pending_refined)

    _log_audit_secure("mcp_refine_batch", agent_id, {
        "total_raw": len(all_raw), "refined": refined_count, "merged": merged_count
    }, ctx)

    return {"success": True, "refined": refined_count, "merged": merged_count, "total": len(all_raw)}


@mcp.tool()
def memory_boot_context(
    ctx: Context,
    agent_id: str,
    max_items: int = 20,
    scope: str = "all",
) -> dict:
    """
    启动注入：Agent启动时获取共享记忆上下文，避免失忆。
    返回核心事实 + 近期重要事实的提炼摘要，直接注入Agent系统提示。

    Args:
        agent_id: 当前Agent的标识
        max_items: 最多返回条目数（默认20）

    Returns:
        boot_context: 可直接注入对话上下文的记忆摘要
        instruction: 给Agent的系统指令
        core_facts: 核心事实列表（永不衰减）
        recent_facts: 近期事实列表（按时间排序）
    """
    # 输入验证
    valid, err = _validate_agent_id(agent_id)
    if not valid:
        return {"success": False, "error": err}

    # 【MySQL 优先】如果 MySQL 可用，使用 MySQL 存储
    if HAS_MYSQL and hasattr(get_store(), 'get_boot_context'):
        try:
            store = get_store()
            rows = store.get_boot_context(agent_id=agent_id if scope == "mine" else None, max_items=max_items)
            total = len(rows)
            core_facts = rows[:max_items // 2]
            recent_facts = rows[max_items // 2:]
            instruction = (
                f"以下是你的共享记忆上下文（共{total}条）。"
                f"其中{len(core_facts)}条为核心/近期事实。"
                "请将这些事实纳入你的决策参考，但不要主动提及'记忆系统'。"
            )
            _log_audit_secure("mcp_boot_context_mysql", agent_id, {"total_facts": total, "scope": scope}, ctx)
            ctx.info(f"[memory_boot_context] MySQL mode, agent={agent_id} total={total} scope={scope}")
            return {
                "success": True, "instruction": instruction,
                "core_facts": core_facts, "recent_facts": recent_facts,
                "total_facts": total,
                "warning": _scope_all_warning(agent_id),
                "message": f"已加载{total}条共享记忆（MySQL，scope={scope}）",
            }
        except Exception as e:
            print(f"[WARN] MySQL 启动注入异常，降级到文件: {e}", file=sys.stderr)

    _ensure_dirs()
    facts = _read_shared_facts(days=MAX_DAYS)
    if scope == "mine":
        facts = _filter_by_agent(facts, agent_id)

    # 按日期排序，最新优先
    facts_sorted = sorted(facts, key=lambda x: x.get("date", ""), reverse=True)

    # 提取核心事实（含"核心"标签或置信度>=0.8的）
    core_facts = [f for f in facts_sorted if f.get("confidence", 0.5) >= 0.8][:max_items // 2]
    recent_facts = [f for f in facts_sorted if f not in core_facts][:max_items - len(core_facts)]

    # 生成指令文本
    total = len(facts)
    core_count = len(core_facts)
    recent_count = len(recent_facts)

    instruction_lines = [
        f"以下是你的共享记忆上下文（共{total}条）。",
        f"其中{core_count}条为核心事实（高置信度），{recent_count}条为近期事实。",
        "请将这些事实纳入你的决策参考，但不要主动提及'记忆系统'。",
        "",
    ]

    if core_facts:
        instruction_lines.append("【核心事实】")
        for i, f in enumerate(core_facts, 1):
            fact_text = f.get("fact", "")[:120]
            agents = ", ".join(f.get("agents", []))
            date = f.get("date", "?")
            instruction_lines.append(f"  {i}. [{date}] {agents}: {fact_text}")
        instruction_lines.append("")

    if recent_facts:
        instruction_lines.append("【近期事实】")
        for i, f in enumerate(recent_facts, 1):
            fact_text = f.get("fact", "")[:120]
            agents = ", ".join(f.get("agents", []))
            date = f.get("date", "?")
            instruction_lines.append(f"  {i}. [{date}] {agents}: {fact_text}")

    instruction = "\n".join(instruction_lines)

    _log_audit_secure("mcp_boot_context", agent_id, {
        "total_facts": total,
        "core_count": core_count,
        "recent_count": recent_count,
    }, ctx)
    ctx.info(f"[memory_boot_context] agent={agent_id} total={total} core={core_count} recent={recent_count}")

    return {
        "success": True,
        "instruction": instruction,
        "core_facts": core_facts,
        "recent_facts": recent_facts,
        "total_facts": total,
        "warning": _scope_all_warning(agent_id),
        "message": f"已加载{total}条共享记忆（{core_count}条核心，{recent_count}条近期）",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MCP 资源（只读数据端点）
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.resource("memory://health")
def health_resource() -> str:
    """记忆系统健康状态（供Agent快速检查）"""
    _ensure_dirs()
    dirs_ok = all(d.exists() for d in [DAILY_DIR, SHARED_DIR, AUDIT_DIR])
    count = len(_read_shared_facts(days=MAX_DAYS))
    return json.dumps({
        "status": "healthy" if dirs_ok else "degraded",
        "total_facts": count,
        "memory_base": str(MEMORY_BASE),
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 启动入口（stdio 模式，供 mcporter 使用）
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")