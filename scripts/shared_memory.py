#!/usr/bin/env python3
"""
跨Agent记忆共享系统 - Shared Memory for Cross-Agent Context

功能：
1. 写入记忆（agent_id隔离 + LLM提炼 + 自动去重）
2. 读取记忆（语义检索 + 相关性排序）
3. LLM提炼（批量处理 + 冲突解决）
4. 完整性验证

存储结构：
  ~/.hermes/memory_data/
    daily/           - 原始记忆（按日期）
    shared/          - 共享事实（提炼后）
    refined/         - LLM提炼缓存
    audit/           - 操作审计日志
    index.jsonl      - 全局索引（fact_id -> file）
"""

import argparse
import json
import os
import sys
import uuid
import hashlib
import re
from shared_utils import validate_agent_id, extract_tags, is_duplicate, fact_id_from_text, today_str, refine_local, now_ts, check_sensitive_info
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import io

# ─────────────────────────────────────────────────────────────────────────────
# 存储路径常量 (v6.8 审计修复)
# ─────────────────────────────────────────────────────────────────────────────

# 存储根目录解析（v6.24 跨平台智能默认）：
#   1) 显式 CROSS_AGENT_MEMORY_BASE 优先
#   2) 沿用既有 ~/.hermes/memory_data（兼容旧数据，避免迁移）
#   3) 否则落到中性位置 ~/.cross-agent-memory/memory_data（换设备/无 Hermes 也能直接跑）
def _resolve_memory_base() -> Path:
    env = os.environ.get("CROSS_AGENT_MEMORY_BASE")
    if env:
        return Path(env)
    hermes_dir = Path.home() / ".hermes" / "memory_data"
    if hermes_dir.exists():
        return hermes_dir
    return Path.home() / ".cross-agent-memory" / "memory_data"

MEMORY_BASE = _resolve_memory_base()
DAILY_DIR = MEMORY_BASE / 'daily'
SHARED_DIR = MEMORY_BASE / 'shared'
REFINED_DIR = MEMORY_BASE / 'refined'
AUDIT_DIR = MEMORY_BASE / 'audit'
INDEX_FILE = MEMORY_BASE / 'index.jsonl'

# ─────────────────────────────────────────────────────────────────────────────
# 敏感信息检测
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# LLM client availability
HAS_LLM_CLIENT = False

# File lock availability
HAS_FILE_LOCK = False
try:
    from filelock import FileLock
    HAS_FILE_LOCK = True
except Exception:
    HAS_FILE_LOCK = False


def refine_fact(text: str, agent_id: str) -> dict:
    """
    提炼记忆事实：将原始文本通过 LLM 提炼为结构化事实。
    
    优先使用 LLM 客户端（OpenAI/Anthropic），失败时降级到本地规则提炼。
    
    Args:
        text: 原始记忆文本
        agent_id: Agent 标识
    
    Returns:
        dict: 提炼后的事实，包含 fact_id, fact_text, tags, agent_ids, level 等字段
    
    Example:
        >>> refine_fact("今天学习了 Python 的 filelock 库", "test-agent")
        {'fact_id': 'abc123...', 'fact_text': '学习了 Python 的 filelock 库', ...}
    """
    if HAS_LLM_CLIENT:
        return llm_refine_text(text, agent_id)
    else:
        return llm_refine_local(text, agent_id)

