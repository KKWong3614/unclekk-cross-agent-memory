#!/usr/bin/env python3
"""
数据迁移工具：JSONL -> MySQL

将现有 JSONL 文件中的记忆数据迁移到 MySQL/TiDB 数据库。

用法:
    python migrate_to_mysql.py [--dry-run] [--days N]

选项:
    --dry-run   只显示迁移计划，不实际执行
    --days N    迁移最近N天的数据（默认90天）
"""

import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from shared_utils import SHARED_DIR, AUDIT_DIR, MEMORY_BASE
from mysql_store import get_store, HAS_MYSQL



def scan_jsonl_files(days: int = 90) -> list:
    """扫描 JSONL 文件，返回待迁移记录"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = []
    
    if not SHARED_DIR.exists():
        print(f"[WARN] 共享目录不存在: {SHARED_DIR}")
        return records
    
    for f in sorted(SHARED_DIR.glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    record = json.loads(line)
                    record["_source_file"] = str(f)
                    records.append(record)
                except json.JSONDecodeError as e:
                    print(f"[WARN] migrate_to_mysql.py JSON decode: {e}", file=sys.stderr)
    
    return records


def migrate_memories(records: list, dry_run: bool = False) -> dict:
    """
    迁移记忆数据到 MySQL（遵循先读后动原则）
    
    流程：
    1. 先读取：扫描 JSONL 文件，获取所有待迁移记录
    2. 后动：调用 store.write_memory()，内部会先检查是否存在再写入
    3. 验证：统计迁移结果
    """
    if not HAS_MYSQL:
        return {"error": "pymysql 未安装"}
    
    store = get_store()
    stats = {
        "total": len(records),
        "migrated": 0,
        "skipped": 0,
        "errors": 0,
    }
    
    for i, record in enumerate(records, 1):
        fact_id = record.get("fact_id")
        if not fact_id:
            stats["errors"] += 1
            print(f"[{i}/{len(records)}] 跳过: 无 fact_id")
            continue
        
        fact = record.get("fact", "")
        original = record.get("original", fact)
        agents = record.get("agents", [])
        level = record.get("level", "normal")
        confidence = record.get("confidence", 0.5)
        tags = record.get("tags", [])
        refinement_method = record.get("refinement_method", "local")
        
        if dry_run:
            print(f"[{i}/{len(records)}] 将迁移: {fact_id[:16]} - {fact[:50]}...")
            stats["migrated"] += 1
            continue
        
        # 写入主记忆
        result = store.write_memory(
            fact_id=fact_id,
            fact=fact,
            original=original,
            agent_id=agents[0] if agents else "migrated",
            level=level,
            confidence=confidence,
            tags=tags,
            refinement_method=refinement_method,
        )
        
        if result["success"]:
            stats["migrated"] += 1
            # 写入所有 Agent 关联
            for agent in agents[1:]:
                try:
                    with store._cursor() as cursor:
                        cursor.execute(
                            "INSERT IGNORE INTO memory_agents (fact_id, agent_id) VALUES (%s, %s)",
                            (fact_id, agent)
                        )
                except Exception as e:
                    print(f"[WARN] migrate_to_mysql.py exception: {e}", file=sys.stderr)
        else:
            stats["errors"] += 1
            print(f"[{i}/{len(records)}] 错误: {result.get('error')}")
    
    return stats


def migrate_audit_logs(days: int = 90, dry_run: bool = False) -> dict:
    """迁移审计日志到 MySQL"""
    if not HAS_MYSQL:
        return {"error": "pymysql 未安装"}
    
    store = get_store()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    stats = {
        "total": 0,
        "migrated": 0,
        "errors": 0,
    }
    
    if not AUDIT_DIR.exists():
        print(f"[WARN] 审计目录不存在: {AUDIT_DIR}")
        return stats
    
    for f in sorted(AUDIT_DIR.glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    record = json.loads(line)
                    stats["total"] += 1
                    
                    if dry_run:
                        stats["migrated"] += 1
                        continue
                    
                    store.log_audit(
                        action=record.get("action", "unknown"),
                        agent_id=record.get("agent_id", record.get("agent_id_claimed", "unknown")),
                        data=record,
                        mcp_session_id=record.get("mcp_session_id"),
                    )
                    stats["migrated"] += 1
                except Exception as e:
                    stats["errors"] += 1
    
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="数据迁移工具：JSONL -> MySQL")
    parser.add_argument("--dry-run", action="store_true", help="只显示迁移计划")
    parser.add_argument("--days", type=int, default=90, help="迁移天数")
    parser.add_argument("--skip-audit", action="store_true", help="跳过审计日志")
    args = parser.parse_args()
    
    if not HAS_MYSQL:
        print("错误: pymysql 未安装")
        sys.exit(1)
    
    print("=" * 60)
    print("数据迁移工具：JSONL -> MySQL")
    print("=" * 60)
    print(f"源目录: {SHARED_DIR}")
    print(f"迁移天数: {args.days}")
    print(f"模式: {'预览' if args.dry_run else '实际迁移'}")
    print()
    
    # 扫描记忆
    print("[1/2] 扫描记忆数据...")
    memories = scan_jsonl_files(days=args.days)
    print(f"  找到 {len(memories)} 条记忆记录")
    
    if memories:
        print("\n[2/2] 迁移记忆数据...")
        mem_stats = migrate_memories(memories, dry_run=args.dry_run)
        print(f"  总数: {mem_stats['total']}")
        print(f"  已迁移: {mem_stats['migrated']}")
        print(f"  跳过: {mem_stats['skipped']}")
        print(f"  错误: {mem_stats['errors']}")
    
    # 审计日志
    if not args.skip_audit:
        print("\n[额外] 迁移审计日志...")
        audit_stats = migrate_audit_logs(days=args.days, dry_run=args.dry_run)
        print(f"  总数: {audit_stats['total']}")
        print(f"  已迁移: {audit_stats['migrated']}")
        print(f"  错误: {audit_stats['errors']}")
    
    # 验证
    if not args.dry_run:
        print("\n[验证] 检查 MySQL 数据...")
        store = get_store()
        health = store.health_check()
        print(f"  状态: {health['status']}")
        print(f"  记忆数: {health['memory_count']}")
        print(f"  Agent数: {health['agent_count']}")
        print(f"  审计日志数: {health['audit_count']}")
    
    print("\n" + "=" * 60)
    print("迁移完成！" if not args.dry_run else "预览完成，去掉 --dry-run 执行实际迁移")
    print("=" * 60)


if __name__ == "__main__":
    main()
