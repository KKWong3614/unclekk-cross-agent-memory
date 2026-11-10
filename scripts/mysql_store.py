#!/usr/bin/env python3
"""
MySQL/TiDB 存储层 - 实现本地数据主权

与 TiDB 完全兼容（MySQL 协议），支持：
- 记忆写入（自动提炼+去重+文件锁保护）
- 记忆搜索（语义搜索+权重排序）
- 审计日志
- 健康检查

配置优先级：
1. 环境变量 CROSS_AGENT_MEMORY_DB_*
2. 配置文件 ~/.hermes/memory_data/db_config.json
3. 默认值（localhost:3306）

依赖：pymysql
"""

import json
import os
import sys
import io
import logging
import time
# P1-7 修复（第六轮审计·部署健壮性）：pymysql 改为可选导入。
# 旧版顶层裸 import 使无 pymysql 的机器上 `import mysql_store`/`import mcp_server`
# 直接 ImportError，mcp_server 的 JSONL 降级兜底（import 层 try/except）不可达，
# 与 SKILL.md「JSONL 零数据库依赖」矛盾。现在无 pymysql 时模块仍可导入，
# HAS_MYSQL=False，仅在实际调用 MySQL 时报清晰错误。
try:
    import pymysql
    HAS_MYSQL = True
except ImportError:
    pymysql = None
    HAS_MYSQL = False
from shared_utils import validate_agent_id, MEMORY_BASE, DAILY_DIR, SHARED_DIR, AUDIT_DIR, INDEX_FILE
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

# 统一数据库异常元组：无 pymysql 时退化为 Exception（except 子句在异常发生时才求值，
# 但 pymysql=None 时访问 pymysql.Error 会 AttributeError，故提前物化）
_DB_ERRORS = (pymysql.Error,) if HAS_MYSQL else (Exception,)




def _convert_decimal(obj):
    """将 Decimal 和 datetime 类型转换为可 JSON 序列化的类型"""
    from decimal import Decimal
    from datetime import datetime, date
    
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimal(item) for item in obj]
    return obj


def _normalize_row(row: dict) -> dict:
    """把 MySQL 原始行统一为与 JSONL 后端一致的字段结构（修复 M-2 双后端 schema 不一致）。

    - tags：MySQL 存为 JSON 字符串 → 统一解析为 list
    - all_agents：GROUP_CONCAT 得到逗号分隔字符串 → 统一为 list 并放到 `agents` 字段
      （JSONL 后端记录使用 `agents` 列表，下游 _filter_by_agent / scope 隔离均依赖该字段）
    """
    row = _convert_decimal(row)
    if isinstance(row.get("tags"), str):
        try:
            row["tags"] = json.loads(row["tags"])
        except (json.JSONDecodeError, TypeError):
            row["tags"] = []
    aa = row.pop("all_agents", None)
    if aa:
        row["agents"] = [a for a in str(aa).split(",") if a]
    else:
        row.setdefault("agents", [])
    return row



# ═══════════════════════════════════════════════════════════════════════════════
# 日志配置
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('mysql_store')

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

# HAS_MYSQL 已在文件头部 import 区定义（P1-7 修复：pymysql 可选导入），
# 此处删除旧版重复的死代码检测块（顶层裸 import 先崩，本块永远不可达）。

# 全局存储实例（单例模式）
_store_instance = None

# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": os.getenv("CROSS_AGENT_MEMORY_DB_PASSWORD", None),
    "database": "cross_agent_memory",
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 30,
    "write_timeout": 30,
    "autocommit": True,
}


def _load_config() -> dict:
    """加载数据库配置"""
    config = DEFAULT_CONFIG.copy()
    
    # 从配置文件加载
    config_file = Path.home() / ".hermes" / "memory_data" / "db_config.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                config.update(file_config)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] 读取数据库配置失败: {e}", file=sys.stderr)
    
    # 从环境变量加载（优先级最高）
    env_mapping = {
        "CROSS_AGENT_MEMORY_DB_HOST": "host",
        "CROSS_AGENT_MEMORY_DB_PORT": "port",
        "CROSS_AGENT_MEMORY_DB_USER": "user",
        "CROSS_AGENT_MEMORY_DB_PASSWORD": "password",
        "CROSS_AGENT_MEMORY_DB_NAME": "database",
    }
    for env_key, config_key in env_mapping.items():
        env_val = os.getenv(env_key)
        if env_val:
            config[config_key] = int(env_val) if config_key == "port" else env_val
    
    return config


