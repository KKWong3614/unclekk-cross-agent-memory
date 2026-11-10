#!/usr/bin/env python3
"""
cross-agent-memory: JSONL 初始化脚本（v6.22.2 起为 detect_storage 的薄封装）

历史问题（第三轮审计 P2-2）：本文件是 v6.19 旧物，曾创建已废弃的
memory.json / skills.json / sessions.json（真实存储结构是 shared/<日期>.jsonl +
index.jsonl），与 SKILL.md「已废弃」声明矛盾，且 verify 步骤还向 memory.json
写入测试数据。现已重写为委托 detect_storage.init_jsonl 的薄封装，
保持原 CLI 参数（--storage-dir / --check-only）向后兼容。

推荐入口（功能更全，含 MySQL 探测）:
  python detect_storage.py [--check-only]
"""

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from detect_storage import check_jsonl, init_jsonl  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='cross-agent-memory JSONL 存储初始化（detect_storage 薄封装）')
    parser.add_argument('--storage-dir', type=str,
                        default=None,
                        help='存储目录路径（默认使用 CROSS_AGENT_MEMORY_BASE 或 ~/.hermes/memory_data）')
    parser.add_argument('--check-only', action='store_true',
                        help='仅检查存储状态，不初始化')
    args = parser.parse_args()

    # 基目录：显式参数 > shared_utils 按环境变量解析的 MEMORY_BASE
    if args.storage_dir:
        memory_base = Path(args.storage_dir).expanduser()
    else:
        from shared_utils import MEMORY_BASE
        memory_base = MEMORY_BASE

    print("=" * 60)
    print("  cross-agent-memory JSONL 存储初始化")
    print("=" * 60)
    print(f"\n存储目录: {memory_base}")

    if args.check_only:
        status = check_jsonl(memory_base)
        print("\n--- 存储状态 ---")
        print(f"  就绪: {'是' if status.get('available') else '否'}")
        print(f"  共享事实条数: {status.get('records', 0)}")
        print(f"  {status.get('message', '')}")
        print("\n[OK] 存储已就绪" if status.get('available')
              else "\n[!] 存储未就绪，请运行: python init_jsonl.py --storage-dir <path>")
        return

    result = init_jsonl(memory_base)
    print(f"\n[INFO] {result.get('message', '')}")
    status = check_jsonl(memory_base)
    if status.get('available'):
        print("\n" + "=" * 60)
        print("  [OK] JSONL 初始化成功！")
        print("=" * 60)
        print("\n真实存储结构: shared/<日期>.jsonl + index.jsonl（memory.json 等已废弃）")
        print("提示: 如需 MySQL 探测与完整报告，请使用 detect_storage.py")
    else:
        print("\n[ERROR] 初始化后检查未通过")
        sys.exit(1)


if __name__ == '__main__':
    main()
