#!/usr/bin/env python3
"""
系统验证脚本 - System Verification

功能：
1. Agent在线状态检查
2. 记忆完整性检查
3. 综合健康检查
4. 状态快照备份

用法:
  python verify.py agents    # Agent在线检查
  python verify.py memory    # 记忆完整性
  python verify.py health    # 综合健康检查
  python verify.py backup    # 创建状态快照
"""

import argparse
import json
import os
import shutil
import subprocess

# Local run_cmd wrapper (replaces upgrade_manager.run_cmd)
def run_cmd(cmd, timeout=10):
    """Run external command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, str(e), ""

import sys
import io
from datetime import datetime
from pathlib import Path

# 重定向 stdout/stderr 以支持 UTF-8（在导入完成后执行）
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if not isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

MEMORY_BASE = Path(
    os.environ.get("CROSS_AGENT_MEMORY_BASE") or (Path.home() / ".hermes" / "memory_data")
)
BACKUP_DIR = Path.home() / ".hermes" / "backups"




def cmd_agents(args):
    """检查所有Agent在线状态"""
    print("=" * 50)
    print("Agent 在线状态检查")
    print("=" * 50)

    rc, stdout, stderr = run_cmd(["openclaw", "agents", "list"])

    if rc != 0:
        print(f"[X] 命令执行失败: {stderr}")
        # 尝试替代命令
        rc2, out2, _ = run_cmd(["openclaw", "status"])
        if rc2 == 0:
            print("Gateway 状态:")
            print(out2[:500])
        sys.exit(1)

    lines = stdout.splitlines()
    if not lines:
        print("未检测到Agent信息")
        return

    # 解析状态
    healthy = 0
    offline = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("="):
            continue
        # 简单状态检测（根据实际输出格式调整）
        if any(kw in line.lower() for kw in ["online", "healthy", "running", "active"]):
            print(f"  [OK] {line}")
            healthy += 1
        elif any(kw in line.lower() for kw in ["offline", "error", "stopped", "dead"]):
            print(f"  [X] {line}")
            offline += 1
        else:
            print(f"  [?] {line}")

    print()
    print(f"汇总: {healthy} 在线, {offline} 离线")
    print("=" * 50)

    if offline > 0 and not args.ignore_errors:
        print("[WARN] 有Agent离线，请检查")


def cmd_memory(args):
    """检查记忆完整性"""
    print("=" * 50)
    print("记忆完整性检查")
    print("=" * 50)

    # 目录检查
    dirs = {
        "记忆根目录": MEMORY_BASE,
        "每日日志": MEMORY_BASE / "daily",
        "共享事实": MEMORY_BASE / "shared",
        "审计日志": MEMORY_BASE / "audit",
    }

    all_ok = True
    for name, path in dirs.items():
        status = "[OK]" if path.exists() else "[X]"
        if not path.exists():
            all_ok = False
        print(f"  {status} {name}: {path}")

    if not all_ok:
        print("\n[X] 目录结构异常，请初始化记忆系统")
        return

    # 统计
    shared_files = list((MEMORY_BASE / "shared").glob("*.jsonl"))
    audit_files = list((MEMORY_BASE / "audit").glob("*.jsonl"))

    total_facts = 0
    total_agents = set()
    broken_records = 0

    for f in shared_files:
        content = f.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.strip()]
        for line in lines:
            try:
                d = json.loads(line)
                if d.get("fact") and d.get("fact_id"):
                    total_facts += 1
                    total_agents.update(d.get("agents", []))
                else:
                    broken_records += 1
            except json.JSONDecodeError:
                broken_records += 1

    print(f"\n  共享事实文件: {len(shared_files)} 个")
    print(f"  事实记录总数: {total_facts} 条")
    print(f"  参与Agent数: {len(total_agents)} 个")
    print(f"  损坏记录: {broken_records} 条")

    if broken_records > 0:
        print(f"\n[WARN] 发现 {broken_records} 条损坏记录，建议运行: python shared_memory.py refine --days 30")

    # 最新日期
    if shared_files:
        latest = sorted(shared_files)[-1]
        print(f"  最新数据: {latest.name}")

    print("=" * 50)
    if total_facts == 0:
        print("[WARN] 记忆库为空，请先写入记忆")
    else:
        print(f"[OK] 记忆完整: {total_facts} 条共享事实")


def cmd_health(args):
    """综合健康检查"""
    print("=" * 60)
    print(f"OpenClaw 系统健康检查  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print("=" * 60)

    checks = []

    # 1. Gateway 进程
    print("\n[1/5] Gateway 进程...")
    rc, out, err = run_cmd(["openclaw", "gateway", "status"])
    if rc == 0:
        print("  [OK] Gateway 运行中")
        checks.append(True)
    else:
        print("  [X] Gateway 未运行或异常")
        print(f"     {err[:100]}")
        checks.append(False)

    # 2. Agent 在线
    print("\n[2/5] Agent 状态...")
    rc, out, _ = run_cmd(["openclaw", "agents", "list"])
    if rc == 0 and ("online" in out.lower() or "healthy" in out.lower() or out.strip()):
        print("  [OK] Agent 检测正常")
        checks.append(True)
    else:
        print("  [WARN] Agent 状态未知（可能需要手动检查）")
        checks.append(True)  # 不阻塞

    # 3. 记忆目录
    print("\n[3/5] 记忆目录...")
    mem_ok = all(
        (MEMORY_BASE / sub).exists()
        for sub in ["daily", "shared", "audit"]
    )
    if mem_ok:
        print("  [OK] 记忆目录完整")
        checks.append(True)
    else:
        print("  [X] 记忆目录缺失")
        checks.append(False)

    # 4. 磁盘空间
    print("\n[4/5] 磁盘空间...")
    try:
        import shutil as sh
        total, used, free = sh.disk_usage(Path.home())
        free_gb = free // (1024**3)
        if free_gb > 5:
            print(f"  [OK] 可用空间: {free_gb} GB")
            checks.append(True)
        else:
            print(f"  [WARN] 可用空间不足: {free_gb} GB")
            checks.append(False)
    except Exception as e:
        print(f"  [WARN] 无法检测磁盘空间: {e}")
        checks.append(True)

    # 5. 日志检查（最近1小时是否有错误）
    print("\n[5/5] 最近日志错误...")
    log_dir = Path.home() / ".hermes" / "logs"
    if log_dir.exists():
        recent_errors = 0
        for f in sorted(log_dir.glob("*.log"))[-3:]:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                # 检查ERROR/CRITICAL
                recent_errors += content.count("ERROR") + content.count("CRITICAL")
            except Exception as e:
                print(f"[WARN] verify.py exception: {e}", file=sys.stderr)
        if recent_errors == 0:
            print("  [OK] 无错误日志")
            checks.append(True)
        else:
            print(f"  [WARN] 发现 {recent_errors} 条错误日志")
            checks.append(False)
    else:
        print("  [WARN] 日志目录不存在（跳过）")
        checks.append(True)

    # 总结
    print("\n" + "=" * 60)
    passed = sum(checks)
    total = len(checks)
    pct = int(passed / total * 100)

    if pct == 100:
        print(f"  [OK] 健康评分: {passed}/{total} ({pct}%) - 系统正常")
    elif pct >= 80:
        print(f"  [WARN] 健康评分: {passed}/{total} ({pct}%) - 基本正常，部分项目需关注")
    else:
        print(f"  [X] 健康评分: {passed}/{total} ({pct}%) - 系统异常，请检查")

    print("=" * 60)


def cmd_backup(args):
    """
    创建状态快照（遵循先读后动原则）
    
    流程：
    1. 先读取：获取当前系统状态（Gateway、Agent、记忆）
    2. 后动：创建备份目录，保存快照
    3. 验证：显示备份结果
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = BACKUP_DIR / f"health_snapshot_{ts}"

    print(f"创建状态快照: {snapshot_dir}")
    print("-" * 50)

    snap_data = {
        "created": datetime.now().isoformat(),
        "gateway_status": None,
        "agent_count": 0,
        "memory_facts": 0,
        "health_score": 0,
    }

    # 【先读后动】Step 1: 先读取当前状态
    # Gateway状态
    rc, out, _ = run_cmd(["openclaw", "gateway", "status"])
    snap_data["gateway_status"] = "ok" if rc == 0 else "error"

    # Agent数量
    rc, out, _ = run_cmd(["openclaw", "agents", "list"])
    if rc == 0:
        snap_data["agent_count"] = len([l for l in out.splitlines() if l.strip()])

    # 记忆统计
    try:
        shared_files = list((MEMORY_BASE / "shared").glob("*.jsonl"))
        fact_count = 0
        for f in shared_files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        if json.loads(line).get("fact"):
                            fact_count += 1
                    except json.JSONDecodeError as e:
                        print(f"[WARN] verify.py JSON decode: {e}", file=sys.stderr)
        snap_data["memory_facts"] = fact_count
    except Exception as e:
        print(f"[WARN] verify.py exception: {e}", file=sys.stderr)

    # 简单评分
    snap_data["health_score"] = (
        (1 if snap_data["gateway_status"] == "ok" else 0) +
        (1 if snap_data["agent_count"] > 0 else 0) +
        (1 if snap_data["memory_facts"] > 0 else 0)
    )

    # 写入快照
    snapshot_dir.mkdir(parents=True)
    with open(snapshot_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(snap_data, f, ensure_ascii=False, indent=2)

    print(f"\n快照内容:")
    for k, v in snap_data.items():
        print(f"  {k}: {v}")

    print(f"\n[OK] 快照已保存: {snapshot_dir}")
    print("-" * 50)


def main():
    parser = argparse.ArgumentParser(description="系统验证脚本")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("agents", help="Agent在线检查")
    sub.add_parser("memory", help="记忆完整性")
    sub.add_parser("health", help="综合健康检查")

    bak = sub.add_parser("backup", help="创建状态快照")
    bak.add_argument("--path", default=str(BACKUP_DIR), help="备份目录")

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "agents":
        cmd_agents(args)
    elif args.cmd == "memory":
        cmd_memory(args)
    elif args.cmd == "health":
        cmd_health(args)
    elif args.cmd == "backup":
        cmd_backup(args)


if __name__ == "__main__":
    main()