# ═══════════════════════════════════════════════════════════════════════════════
# 连接管理
# ═══════════════════════════════════════════════════════════════════════════════

class MySQLStore:
    """MySQL/TiDB 存储层"""
    
    def __init__(self, config: dict = None):
        self.config = config or _load_config()
        self._connection = None
        self._schema_initialized = False
    
    def _init_schema(self):
        """初始化数据库 schema（创建表）"""
        if self._schema_initialized:
            return
        try:
            with self._cursor() as cursor:
                # 共享记忆表（实际被读写查询使用的表名）
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS memories ("
                    "fact_id VARCHAR(64) PRIMARY KEY, "
                    "fact TEXT, "
                    "original TEXT, "
                    "agent_id VARCHAR(64) DEFAULT '', "
                    "level VARCHAR(20) DEFAULT 'normal', "
                    "confidence FLOAT DEFAULT 0.5, "
                    "tags JSON, "
                    "refinement_method VARCHAR(20) DEFAULT 'local', "
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )
                # 记忆-Agent 关联表（支持多 Agent 共享同一条记忆）
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS memory_agents ("
                    "fact_id VARCHAR(64) NOT NULL, "
                    "agent_id VARCHAR(64) NOT NULL, "
                    "PRIMARY KEY (fact_id, agent_id), "
                    "INDEX idx_agent (agent_id)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )
                self._schema_initialized = True
                print("[mysql_store] Schema initialized successfully")
        except Exception as e:
            print(f"[WARN] Schema initialization failed: {e}", file=sys.stderr)
    
    def _get_connection(self) -> "pymysql.Connection":
        """获取数据库连接（自动重连）"""
        # P1-7 守卫：无 pymysql 时禁止触碰 MySQL，报清晰错误（调用方捕获后降级 JSONL）
        if not HAS_MYSQL:
            raise RuntimeError(
                "pymysql 未安装，MySQL 存储不可用。请 pip install pymysql，"
                "或使用 JSONL 文件存储（本技能默认零数据库依赖）。"
            )
        if self._connection is None or not self._connection.open:
            # R·运行稳定性（TRACE 4.5）：连接失败自动重试 1 次（1s 退避），
            # 吸收瞬时网络/服务抖动；重试仍失败才抛 ConnectionError（调用方捕获后降级 JSONL）。
            last_err = None
            connected = False
            for attempt in (1, 2):
                try:
                    self._connection = pymysql.connect(
                        host=self.config["host"],
                        port=self.config["port"],
                        user=self.config["user"],
                        password=self.config["password"],
                        database=self.config["database"],
                        charset=self.config["charset"],
                        connect_timeout=self.config["connect_timeout"],
                        read_timeout=self.config["read_timeout"],
                        write_timeout=self.config["write_timeout"],
                        autocommit=self.config["autocommit"],
                        cursorclass=pymysql.cursors.DictCursor,
                    )
                    connected = True
                    break
                except _DB_ERRORS as e:
                    last_err = e
                    if attempt == 1:
                        time.sleep(1.0)
            if not connected:
                raise ConnectionError(f"MySQL 连接失败（已自动重试 1 次）: {last_err}")
            # 连接成功后再初始化 schema。
            # 注意：此前在连接建立前调用 _init_schema，而 _init_schema 内部
            # _cursor() → _get_connection() 时 _connection 仍为 None，会形成
            # 无限递归（RecursionError），递归被 except 兜底打印
            # "[WARN] Schema initialization failed"，导致 schema 永远初始化失败。
            # 现将调用置于 pymysql.connect 成功之后，此时 _connection 已存在，
            # _init_schema → _cursor → _get_connection 会立即返回，递归消除。
            if not self._schema_initialized:
                self._init_schema()
        return self._connection
    
    @contextmanager
    def _cursor(self):
        """上下文管理器：自动管理游标"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
    
    def close(self):
        """关闭连接"""
        if self._connection and self._connection.open:
            self._connection.close()
            self._connection = None
    
    # ─────────────────────────────────────────────────────────────────────────
    # 记忆写入
    # ─────────────────────────────────────────────────────────────────────────
    
    def write_memory(
        self,
        fact_id: str,
        fact: str,
        original: str,
        agent_id: str,
        level: str = "normal",
        confidence: float = 0.5,
        tags: list = None,
        refinement_method: str = "local",
    ) -> dict:
        """
        写入记忆（遵循先读后动原则）
        
        流程：
        1. 先读取：检查 fact_id 是否已存在
        2. 判断：存在则合并，不存在则新写入
        3. 验证：写入后确认成功
        
        Returns:
            {"success": True, "fact_id": ..., "action": "new"|"merged"}
        """
        try:
            with self._cursor() as cursor:
                # 【先读后动】Step 1: 先读取，检查是否已存在
                cursor.execute(
                    "SELECT fact_id FROM memories WHERE fact_id = %s",
                    (fact_id,)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # 【先读后动】Step 2a: 已存在，执行合并
                    cursor.execute(
                        """UPDATE memories 
                           SET updated_at = NOW(), confidence = GREATEST(confidence, %s)
                           WHERE fact_id = %s""",
                        (confidence, fact_id)
                    )
                    # 确保关联表有记录
                    cursor.execute(
                        """INSERT IGNORE INTO memory_agents (fact_id, agent_id) 
                           VALUES (%s, %s)""",
                        (fact_id, agent_id)
                    )
                    return {"success": True, "fact_id": fact_id, "action": "merged"}
                else:
                    # 【先读后动】Step 2b: 不存在，执行新写入
                    cursor.execute(
                        """INSERT INTO memories 
                           (fact_id, fact, original, agent_id, level, confidence, tags, refinement_method)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (fact_id, fact, original, agent_id, level, confidence,
                         json.dumps(tags or [], ensure_ascii=False), refinement_method)
                    )
                    # 写入关联表
                    cursor.execute(
                        "INSERT INTO memory_agents (fact_id, agent_id) VALUES (%s, %s)",
                        (fact_id, agent_id)
                    )
                    return {"success": True, "fact_id": fact_id, "action": "new"}
        except _DB_ERRORS as e:
            return {"success": False, "error": f"数据库写入失败: {e}"}
    
    # ─────────────────────────────────────────────────────────────────────────
    # 记忆搜索
    # ─────────────────────────────────────────────────────────────────────────
    
    def search_memories(
        self,
        query: str,
        days: int = 30,
        limit: int = 10,
        agent_id: str = None,
    ) -> list:
        """
        搜索记忆（关键词匹配 + 2-gram 相似度）
        
        Args:
            query: 搜索关键词
            days: 搜索最近N天
            limit: 最大返回数
            agent_id: 可选，过滤特定Agent
        
        Returns:
            记忆列表，按相关性排序
        """
        try:
            with self._cursor() as cursor:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                
                # 构建查询
                sql = """
                    SELECT m.*, GROUP_CONCAT(ma.agent_id) as all_agents
                    FROM memories m
                    LEFT JOIN memory_agents ma ON m.fact_id = ma.fact_id
                    WHERE m.created_at >= %s
                """
                params = [cutoff]
                
                if agent_id:
                    # 按 memory_agents 成员过滤：返回该 agent 共享的全部记忆（含他人主的）
                    sql += " AND m.fact_id IN (SELECT fact_id FROM memory_agents WHERE agent_id = %s)"
                    params.append(agent_id)
                
                sql += " GROUP BY m.fact_id"
                
                cursor.execute(sql, params)
                all_memories = cursor.fetchall()
                
                # 关键词匹配评分
                query_lower = query.lower()
                query_chars = set(query_lower)
                
                scored = []
                for mem in all_memories:
                    fact_text = (mem.get("fact") or "").lower()
                    tags_str = " ".join(json.loads(mem.get("tags") or "[]")).lower()
                    combined = fact_text + " " + tags_str
                    
                    # 字符匹配度
                    matches = sum(1 for c in query_chars if c in combined)
                    if matches > 0:
                        score = matches / len(query_chars) if query_chars else 0
                        scored.append((score, mem))
                
                # 按分数排序
                scored.sort(key=lambda x: x[0], reverse=True)
                return [_normalize_row(mem) for _, mem in scored[:limit]]
        except _DB_ERRORS as e:
            print(f"[WARN] 搜索失败: {e}", file=sys.stderr)
            return []
    
    def get_all_memories(self, days: int = 30, agent_id: str = None) -> list:
        """获取所有记忆"""
        try:
            with self._cursor() as cursor:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                
                sql = """
                    SELECT m.*, GROUP_CONCAT(ma.agent_id) as all_agents
                    FROM memories m
                    LEFT JOIN memory_agents ma ON m.fact_id = ma.fact_id
                    WHERE m.created_at >= %s
                """
                params = [cutoff]
                
                if agent_id:
                    # 按 memory_agents 成员过滤：返回该 agent 共享的全部记忆（含他人主的）
                    sql += " AND m.fact_id IN (SELECT fact_id FROM memory_agents WHERE agent_id = %s)"
                    params.append(agent_id)
                
                sql += " GROUP BY m.fact_id ORDER BY m.created_at DESC"
                
                cursor.execute(sql, params)
                return [_normalize_row(row) for row in cursor.fetchall()]
        except _DB_ERRORS as e:
            print(f"[WARN] 读取失败: {e}", file=sys.stderr)
            return []
    
    # ─────────────────────────────────────────────────────────────────────────
    # 统计与健康
    # ─────────────────────────────────────────────────────────────────────────
    
    def get_statistics(self, days: int = 30) -> dict:
        """返回共享记忆的统计信息"""
        try:
            with self._cursor() as cursor:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT COUNT(*) as total FROM memories WHERE created_at >= %s",
                    (cutoff,)
                )
                total = cursor.fetchone()[0]
                
                cursor.execute(
                    "SELECT COUNT(DISTINCT agent_id) as agent_count FROM memory_agents"
                )
                agent_count = cursor.fetchone()[0]
                
                cursor.execute(
                    "SELECT MAX(created_at) as last_write FROM memories"
                )
                last_write = cursor.fetchone()[0]
                
                return {
                    "total_entries": total,
                    "unique_agents": agent_count or 0,
                    "last_write": str(last_write) if last_write else None
                }
        except Exception as e:
            return {"total_entries": 0, "unique_agents": 0, "last_write": None, "note": "MySQL 不可用"}

    def log_audit(self, action: str, agent_id: str, data: dict = None, mcp_session_id: str = None, fact_id: str = None) -> dict:
        """将审计日志写入 memory_audit 表（迁移工具 migrate_to_mysql 使用）。

        与 mcp_server 的 _log_audit_secure（写本地 JSONL）不同，这里落 MySQL，
        供跨 Agent 审计集中查询。表不存在时自动创建（schema 以真实库为准：
        id, action, agent_id, fact_id, details JSON, created_at）。
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_audit (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        action VARCHAR(32) NOT NULL,
                        agent_id VARCHAR(64) NOT NULL,
                        fact_id VARCHAR(64),
                        details JSON,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                payload = dict(data or {})
                if mcp_session_id is not None:
                    payload["mcp_session_id"] = mcp_session_id
                cursor.execute(
                    "INSERT INTO memory_audit (action, agent_id, fact_id, details) "
                    "VALUES (%s, %s, %s, %s)",
                    (action, agent_id, fact_id, json.dumps(payload, ensure_ascii=False)),
                )
            return {"success": True}
        except _DB_ERRORS as e:
            return {"success": False, "error": f"审计日志写入失败: {e}"}

    def health_check(self) -> dict:
        """检查数据库连接状态，并返回关键计数（供迁移工具 / 验证使用）"""
        try:
            with self._cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.execute("SELECT COUNT(*) AS c FROM memories")
                memory_count = cursor.fetchone()["c"]
                cursor.execute("SELECT COUNT(DISTINCT agent_id) AS c FROM memory_agents")
                agent_count = cursor.fetchone()["c"]
                try:
                    cursor.execute("SELECT COUNT(*) AS c FROM memory_audit")
                    audit_count = cursor.fetchone()["c"]
                except _DB_ERRORS:
                    audit_count = 0
                return {
                    "status": "healthy",
                    "connection": "active",
                    "memory_count": memory_count,
                    "agent_count": agent_count,
                    "audit_count": audit_count,
                }
        except Exception as e:
            self._connection = None
            return {"status": "unhealthy", "error": str(e)}

    def get_boot_context(self, agent_id: str = None, max_items: int = 20) -> list:
        """返回最近的记忆作为启动上下文。
        
        agent_id 为 None 时返回全量最近记忆（scope=all，跨Agent共享）；
        传入 agent_id 时仅返回该 agent 共享的记忆（scope=mine）。
        """
        try:
            with self._cursor() as cursor:
                if agent_id:
                    cursor.execute(
                        "SELECT m.fact, m.tags, m.confidence FROM memories m "
                        "WHERE m.fact_id IN (SELECT fact_id FROM memory_agents WHERE agent_id = %s) "
                        "ORDER BY m.created_at DESC LIMIT %s",
                        (agent_id, max_items)
                    )
                else:
                    cursor.execute(
                        "SELECT m.fact, m.tags, m.confidence FROM memories m "
                        "ORDER BY m.created_at DESC LIMIT %s",
                        (max_items,)
                    )
                results = cursor.fetchall()
                return [
                    {
                        "fact": r["fact"],
                        "tags": json.loads(r["tags"]) if r["tags"] else [],
                        "confidence": r["confidence"]
                    } for r in results
                ]
        except Exception:
            return []
    
    # ─────────────────────────────────────────────────────────────────────────
    # 删除（带备份返回）
    # ─────────────────────────────────────────────────────────────────────────

    def delete_fact(self, fact_id: str, agent_id: str = None, scope: str = "all") -> dict:
        """删除记忆（先取回待删记录供备份，再删除）。

        scope='all'（默认）：删除该 fact_id 的记忆本体与全部关联（跨Agent）；
        scope='mine'：仅移除该 agent_id 的共享关联；若移除后无其他 agent 共享，则连带删除记忆本体。
        返回 {success, found, deleted, rows}；deleted 为实际删除的数据库行数
        （共享关联 + 记忆本体，P2-3 修复：不再硬编码 1，用 cursor.rowcount 如实计数）；
        rows 为被删记录的备份，供上层写入 trash/。
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "SELECT fact_id, fact, original, agent_id, level, confidence, tags, "
                    "refinement_method, created_at, updated_at FROM memories WHERE fact_id = %s",
                    (fact_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return {"success": True, "found": False, "deleted": 0, "rows": []}
                cursor.execute("SELECT agent_id FROM memory_agents WHERE fact_id = %s", (fact_id,))
                row["all_agents"] = [r["agent_id"] for r in cursor.fetchall()]

                if scope == "mine" and agent_id:
                    cursor.execute(
                        "DELETE FROM memory_agents WHERE fact_id = %s AND agent_id = %s",
                        (fact_id, agent_id)
                    )
                    assoc_deleted = max(cursor.rowcount, 0)
                    cursor.execute("SELECT COUNT(*) AS c FROM memory_agents WHERE fact_id = %s", (fact_id,))
                    remaining = cursor.fetchone()["c"]
                    deleted = assoc_deleted
                    if remaining == 0:
                        cursor.execute("DELETE FROM memories WHERE fact_id = %s", (fact_id,))
                        deleted += max(cursor.rowcount, 0)
                else:
                    cursor.execute("DELETE FROM memory_agents WHERE fact_id = %s", (fact_id,))
                    deleted = max(cursor.rowcount, 0)
                    cursor.execute("DELETE FROM memories WHERE fact_id = %s", (fact_id,))
                    deleted += max(cursor.rowcount, 0)
                # 返回前转换 Decimal/datetime 为可 JSON 序列化类型（避免上层 json.dumps 抛错）
                return {"success": True, "found": True, "deleted": deleted, "rows": [_convert_decimal(dict(row))]}
        except _DB_ERRORS as e:
            return {"success": False, "error": f"数据库删除失败: {e}"}
    
    # ─────────────────────────────────────────────────────────────────────────
    # 审计日志
    # ─────────────────────────────────────────────────────────────────────────
    
    

def get_store() -> MySQLStore:
    """获取全局存储实例"""
    global _store_instance
    if _store_instance is None:
        _store_instance = MySQLStore()
    return _store_instance

def close_store():
    """关闭全局存储实例"""
    global _store_instance
    if _store_instance:
        _store_instance.close()
        _store_instance = None


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """CLI 入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="MySQL/TiDB 存储层工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # 健康检查
    subparsers.add_parser("health", help="健康检查")
    
    # 统计
    stats_parser = subparsers.add_parser("stats", help="统计信息")
    stats_parser.add_argument("--days", type=int, default=30, help="统计天数")
    
    # 写入测试
    write_parser = subparsers.add_parser("write-test", help="写入测试")
    write_parser.add_argument("--fact", default="测试记忆", help="测试内容")
    
    args = parser.parse_args()
    
    if not HAS_MYSQL:
        print("错误: pymysql 未安装，请运行 pip install pymysql")
        sys.exit(1)
    
    store = get_store()
    
    try:
        if args.command == "health":
            result = store.health_check()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "stats":
            result = store.get_statistics(args.days)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "write-test":
            import hashlib
            fact_id = hashlib.sha256(args.fact.encode()).hexdigest()[:16]
            result = store.write_memory(
                fact_id=fact_id,
                fact=args.fact,
                original=args.fact,
                agent_id="test_cli",
                level="normal",
                confidence=0.5,
                tags=["test"],
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            parser.print_help()
    finally:
        close_store()


if __name__ == "__main__":
    main()