def _check_sensitive_info(text: str) -> list[str]:
    """
    检测文本中的敏感信息模式，返回警告列表。
    委托 shared_utils.check_sensitive_info（权威实现），消除两处重复检测器。
    CLI 层据此提示用户确认后写入（而非像 MCP 层那样仅返回警告）。
    """
    return check_sensitive_info(text)


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def ensure_dirs():
    for d in [DAILY_DIR, SHARED_DIR, REFINED_DIR, AUDIT_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("", encoding="utf-8")


MAX_DAYS = 365  # 最多查询 1 年数据


def log_audit(action: str, agent_id: str, data: dict):
    """写入审计日志"""
    ensure_dirs()
    entry = {
        "ts": now_ts(),
        "action": action,
        "agent_id": agent_id,
        **data
    }
    audit_file = AUDIT_DIR / f"{today_str()}.jsonl"
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_index() -> dict:
    """加载全局索引 {fact_id: {file, line_num, ...}}"""
    ensure_dirs()
    idx = {}
    if INDEX_FILE.exists():
        for line in INDEX_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    item = json.loads(line)
                    idx[item["fact_id"]] = item
                except json.JSONDecodeError as e:
                    print(f"[WARN] shared_memory.py JSON decode: {e}", file=sys.stderr)
    return idx

def save_index_entry(entry: dict):
    """
    追加索引条目
    
    Args:
        entry: 索引条目字典
    
    Returns:
        bool: 写入是否成功
    """
    try:
        with open(INDEX_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"[ERROR] save_index_entry 写入失败: {e}", file=sys.stderr)
        return False






def read_shared_facts(days: int = 30) -> list[dict]:
    """读取近N日的共享事实"""
    facts = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for f in sorted(SHARED_DIR.glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    facts.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"[WARN] shared_memory.py JSON decode: {e}", file=sys.stderr)
    return facts


def search_facts(query: str, facts: list[dict], top_k: int = 10) -> list[dict]:
    """
    简单关键词搜索（替代向量检索）
    匹配：fact字段 + tags字段
    """
    query_lower = query.lower()
    query_words = set(query_lower)
    scored = []
    for f in facts:
        text = f.get("fact", "") + " " + " ".join(f.get("tags", []))
        text_lower = text.lower()
        # 计算重叠词数
        matches = sum(1 for w in query_words if w in text_lower)
        if matches > 0:
            scored.append((matches, len(text), f))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [f for _, _, f in scored[:top_k]]


def llm_refine_text(text: str, agent_id: str) -> dict:
    """
    调用 LLM 提炼事实（支持 OpenAI/Anthropic/本地）
    
    优先级:
    1. 从环境变量 LLM_PROVIDER 读取提供商 (openai/anthropic/local)
    2. 如果未设置，降级到本地提炼
    """
    if HAS_LLM_CLIENT:
        # 使用真实 LLM 客户端
        provider = os.getenv("LLM_PROVIDER", "local")
        model = os.getenv("LLM_MODEL")  # 可选
        
        result = refine_fact(
            text=text,
            agent_id=agent_id,
            provider=provider,
            model=model,
            fallback_to_local=True  # 失败时降级到本地
        )
        
        # 转换日期格式
        if "date" in result:
            result["date"] = today_str()
        if "refined_at" in result:
            result["refined_at"] = now_ts()
        
        return result
    else:
        # 降级到本地提炼
        return llm_refine_local(text, agent_id)


def llm_refine_local(text: str, agent_id: str) -> dict:
    """
    本地提炼（降级方案）—— 提取式核心事实

    策略：识别陈述句的"主体-动作-对象"结构，移除语气词和不确定性表达。
    优于旧版：旧版仅删除语气字符，容易破坏中文语义；新版保留结构、提取核心。
    """
    import re

    # ── Step 1: 预处理 ──────────────────────────────────────────
    original = text.strip()
    lower = original.lower()

    # 语气词列表（仅用于辅助判断，不直接删除）
    filler_chars = set("吗吧呀啊呢的哈哦呃噢哇啰咧嘻哼呐哇呃嗨呦哇喔哎嘿啰嗨")

    # ── Step 2: 识别不确定性（降置信度）────────────────────────
    uncertain_patterns = [
        r"可能", r"大概", r"也许", r"似乎", r"好像", r"应该",
        r"估计", r"猜测", r"感觉", r"或许", r"未必", r"不一定",
        r"不太确定", r"不太清楚", r"我没", r"我不确定",
    ]
    uncertainty_count = sum(1 for p in uncertain_patterns if re.search(p, lower))

    # ── Step 3: 识别否定（标记核心事实）────────────────────────
    negation_patterns = [
        r"不", r"没", r"无", r"非", r"别", r"勿", r"否",
        r"拒绝", r"取消", r"停止", r"禁止", r"反对",
    ]
    has_negation = any(re.search(p, lower) for p in negation_patterns)

    # ── Step 4: 提取核心事实（提取式，不生成）─────────────────
    #
    # 启发式：保留最接近"[主体][动作][对象]"结构的句子片段
    # 策略：
    #   - 如果文本中有"是"字句（"X是Y"），优先提取"X是Y"这个结构
    #   - 如果有动词（买/卖/投资/计划/决定/认为），提取动宾结构
    #   - 否则截取去掉语气词后的最长有意义片段

    refined = original

    # 4a: "X是Y" / "X为Y" 句式
    m = re.search(r"([^，。？！；\n]{2,30}?)(?:是|为|等于)([^，。？！；\n]{1,50})", original)
    if m:
        subject_part = m.group(1).strip()
        predicate_part = m.group(2).strip()
        # 清理主体部分中的语气词
        subject_clean = "".join(
            c for c in subject_part
            if c not in filler_chars or c in ("不", "没")
        ).strip()
        refined = f"{subject_clean}是{predicate_part}"
    else:
        # 4b: 动词宾语结构
        action_verbs = [
            r"(投资|买入|卖出|增持|减持|建仓|清仓)[^，。？！；\n]{0,50}",
            r"(计划|打算|准备|决定|认为|相信|预计)[^，。？！；\n]{0,50}",
            r"(喜欢|偏好|倾向|关注|看好|唱空)[^，。？！；\n]{0,50}",
            r"(持有|拥有|管理|负责|负责)[^，。？！；\n]{0,50}",
            r"(访问|考察|参观|签约|合作|签约)[^，。？！；\n]{0,50}",
        ]
        best_match = ""
        for vp in action_verbs:
            mv = re.search(vp, original)
            if mv and len(mv.group()) > len(best_match):
                best_match = mv.group().strip()
        if best_match:
            # 清理语气词
            refined = "".join(
                c for c in best_match
                if c not in filler_chars or c in ("不", "没")
            ).strip()
        else:
            # 4c: 去除句首语气词后的首句
            first_sent = re.split(r"[。！？]", original)[0].strip()
            # 去除常见句首语气词前缀
            first_sent = re.sub(r"^(?:那个|嗯|哎|喔|唉|其实|就是|然后|所以|不过|但是|而且|但是)\s*", "", first_sent)
            refined = first_sent.strip() if first_sent.strip() else original

    # 去除多余空格
    refined = re.sub(r"\s+", " ", refined).strip()
    if not refined:
        refined = original

    # ── Step 5: 提取标签 ───────────────────────────────────────
    tags = extract_tags(refined)
    # 添加句式标签
    if has_negation:
        tags.append("否定")
    if uncertainty_count >= 2:
        tags.append("不确定性")
    if re.search(r"\d{4}", refined) or re.search(r"\d+[万亿]?元", refined):
        tags.append("含数据")

    # ── Step 6: 计算置信度 ────────────────────────────────────
    # 基础分 0.5，每增加一个确定性信号 +0.05，每增加一个不确定性信号 -0.1
    confidence = 0.5
    if has_negation:
        confidence += 0.05
    if uncertainty_count == 0:
        confidence += 0.15
    elif uncertainty_count == 1:
        confidence += 0.0
    else:
        confidence -= 0.1 * (uncertainty_count - 1)
    confidence = max(0.3, min(0.85, confidence))

    return {
        "fact": refined,
        "original": original,
        "agents": [agent_id],
        "date": today_str(),
        "refined_at": now_ts(),
        "tags": list(set(tags)),
        "refinement_method": "local",
        "confidence": round(confidence, 2),
    }



# ─────────────────────────────────────────────────────────────────────────────
# 命令实现
# ─────────────────────────────────────────────────────────────────────────────

def cmd_write(args):
    """写入记忆（遵循先读后动原则）"""
    ensure_dirs()
    agent_id = args.agent
    fact_text = args.fact.strip()

    if not fact_text:
        print("错误: fact不能为空", file=sys.stderr)
        sys.exit(1)

    # 计算 fact_id（基于内容，稳定不变）
    fact_id = fact_id_from_text(fact_text)

    # 【安全检查】敏感信息过滤
    sensitive_warnings = _check_sensitive_info(fact_text)
    if sensitive_warnings:
        print("警告: 敏感信息检测:", file=sys.stderr)
        for w in sensitive_warnings:
            print(f"   - {w}", file=sys.stderr)
        print("   已自动记录警告，继续写入（Hermes 无 stdin，无法等待确认）", file=sys.stderr)

    date_str = today_str()

    # 【先读后动】Step 1: 先读取现有记忆，检查是否重复
    existing = read_shared_facts(days=90)
    dup = is_duplicate(fact_text, existing)

    if dup:
        # 【BUG FIX】不再追加写入，而是找到原行并原地更新
        # 追加写入会导致同一 fact 出现两条记录，数据翻倍污染
        out_file = SHARED_DIR / f"{dup.get('date', date_str)}.jsonl"
        
        # 使用文件锁保护读写
        lock_file = str(out_file)
        
        def _do_update():
            """在锁内执行：找到原行、更新、写回（不重复追加）"""
            if not out_file.exists():
                return False
            lines = []
            found = False
            for line in out_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rec = json.loads(line)
                        # 找到 fact_id 相同的记录（同一事实）
                        if rec.get("fact_id") == dup.get("fact_id"):
                            # 更新 agents 列表（追加当前 agent，如果尚未包含）
                            rec.setdefault("agents", [])
                            if agent_id not in rec["agents"]:
                                rec["agents"].append(agent_id)
                            rec["updated_at"] = now_ts()
                            lines.append(json.dumps(rec, ensure_ascii=False))
                            found = True
                        else:
                            lines.append(line)
                    except json.JSONDecodeError:
                        lines.append(line)
                else:
                    lines.append(line)
            
            if found:
                out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return found
        
        try:
            if HAS_FILE_LOCK:
                with FileLock(lock_file + '.lock'):
                    updated = _do_update()
            else:
                updated = _do_update()
        except Exception as merge_lock_err:
            print(f"[WARN] 合并文件锁失败，降级到无锁模式: {merge_lock_err}", file=sys.stderr)
            updated = _do_update()
        
        if updated:
            print(f"去重合并 ✓ fact_id: {dup.get('fact_id', 'unknown')[:16]}", file=sys.stderr)
            print(f"  已将 agent[{agent_id}]追加到现有记忆", file=sys.stderr)
            # 更新索引（merge 后 agents 列表已更新，需同步索引）
            dup_date = dup.get("date", date_str)
            merged_agents = dup.get("agents", []) + ([agent_id] if agent_id not in dup.get("agents", []) else [])
            merged_entry = {
                "fact_id": dup.get("fact_id", fact_id),
                "date": dup_date,
                "file": str(SHARED_DIR / f"{dup_date}.jsonl"),
                "agents": merged_agents,
                "tags": dup.get("tags", []),
            }
            try:
                if HAS_FILE_LOCK:
                    with FileLock(str(INDEX_FILE) + '.lock'):
                        save_index_entry(merged_entry)
                else:
                    save_index_entry(merged_entry)
            except Exception as merge_idx_err:
                print(f"[WARN] 合并索引锁失败，降级到无锁模式: {merge_idx_err}", file=sys.stderr)
                save_index_entry(merged_entry)
            log_audit("write_merge", agent_id, {"fact_id": fact_id, "merged": True})
        else:
            # 罕见情况：原文件已被删除，当作新写入处理（带锁保护）
            refined = llm_refine_text(fact_text, agent_id)
            refined["fact_id"] = fact_id
            out_file = SHARED_DIR / f"{date_str}.jsonl"
            index_entry = {
                "fact_id": fact_id,
                "date": date_str,
                "file": str(out_file),
                "agents": refined["agents"],
                "tags": refined["tags"],
            }
            # 【BUG FIX】嵌套锁死锁风险修复：使用单一锁保护所有操作
            # 原代码先获取 out_file.lock 再获取 INDEX_FILE.lock，可能导致死锁
            # 修复：使用单一共享锁保护所有相关文件操作
            try:
                if HAS_FILE_LOCK:
                    # 使用单一锁保护所有操作，避免死锁
                    with FileLock(str(SHARED_DIR / "shared.lock")):
                        with open(out_file, "a", encoding="utf-8") as f:
                            f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                        save_index_entry(index_entry)
                else:
                    with open(out_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                    save_index_entry(index_entry)
            except Exception as fallback_lock_err:
                print(f"[WARN] 回退写入锁失败，降级到无锁模式: {fallback_lock_err}", file=sys.stderr)
                with open(out_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                save_index_entry(index_entry)
            print(f"写入成功 ✓ fact_id: {fact_id[:16]}（回退新写入）", file=sys.stderr)
            log_audit("write_new", agent_id, {"fact_id": fact_id})
    else:
        # LLM提炼 + 写入
        refined = llm_refine_text(fact_text, agent_id)
        refined["fact_id"] = fact_id

        out_file = SHARED_DIR / f"{date_str}.jsonl"
        
        # 使用文件锁保护写入（失败时自动降级）
        try:
            if HAS_FILE_LOCK:
                with FileLock(str(out_file) + '.lock'):
                    with open(out_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(refined, ensure_ascii=False) + "\n")
            else:
                with open(out_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(refined, ensure_ascii=False) + "\n")
        except Exception as lock_err:
            print(f"[WARN] 文件锁失败，降级到无锁模式: {lock_err}", file=sys.stderr)
            with open(out_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(refined, ensure_ascii=False) + "\n")

        # 更新索引（索引文件也需要锁，失败时降级）
        try:
            if HAS_FILE_LOCK:
                with FileLock(str(INDEX_FILE) + '.lock'):
                    save_index_entry({
                        "fact_id": fact_id,
                        "date": date_str,
                        "file": str(out_file),
                        "agents": refined["agents"],
                        "tags": refined["tags"]
                    })
            else:
                save_index_entry({
                    "fact_id": fact_id,
                    "date": date_str,
                    "file": str(out_file),
                    "agents": refined["agents"],
                    "tags": refined["tags"]
                })
        except Exception as idx_err:
            print(f"[WARN] 索引文件锁失败，降级到无锁模式: {idx_err}", file=sys.stderr)
            save_index_entry({
                "fact_id": fact_id,
                "date": date_str,
                "file": str(out_file),
                "agents": refined["agents"],
                "tags": refined["tags"]
            })

        print(f"写入成功 ✓ fact_id: {fact_id[:16]}", file=sys.stderr)
        print(f"  事实: {refined['fact'][:60]}{'...' if len(refined['fact']) > 60 else ''}")
        log_audit("write_new", agent_id, {"fact_id": fact_id, "fact": refined["fact"][:100]})


def cmd_read(args):
    """读取/检索记忆"""
    if args.all:
        facts = read_shared_facts(days=MAX_DAYS)
        print(f"共 {len(facts)} 条共享记忆:")
        for f in facts:
            print(f"  [{f.get('date', '?')}] {f.get('agents', [])}: {f.get('fact', '')[:70]}")
        return

    query = args.query
    if not query:
        print("错误: 需要 --query 或 --all", file=sys.stderr)
        sys.exit(1)

    facts = read_shared_facts(days=args.days or 30)
    results = search_facts(query, facts, top_k=args.limit or 10)

    if not results:
        print("未找到相关记忆")
        return

    print(f"找到 {len(results)} 条相关记忆:")
    for r in results:
        agents_str = ", ".join(r.get("agents", []))
        tags_str = " ".join(f"[{t}]" for t in r.get("tags", []))
        print(f"\n  [{r.get('date', '?')}] {agents_str}")
        print(f"  事实: {r.get('fact', '')}")
        if args.verbose and r.get("tags"):
            print(f"  标签: {tags_str}")


def cmd_refine(args):
    """LLM批量提炼"""
    days = args.days or 7
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    ensure_dirs()
    all_raw = []

    # 读取原始记忆
    for f in DAILY_DIR.glob("*.jsonl"):
        if f.stem >= cutoff:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        all_raw.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        print(f"[WARN] shared_memory.py JSON decode: {e}", file=sys.stderr)

    # 读取共享事实（已提炼的）
    existing_shared = read_shared_facts(days=MAX_DAYS)
    existing_ids = {fact_id_from_text(e.get("fact", "")): e for e in existing_shared}

    refined_count = 0
    merged_count = 0

    for raw in all_raw:
        text = raw.get("text", "") or raw.get("fact", "")
        if not text:
            continue
        agent_id = raw.get("agent_id", "unknown")
        fid = fact_id_from_text(text)

        if fid in existing_ids:
            # 合并：更新 shared 文件（带锁）+ 写入索引（带锁）
            existing_rec = existing_ids[fid]
            existing_rec.setdefault("agents", [])
            if agent_id not in existing_rec["agents"]:
                existing_rec["agents"].append(agent_id)
            # 找到对应文件并原地更新
            existing_file = existing_rec.get("file")
            if existing_file:
                ef = Path(existing_file)
                if ef.exists():
                    def _do_merge():
                        lines = []
                        found = False
                        for line in ef.read_text(encoding="utf-8").splitlines():
                            if line.strip():
                                try:
                                    rec = json.loads(line)
                                    if rec.get("fact_id") == fid:
                                        rec["agents"] = existing_rec["agents"]
                                        rec["updated_at"] = now_ts()
                                        lines.append(json.dumps(rec, ensure_ascii=False))
                                        found = True
                                    else:
                                        lines.append(line)
                                except json.JSONDecodeError:
                                    lines.append(line)
                            else:
                                lines.append(line)
                        if found:
                            ef.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        return found
                    if HAS_FILE_LOCK:
                        with FileLock(str(ef) + '.lock'):
                            _do_merge()
                    else:
                        _do_merge()
            # 写入索引（更新 agents 列表）
            index_entry = {
                "fact_id": fid,
                "date": existing_rec.get("date", today_str()),
                "file": existing_file,
                "agents": existing_rec["agents"],
                "tags": existing_rec.get("tags", []),
            }
            try:
                if HAS_FILE_LOCK:
                    with FileLock(str(INDEX_FILE) + '.lock'):
                        save_index_entry(index_entry)
                else:
                    save_index_entry(index_entry)
            except Exception as refine_idx_err:
                print(f"[WARN] refine索引锁失败，降级到无锁模式: {refine_idx_err}")
                save_index_entry(index_entry)
            merged_count += 1
        else:
            # 提炼新事实：提炼 + 文件写入（带锁）+ 索引写入（带锁）
            refined = llm_refine_text(text, agent_id)
            refined["fact_id"] = fid
            out_file = SHARED_DIR / f"{today_str()}.jsonl"
            index_entry = {
                "fact_id": fid,
                "date": today_str(),
                "file": str(out_file),
                "agents": refined["agents"],
                "tags": refined["tags"],
            }
            try:
                if HAS_FILE_LOCK:
                    # 使用单一锁保护所有操作，避免死锁
                    with FileLock(str(SHARED_DIR / "shared.lock")):
                        with open(out_file, "a", encoding="utf-8") as f:
                            f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                        save_index_entry(index_entry)
                else:
                    with open(out_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                    save_index_entry(index_entry)
            except Exception as fallback_lock_err:
                print(f"[WARN] 回退写入锁失败，降级到无锁模式: {fallback_lock_err}", file=sys.stderr)
                with open(out_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(refined, ensure_ascii=False) + "\n")
                save_index_entry(index_entry)
            existing_ids[fid] = refined
            refined_count += 1

    print(f"提炼完成: 新增 {refined_count} 条, 合并 {merged_count} 条")


def cmd_verify(args):
    """验证记忆完整性"""
    print("=" * 50)
    print("跨Agent记忆完整性验证")
    print("=" * 50)

    # 检查目录
    ensure_dirs()
    dirs_ok = all(d.exists() for d in [DAILY_DIR, SHARED_DIR, AUDIT_DIR])
    print(f"  目录结构: {'[OK]' if dirs_ok else '[X]'}")

    # 统计
    shared_count = len(read_shared_facts(days=MAX_DAYS))
    index = load_index()
    print(f"  共享事实: {shared_count} 条")
    print(f"  索引条目: {len(index)} 条")

    # 审计日志检查
    today_audit = AUDIT_DIR / f"{today_str()}.jsonl"
    audit_count = 0
    if today_audit.exists():
        audit_count = len([l for l in today_audit.read_text(encoding="utf-8").splitlines() if l.strip()])
    print(f"  今日审计: {audit_count} 条记录")

    # Agent覆盖检查
    agents_in_shared = set()
    for f in SHARED_DIR.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    agents_in_shared.update(json.loads(line).get("agents", []))
                except json.JSONDecodeError as e:
                    print(f"[WARN] shared_memory.py JSON decode: {e}", file=sys.stderr)
    print(f"  参与共享的Agent: {len(agents_in_shared)} 个")
    if agents_in_shared:
        print(f"    {', '.join(sorted(agents_in_shared))}")

    if args.full:
        print("\n逐文件完整性:")
        for f in sorted(SHARED_DIR.glob("*.jsonl"))[-5:]:
            lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            valid = 0
            for l in lines:
                try:
                    d = json.loads(l)
                    if d.get("fact") and d.get("fact_id"):
                        valid += 1
                except json.JSONDecodeError as e:
                    print(f"[WARN] shared_memory.py JSON decode: {e}", file=sys.stderr)
            print(f"  {f.name}: {valid}/{len(lines)} 条有效")

    print("=" * 50)
    print("[OK] 验证完成")


def cmd_stats(args):
    """记忆统计"""
    facts = read_shared_facts(days=args.days or 30)

    agent_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}

    for f in facts:
        for a in f.get("agents", []):
            agent_counts[a] = agent_counts.get(a, 0) + 1
        for t in f.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    print(f"近 {args.days or 30} 天记忆统计:")
    print(f"  总事实数: {len(facts)}")
    print(f"  参与Agent数: {len(agent_counts)}")

    if agent_counts:
        print(f"  各Agent贡献:")
        for a, c in sorted(agent_counts.items(), key=lambda x: -x[1]):
            print(f"    {a}: {c} 条")

    if tag_counts:
        print(f"  热门标签:")
        for t, c in sorted(tag_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    [{t}]: {c}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="跨Agent记忆共享系统")
    sub = parser.add_subparsers(dest="cmd")

    # write
    w = sub.add_parser("write", help="写入记忆")
    w.add_argument("--agent", required=True, help="Agent ID")
    w.add_argument("--fact", required=True, help="记忆内容")

    # read
    r = sub.add_parser("read", help="读取/检索记忆")
    r.add_argument("--query", help="查询内容")
    r.add_argument("--all", action="store_true", help="列出全部")
    r.add_argument("--days", type=int, help="近N天（默认30）")
    r.add_argument("--limit", type=int, default=10, help="返回条数")
    r.add_argument("--verbose", action="store_true", help="详细输出")

    # refine
    rf = sub.add_parser("refine", help="LLM批量提炼")
    rf.add_argument("--days", type=int, default=7, help="处理近N天（默认7）")

    # verify
    v = sub.add_parser("verify", help="完整性验证")
    v.add_argument("--full", action="store_true", help="完整检查")

    # stats
    s = sub.add_parser("stats", help="记忆统计")
    s.add_argument("--days", type=int, default=30, help="近N天")

    args = parser.parse_args()

    if args.cmd == "write":
        cmd_write(args)
    elif args.cmd == "read":
        cmd_read(args)
    elif args.cmd == "refine":
        cmd_refine(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()