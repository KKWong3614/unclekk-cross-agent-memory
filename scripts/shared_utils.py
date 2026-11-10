#!/usr/bin/env python3
"""
共享工具函数库 - 消除代码重复

用达尔文进化论框架优化：
- 变异：识别重复的代码
- 自然选择：选择最优的实现
- 遗传：将最优实现提取为共享模块
- 适应：确保降级机制仍然有效
"""

import hashlib
import json
import os
from datetime import datetime
import re
import sys
import io
from pathlib import Path
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# 共享记忆系统常量（所有模块统一使用）
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
DAILY_DIR = MEMORY_BASE / "daily"
SHARED_DIR = MEMORY_BASE / "shared"
REFINED_DIR = MEMORY_BASE / "refined"
AUDIT_DIR = MEMORY_BASE / "audit"
INDEX_FILE = MEMORY_BASE / "index.jsonl"

# 修复 Windows 编码（防止重复包装导致 buffer 关闭）
if not getattr(sys.stdout, '_cam_utf8', False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdout._cam_utf8 = True
if not getattr(sys.stderr, '_cam_utf8', False):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stderr._cam_utf8 = True


# ═══════════════════════════════════════════════════════════════════════════════
# 敏感信息检测
# ═══════════════════════════════════════════════════════════════════════════════

def check_sensitive_info(text: str) -> list[str]:
    """
    检测文本中的敏感信息模式，返回警告列表。
    
    检测模式：
    - API Key (OpenAI, Anthropic, AWS)
    - 私钥、证书
    - 密码、Token
    
    Returns:
        警告列表，如果没有检测到敏感信息则返回空列表
    """
    warnings = []
    upper = text.upper()
    
    # API Key 模式
    if re.search(r"OPENAI[A-Z_]*KEY", upper):
        warnings.append("可能包含 OpenAI API Key 模式")
    if re.search(r"ANTHROPIC[A-Z_]*KEY", upper):
        warnings.append("可能包含 Anthropic API Key 模式")
    if re.search(r"sk-[A-Za-z0-9_-]{20,}", text):
        warnings.append("可能包含 OpenAI 风格 API Key (sk-...)")
    if re.search(r"sk-ant-[A-Za-z0-9_-]{40,}", text):
        warnings.append("可能包含 Anthropic API Key (sk-ant-...)")
    
    # AWS 凭证
    if re.search(r"AKIA[A-Z0-9]{16}", text):
        warnings.append("可能包含 AWS Access Key")
    
    # 私钥 / 证书
    if re.search(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", text):
        warnings.append("可能包含私钥")
    if re.search(r"-----BEGIN CERTIFICATE-----", text):
        warnings.append("可能包含证书")
    
    # 密码/Token 常见模式
    if re.search(r"password\s*[=:　]\s*['\"]?[A-Za-z0-9_@#$%^&*!~]{4,}", text, re.IGNORECASE):
        warnings.append("可能包含明文密码")
    if re.search(r"token\s*[=:　]\s*['\"]?[A-Za-z0-9_.-]{16,}", text, re.IGNORECASE):
        warnings.append("可能包含 Token")
    if re.search(r"(Bearer |bearer )[A-Za-z0-9_.-]{10,}", text):
        warnings.append("可能包含 Bearer Token")
    
    return warnings


# ═══════════════════════════════════════════════════════════════════════════════
# 事实ID生成
# ═══════════════════════════════════════════════════════════════════════════════

def fact_id_from_text(text: str) -> str:
    """
    基于内容hash生成稳定ID
    
    同一内容永远相同ID，用于去重和关联
    
    Args:
        text: 事实文本
    
    Returns:
        16位十六进制hash字符串
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# N-gram 去重
# ═══════════════════════════════════════════════════════════════════════════════

def to_ngrams(text: str, n: int = 2) -> set:
    """
    生成 n-gram 字符集（中文友好）
    
    例如："今天买股票" -> {"今天","天买","买股","股票"}
    
    Args:
        text: 输入文本
        n: n-gram 大小，默认为 2（bigram）
    
    Returns:
        n-gram 集合
    """
    chars = list(text)
    return set("".join(chars[i:i+n]) for i in range(len(chars) - n + 1))


# 数值 token 匹配（P1-1 数值豁免用）：
# - 阿拉伯数字（含小数与百分号）：80 / 3.14 / 50%
# - 中文数字序列：八十 / 五十 / 三点五 / 两千（覆盖 百分之八十 中的数字段）
_NUMERIC_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?%?"
    r"|[零一二两三四五六七八九十百千万亿]+(?:点[零一二三四五六七八九]+)?"
)


def _numeric_tokens(text: str) -> set:
    """提取文本中的数值 token 集合（阿拉伯 + 中文数字），用于去重的数值豁免判定。

    两侧数值 token 不一致 → 语义不同的事实，禁止模糊合并（防止数据丢失）。
    """
    if not text:
        return set()
    return set(_NUMERIC_TOKEN_RE.findall(text))


# 专名 token：连续拉丁字母词（项目代号/产品名等，如 Alpha/Beta），不同即视为不同事实
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")

# 反义/极性冲突对：两侧出现同对的不同成员 → 语义相反，绝不合并（P1-1' 第四轮审计）
_ANTONYM_PAIRS = [
    ("完成", "取消"), ("完成", "放弃"), ("启动", "取消"), ("启动", "终止"), ("启动", "暂停"),
    ("上线", "下线"), ("发布", "撤回"), ("买入", "卖出"), ("增加", "减少"), ("提升", "下降"),
    ("开始", "停止"), ("开始", "暂停"), ("同意", "拒绝"), ("批准", "驳回"), ("成功", "失败"),
    ("通过", "驳回"), ("开启", "关闭"), ("启用", "停用"), ("招聘", "裁员"), ("续约", "解约"),
]
# 否定标记：单侧出现否定标记而另一侧无 → 语义可能相反
_NEGATION_MARKERS = ("不", "没", "未", "别", "无", "取消", "撤销", "拒绝", "禁止", "暂停")


def _polarity_conflict(new_fact: str, ef_text: str) -> bool:
    """判断两条事实是否存在专名或极性冲突（P1-1'：无数值通道的语义区分）。

    冲突判定（任一命中即冲突，禁止模糊合并）：
    1. 专名冲突：两侧拉丁词集合不同（如 Alpha vs Beta 项目）
    2. 反义冲突：同一对反义词分别出现在两侧（如 完成 vs 取消）
    3. 否定冲突：一侧含否定标记而另一侧不含，且两侧其余部分高度相似
       （由调用方阈值保证相似性，此处只需标记差异）
    """
    # 1) 专名冲突
    if set(_LATIN_WORD_RE.findall(new_fact or "")) != set(_LATIN_WORD_RE.findall(ef_text or "")):
        return True
    # 2) 反义冲突
    for a, b in _ANTONYM_PAIRS:
        if (a in new_fact and b in ef_text) or (b in new_fact and a in ef_text):
            return True
    # 3) 否定冲突（仅当恰好一侧含否定标记）
    new_neg = any(m in new_fact for m in _NEGATION_MARKERS)
    ef_neg = any(m in ef_text for m in _NEGATION_MARKERS)
    if new_neg != ef_neg:
        return True
    return False


def is_duplicate(new_fact: str, existing_facts: list[dict], threshold: float = 0.85) -> Optional[dict]:
    """
    中文友好的相似度去重（精确命中先行 + 数值豁免 + 极性冲突检查 + 2-gram Jaccard）

    改进：
    - 从单字符改为 2-gram（bigram），"今天买股票" -> {"今天","天买","买股","股票"}
    - 中文词语被多个相邻字符共享，相似内容自然得到高 Jaccard 分数

    P1-1 修复（第三轮审计·数据完整性）：
    - 精确去重先行：归一化空白后完全一致直接判重，无歧义
    - 数值豁免：两侧数值 token（阿拉伯数字 + 中文数字，含小数/百分号）不一致时
      **绝不模糊合并**——「已完成百分之八十的进度」与「已完成百分之五十的进度」
      模糊相似度实测 0.778 > 0.6，旧逻辑会静默合并导致语义不同、仅数值不同的
      记忆不可检索（数据丢失）。数值不同即视为不同事实。

    P1-1' 修复（第四轮审计·无数值通道仍丢数据）：
    - 模糊阈值 0.6 → **0.85**（默认参数）：中文共享长前缀时 2-gram Jaccard 天然偏高
      （实测 0.625-0.714 的语义不同文本会被 0.6 阈值误合并），0.85 只保留"几乎逐字相同"
      的模糊合并，其余各自落库——宁可多存一条，不可丢一条记忆。
    - **极性冲突检查**（_polarity_conflict，任一命中即不合并）：
      ① 专名冲突（拉丁词集合不同，如 Alpha vs Beta 项目）
      ② 反义冲突（同对反义词分列两侧，如 完成 vs 取消）
      ③ 否定冲突（恰好一侧含否定标记）
    - 模糊去重是"锦上添花"：查重主体由精确 hash（fact_id）承担，MySQL 路径用
      精确 SHA256 完全不受影响；双后端对语义不同文本的行为一致（都保留）。

    Args:
        new_fact: 新事实文本
        existing_facts: 已有事实列表
        threshold: 模糊相似度阈值，默认 0.85（仅极高相似才合并）

    Returns:
        如果找到重复，返回重复的事实字典；否则返回 None
    """
    new_ngrams = to_ngrams(new_fact, n=2)
    if not new_ngrams:
        return None

    # 精确去重先行：归一化空白后完全一致
    new_norm = "".join((new_fact or "").split())
    # 数值 token：阿拉伯数字（含小数/百分号）+ 中文数字序列
    new_nums = _numeric_tokens(new_fact)

    for ef in existing_facts:
        ef_text = ef.get("fact", "")
        if not ef_text:
            continue
        # ① 精确命中：直接判重
        if "".join(ef_text.split()) == new_norm:
            return ef
        # ② 数值豁免：两侧数值 token 不一致 → 语义不同，绝不模糊合并
        if new_nums != _numeric_tokens(ef_text):
            continue
        # ③ 极性冲突检查：专名/反义/否定冲突 → 绝不模糊合并（P1-1'）
        if _polarity_conflict(new_fact, ef_text):
            continue
        ef_ngrams = to_ngrams(ef_text, n=2)
        if not ef_ngrams:
            continue
        # Jaccard 相似度（默认阈值 0.85：仅几乎逐字相同才合并）
        intersection = len(new_ngrams & ef_ngrams)
        union = len(new_ngrams | ef_ngrams)
        if union == 0:
            continue
        sim = intersection / union
        if sim > threshold:
            return ef
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 标签提取
# ═══════════════════════════════════════════════════════════════════════════════

def extract_tags(text: str) -> list[str]:
    """
    提取关键词标签（简化版）
    
    基于预定义关键词列表，提取文本中包含的标签
    
    Args:
        text: 输入文本
    
    Returns:
        标签列表
    """
    keywords = [
        "投资", "股票", "基金", "市场", "分析", "决策",
        "项目", "代码", "开发", "设计", "任务", "进度",
        "会议", "日程", "文档", "报告", "计划", "目标",
        "客户", "需求", "反馈", "问题", "bug", "修复",
        "升级", "配置", "部署", "测试", "发布", "版本",
        "记忆", "技能", "Agent", "系统", "性能", "优化",
    ]
    return [k for k in keywords if k in text]


# ═══════════════════════════════════════════════════════════════════════════════
# 本地提炼（降级方案）
# ═══════════════════════════════════════════════════════════════════════════════

def refine_local(text: str, agent_id: str) -> dict:
    """
    本地提炼（降级方案）—— 提取式核心事实
    
    策略：识别陈述句的核心结构（X是Y / 动宾结构），移除语气词和不确定性表达。
    
    优于旧版：旧版仅删除语气字符，容易破坏中文语义；新版保留结构、提取核心。
    
    Args:
        text: 原始文本
        agent_id: Agent标识
    
    Returns:
        提炼后的事实字典，包含 fact, original, agents, date, refined_at, tags, confidence
    """
    original = text.strip()
    lower = original.lower()
    
    # 语气词列表（仅用于辅助判断，不直接删除）
    filler_chars = set("吗吧呀啊呢的哈哦呃噢哇啰咧嘻哼呐哇呃嗨呦哇喔哎嘿啰嗨")
    
    # 不确定性模式
    uncertain_patterns = [
        r"可能", r"大概", r"也许", r"似乎", r"好像", r"应该",
        r"估计", r"猜测", r"感觉", r"或许", r"未必", r"不一定",
        r"不太确定", r"不太清楚", r"我没", r"我不确定",
    ]
    uncertainty_count = sum(1 for p in uncertain_patterns if re.search(p, lower))
    
    # 否定模式
    negation_patterns = [
        r"不", r"没", r"无", r"非", r"别", r"勿", r"否",
        r"拒绝", r"取消", r"停止", r"禁止", r"反对",
    ]
    has_negation = any(re.search(p, lower) for p in negation_patterns)
    
    # 提取核心事实（提取式，不生成）
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
        # P1-6 修复（第五轮审计·数据完整性）：动词必须锚定在句首或标点/空白之后。
        # 旧版 re.search 全文匹配，「计划/准备/决定」等兼类名词在句中作宾语时也被当作动词命中，
        # 导致默认写入路径（skip_refine=False）把长句从中间截断丢前半句——实测
        # 「我们制定了年度营销计划并提交董事会审批」→「计划并提交董事会审批」（保留 53%）、
        # 「财务部完成了季度预算计划表」→「计划表」（23%）、「老板批准了明年的招聘计划」→「计划」（17%）。
        # 原则：宁可多存（保留原文），绝不截断。锚定不到动词就落入 4c 保留原句。
        _verb_anchor = r"(?:^|[，。；、：！？\s])"
        action_verbs = [
            r"((?:投资|买入|卖出|增持|减持|建仓|清仓)[^，。？！；\n]{0,50})",
            r"((?:计划|打算|准备|决定|认为|相信|预计)[^，。？！；\n]{0,50})",
            r"((?:喜欢|偏好|倾向|关注|看好|唱空)[^，。？！；\n]{0,50})",
            r"((?:持有|拥有|管理|负责)[^，。？！；\n]{0,50})",
            r"((?:访问|考察|参观|签约|合作)[^，。？！；\n]{0,50})",
        ]
        best_match = ""
        for vp in action_verbs:
            mv = re.search(_verb_anchor + vp, original)
            if mv and len(mv.group(1)) > len(best_match):
                best_match = mv.group(1).strip()
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
    
    # 提取标签
    tags = extract_tags(refined)
    # 添加句式标签
    if has_negation:
        tags.append("否定")
    if uncertainty_count >= 2:
        tags.append("不确定性")
    if re.search(r"\d{4}", refined) or re.search(r"\d+[万亿]?元", refined):
        tags.append("含数据")
    
    # 计算置信度
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
    
    # 生成时间戳
    now = datetime.now()
    
    return {
        "fact": refined,
        "original": original,
        "agents": [agent_id],
        "date": now.strftime("%Y-%m-%d"),
        "refined_at": now.isoformat(),
        "tags": list(set(tags)),
        "refinement_method": "local",
        "confidence": round(confidence, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 时间工具
# ═══════════════════════════════════════════════════════════════════════════════

def today_str() -> str:
    """返回今天的日期字符串 YYYY-MM-DD"""
    return datetime.now().strftime("%Y-%m-%d")


def now_ts() -> str:
    """返回当前时间戳 ISO 格式"""
    return datetime.now().isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# 记忆级别配置
# ═══════════════════════════════════════════════════════════════════════════════

# 记忆分级配置
MEMORY_LEVELS = {
    "core":     {"half_life": None,  "base_weight": 1.0,  "boot_priority": 100, "label": "核心"},
    "important":{"half_life": 30,    "base_weight": 0.8,  "boot_priority": 80,  "label": "重要"},
    "normal":   {"half_life": 7,     "base_weight": 0.5,  "boot_priority": 50,  "label": "普通"},
    "temp":     {"half_life": 1,     "base_weight": 0.2,  "boot_priority": 20,  "label": "临时"},
}


def calculate_weight(level: str, date_str: str) -> float:
    """
    计算记忆的衰减权重。
    公式: weight = base_weight * (0.5 ^ (days_old / half_life))
    
    Args:
        level: 记忆级别 (core/important/normal/temp)
        date_str: 记忆日期 (YYYY-MM-DD)
    
    Returns:
        衰减后的权重值
    """
    level_cfg = MEMORY_LEVELS.get(level, MEMORY_LEVELS["normal"])
    base_weight = level_cfg["base_weight"]
    half_life = level_cfg["half_life"]
    
    # core 级别永不衰减
    if half_life is None:
        return base_weight
    
    # 计算记忆年龄（天）
    try:
        mem_date = datetime.strptime(date_str, "%Y-%m-%d")
        days_old = (datetime.now() - mem_date).days
    except (ValueError, TypeError):
        # 日期无效，按半天处理
        days_old = 0.5
    
    # 负数天数视为0
    days_old = max(0, days_old)
    
    # 衰减计算
    weight = base_weight * (0.5 ** (days_old / half_life))
    return round(weight, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# 验证函数
# ═══════════════════════════════════════════════════════════════════════════════

# 合法 agent_id 正则
AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 参数上限
MAX_DAYS = 365  # 最多查询 1 年数据
MAX_LIMIT = 100  # 最多返回 100 条
MAX_FACT_LEN = 10000  # 单条记忆最大 10KB


def validate_agent_id(agent_id: str) -> tuple[bool, str]:
    """验证 agent_id 格式，返回 (is_valid, error_msg)"""
    # 【P3 增强】类型检查
    if not isinstance(agent_id, str):
        return False, f"agent_id 必须是字符串，当前类型: {type(agent_id).__name__}"
    if not agent_id:
        return False, "agent_id 不能为空"
    if not AGENT_ID_PATTERN.match(agent_id):
        return False, f"无效的 agent_id: {agent_id}（只允许字母、数字、下划线、连字符，长度 1-64）"
    return True, ""


def validate_days(days: int) -> tuple[bool, str]:
    """验证 days 参数"""
    if days < 1:
        return False, "days 必须 >= 1"
    if days > MAX_DAYS:
        return False, f"days 不能超过 {MAX_DAYS}"
    return True, ""


def validate_limit(limit: int) -> tuple[bool, str]:
    """验证 limit 参数"""
    if limit < 1:
        return False, "limit 必须 >= 1"
    if limit > MAX_LIMIT:
        return False, f"limit 不能超过 {MAX_LIMIT}"
    return True, ""


def validate_fact(fact: str) -> tuple[bool, str]:
    """验证 fact 内容（含敏感信息检测）"""
    if not fact or not fact.strip():
        return False, "fact 不能为空"
    if len(fact) > MAX_FACT_LEN:
        return False, f"fact 长度不能超过 {MAX_FACT_LEN} 字符"
    # 敏感信息检测：检测到则返回警告（不拒绝）
    sensitive_warnings = check_sensitive_info(fact)
    if sensitive_warnings:
        return True, "; ".join(sensitive_warnings)
    return True, ""


def validate_query(query: str) -> tuple[bool, str]:
    """验证 query 参数"""
    if not query or not query.strip():
        return False, "query 不能为空"
    if len(query) > 500:
        return False, "query 长度不能超过 500 字符"
    return True, ""
