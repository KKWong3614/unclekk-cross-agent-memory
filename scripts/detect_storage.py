#!/usr/bin/env python3
"""
cross-agent-memory 首次加载探测和初始化脚本

功能：
1. 检测共享 MySQL 数据库是否可用
2. 检测 JSONL 文件存储是否可用（按真实存储模型：daily/ shared/ refined/ audit/ + index.jsonl）
3. 自动初始化本地存储目录（与 shared_memory.ensure_dirs 保持一致）
4. 返回当前存储层状态，供 Agent 决策使用

所有 Agent 加载 skill 后应自动执行此脚本进行探测。

输出约定：
- stdout = 纯 JSON（供程序解析，json.loads 可直接解析）
- stderr = 人类可读摘要（供人类阅读）

用法：
    python detect_storage.py            # 探测；若 JSONL 未初始化则自动初始化
    python detect_storage.py --check-only  # 仅检查，不初始化（CI / 状态巡检）

【v6.22】修复：旧版以 memory.json 判断 JSONL 就绪、init 创建 memory.json/skills.json/sessions.json，
与真实存储（shared/*.jsonl + index.jsonl）不符；现改为检测/初始化真实目录结构，并补齐 --check-only。
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# 与 shared_memory / mcp_server 共用同一套路径常量，确保探测与实际读写一致
from shared_utils import MEMORY_BASE, DAILY_DIR, SHARED_DIR, REFINED_DIR, AUDIT_DIR, INDEX_FILE

# 默认数据库配置
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": os.getenv("CROSS_AGENT_MEMORY_DB_PASSWORD", None),
    "database": "cross_agent_memory",
    "charset": "utf8mb4",
}

# 当前版本（与 SKILL.md/VERSION 保持一致，发版时三处同步）
SKILL_VERSION = "6.26.3"


def load_db_config(memory_base):
    """加载数据库配置（优先级：环境变量 > 配置文件 > 默认值）"""
    config = DEFAULT_DB_CONFIG.copy()

    env_mappings = {
        'CROSS_AGENT_MEMORY_DB_HOST': 'host',
        'CROSS_AGENT_MEMORY_DB_PORT': 'port',
        'CROSS_AGENT_MEMORY_DB_USER': 'user',
        'CROSS_AGENT_MEMORY_DB_PASSWORD': 'password',
        'CROSS_AGENT_MEMORY_DB_NAME': 'database',
    }
    for env_key, config_key in env_mappings.items():
        env_val = os.environ.get(env_key)
        if env_val:
            config[config_key] = int(env_val) if config_key == 'port' else env_val

    config_file = memory_base / 'db_config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                for k, v in file_config.items():
                    if not k.startswith('_') and k in config:
                        config[k] = v
        except Exception as e:
            print(f'[WARN] 读取配置文件失败: {e}', file=sys.stderr)

    return config


def _short_mysql_error(e: Exception) -> tuple:
    """把底层 pymysql 异常转成一句人类可读的短消息（TRACE R·异常处理：
    不再把原始异常全文直接塞进 message；完整信息放 detail 字段供排查）。"""
    detail = str(e).strip()
    low = detail.lower()
    if "access denied" in low:
        short = "认证失败（用户名或密码错误）"
    elif "unknown database" in low:
        short = "数据库不存在"
    elif "name or service not known" in low or "getaddrinfo failed" in low:
        short = "主机名无法解析（检查 host 配置）"
    elif "timed out" in low or "timeout" in low:
        short = "连接超时（检查网络与防火墙）"
    elif "can't connect" in low or "connection refused" in low or "10061" in low:
        short = "无法连接到数据库服务器（检查 host/port 与 MySQL 服务状态）"
    else:
        short = "连接失败（详见 detail 字段）"
    return f"MySQL 连接失败: {short}", detail


def check_mysql(config):
    """检查 MySQL 连接状态（失败不抛异常，返回 available=False）"""
    try:
        import pymysql
        conn = pymysql.connect(
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            charset=config['charset'],
            connect_timeout=5,
        )
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES LIKE %s", (config['database'],))
        db_exists = cursor.fetchone()
        tables = []
        if db_exists:
            conn.select_db(config['database'])
            cursor.execute('SHOW TABLES')
            tables = [t[0] for t in cursor.fetchall()]
        conn.close()
        return {
            'available': True,
            'database_exists': bool(db_exists),
            'tables': tables,
            'message': f"MySQL {config['host']}:{config['port']} 连接成功",
        }
    except ImportError:
        return {'available': False, 'message': 'pymysql 未安装，无法连接 MySQL'}
    except Exception as e:
        message, detail = _short_mysql_error(e)
        return {'available': False, 'message': message, 'detail': detail}


def check_jsonl(memory_base):
    """检测 JSONL 文件存储状态（按真实存储模型）。

    就绪判定：shared/ 目录存在，且已有 shared/*.jsonl 或 index.jsonl。
    统计：shared/*.jsonl 中有效事实条数。
    """
    shared_dir = memory_base / 'shared'
    index_file = memory_base / 'index.jsonl'

    initialized = shared_dir.exists() and (
        any(shared_dir.glob('*.jsonl')) or index_file.exists()
    )

    records = 0
    if shared_dir.exists():
        for f in shared_dir.glob('*.jsonl'):
            try:
                for line in f.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                        if d.get('fact') or d.get('fact_id'):
                            records += 1
                    except json.JSONDecodeError:
                        pass
            except OSError:
                pass

    if initialized:
        return {
            'available': True,
            'records': records,
            'message': f"JSONL 本地存储就绪，{records} 条共享事实",
        }
    return {
        'available': False,
        'records': 0,
        'message': 'JSONL 本地存储未初始化',
    }


def init_jsonl(memory_base):
    """初始化 JSONL 文件存储（与 shared_memory.ensure_dirs 对齐的真实目录结构）。

    创建：daily/ shared/ refined/ audit/ 以及 index.jsonl、config.json。
    不创建 memory.json（旧版残留，已废弃）。

    注意：目录与索引均基于传入的 memory_base（而非模块全局 MEMORY_BASE），
    使初始化对任意指定基目录都正确——修复旧版忽略参数、一律写到全局基目录的缺陷。
    """
    for d in ['daily', 'shared', 'refined', 'audit']:
        (memory_base / d).mkdir(parents=True, exist_ok=True)
    index_file = memory_base / 'index.jsonl'
    if not index_file.exists():
        index_file.write_text('', encoding='utf-8')
    config_file = memory_base / 'config.json'
    if not config_file.exists():
        config_file.write_text(
            json.dumps(
                {
                    'storage_type': 'jsonl',
                    'created_at': datetime.now().isoformat(),
                    'version': SKILL_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
    return {'success': True, 'message': f"JSONL 存储已初始化: {memory_base}"}


def get_agent_id():
    """获取当前 Agent 的 ID（从环境变量或生成默认值）"""
    agent_id = os.environ.get('CROSS_AGENT_MEMORY_AGENT_ID')
    if agent_id:
        return agent_id
    session_id = os.environ.get('HERMES_SESSION_ID')
    if session_id:
        return f"hermes_{session_id.split('_')[0]}"
    import socket
    hostname = socket.gethostname()
    return f"agent_{hostname[:8]}"


def build_report(memory_base, db_config, agent_id, mysql_status, jsonl_status, check_only):
    """组装状态报告（stdout 纯 JSON）"""
    if mysql_status['available']:
        storage_type = 'mysql'
    elif jsonl_status['available']:
        storage_type = 'jsonl'
    else:
        storage_type = 'none'

    report = {
        'timestamp': datetime.now().isoformat(),
        'agent_id': agent_id,
        'memory_base': str(memory_base),
        'storage_type': storage_type,
        'mysql': mysql_status,
        'jsonl': jsonl_status,
        'db_config': {
            'host': db_config['host'],
            'port': db_config['port'],
            'database': db_config['database'],
        },
    }
    return report, storage_type


def main():
    parser = argparse.ArgumentParser(description="cross-agent-memory 存储层探测")
    parser.add_argument('--check-only', action='store_true',
                        help='仅检查存储状态，不执行初始化（适用于 CI / 状态巡检）')
    args = parser.parse_args()

    memory_base = MEMORY_BASE
    db_config = load_db_config(memory_base)
    agent_id = get_agent_id()

    mysql_status = check_mysql(db_config)
    jsonl_status = check_jsonl(memory_base)

    # 非 check-only 模式：若两者都不可用，自动初始化 JSONL
    if not args.check_only and not mysql_status['available'] and not jsonl_status['available']:
        init_result = init_jsonl(memory_base)
        jsonl_status = check_jsonl(memory_base)
        print(f"[init] {init_result['message']}", file=sys.stderr)

    report, storage_type = build_report(
        memory_base, db_config, agent_id, mysql_status, jsonl_status, args.check_only
    )

    # stdout：纯 JSON，供程序解析
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # stderr：人类可读摘要
    summary = [
        "",
        "=" * 60,
        "cross-agent-memory 存储层探测报告",
        "=" * 60,
        f"Agent ID: {agent_id}",
        f"Memory Base: {memory_base}",
        f"当前存储层: {storage_type}",
        f"MySQL: {mysql_status['message']}",
        f"JSONL: {jsonl_status['message']}",
    ]
    if mysql_status['available']:
        summary.append("")
        summary.append(f"[推荐] 使用 MySQL 共享存储，数据库: {db_config['database']}")
        if mysql_status.get('database_exists'):
            summary.append(f"  表: {', '.join(mysql_status['tables'])}")
    else:
        summary.append("")
        summary.append("[提示] MySQL 不可用，使用 JSONL 本地存储")
        if args.check_only and not jsonl_status['available']:
            summary.append("  --check-only 模式：未初始化（运行不带该参数的命令可自动初始化）")
        else:
            summary.append("  如需共享记忆，请配置 MySQL 连接参数")
    for line in summary:
        print(line, file=sys.stderr)

    return report


if __name__ == '__main__':
    main()
