# MySQL 集成指南 — Pitfalls & Patterns

**适用场景**: 在 Windows 上部署 MySQL 8.0 作为 cross-agent-memory 的本地数据主权存储层。

---

## 安装 (Windows 原生)

```bash
# 1. 下载 MySQL 8.0 ZIP 免安装版
curl -L "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-winx64.zip" -o /tmp/mysql-8.0.36-winx64.zip

# 2. 解压到 D:\mysql-8.0.36-winx64

# 3. 创建 my.ini 配置文件
cat > /d/mysql-8.0.36-winx64/my.ini << 'EOF'
[mysqld]
basedir=D:/mysql-8.0.36-winx64
datadir=D:/mysql-8.0.36-winx64/data
port=3306
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default-authentication-plugin=mysql_native_password
max_connections=100
innodb_buffer_pool_size=256M

[client]
default-character-set=utf8mb4
EOF

# 4. 初始化（无密码模式）
/d/mysql-8.0.36-winx64/bin/mysqld.exe --initialize-insecure --console

# 5. 启动（后台模式，不能用 & 语法）
# 必须使用 terminal(background=true) 而非 &
```

## Pitfalls

### P1: Windows 无法安装为服务
- `mysqld --install` 需要管理员权限，通常被拒绝
- 解决: 使用 `terminal(background=true)` 直接后台启动
- **不要**尝试 `net start MySQL`，会失败

### P2: pymysql Decimal 不可 JSON 序列化
- MySQL 返回的 DECIMAL 类型 (如 confidence 字段) 不能直接 `json.dumps()`
- 解决: 定义 `_convert_decimal()` 辅助函数
```python
from decimal import Decimal
from datetime import datetime, date

def _convert_decimal(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimal(item) for item in obj]
    return obj
```

### P3: pymysql datetime 不可 JSON 序列化
- MySQL 返回的 TIMESTAMP/DATETIME 类型不能直接 `json.dumps()`
- 解决: 同 P2，在 `_convert_decimal()` 中处理

### P4: 查询列必须与表结构匹配
- memories 表中没有 `agents` 列，agents 通过 `memory_agents` 关联表存储
- 查询时需要 `LEFT JOIN memory_agents` 并使用 `GROUP_CONCAT(ma.agent_id)`
- 写入时需要分别写入 `memories` 和 `memory_agents` 两个表

### P5: JSONL 文件中的 agents 是数组，MySQL 中是关联表
- JSONL 格式: `{"agents": ["agent1", "agent2"], ...}`
- MySQL 格式: `memory_agents` 表，每行一个 (fact_id, agent_id) 对
- 迁移时需要遍历 agents 数组，逐个插入关联表

### P6: 环境变量优先级
- 配置优先级: 环境变量 > 配置文件 > 默认值
- 环境变量名: `CROSS_AGENT_MEMORY_DB_HOST`, `CROSS_AGENT_MEMORY_DB_PORT`, 等
- 配置文件: `~/.hermes/memory_data/db_config.json`（新环境无该目录时智能默认落在 `~/.cross-agent-memory/memory_data/db_config.json`，可用 `CROSS_AGENT_MEMORY_BASE` 覆盖）

### P7: Python 环境问题 (SRE module mismatch)
- 系统 Python 可能有 SRE module mismatch 错误
- 解决: 使用项目虚拟环境（venv）内的 Python 运行脚本

---

## 数据库 Schema

```sql
-- 记忆主表
CREATE TABLE memories (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  fact_id VARCHAR(64) NOT NULL UNIQUE,
  fact TEXT NOT NULL,
  original TEXT,
  agent_id VARCHAR(64) NOT NULL,
  level ENUM('core', 'important', 'normal', 'temp') DEFAULT 'normal',
  confidence DECIMAL(3,2) DEFAULT 0.50,
  tags JSON,
  refinement_method VARCHAR(32) DEFAULT 'local',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_agent_id (agent_id),
  INDEX idx_level (level),
  INDEX idx_created_at (created_at)
);

-- Agent 关联表（支持多 Agent 共享同一记忆）
CREATE TABLE memory_agents (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  fact_id VARCHAR(64) NOT NULL,
  agent_id VARCHAR(64) NOT NULL,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_fact_agent (fact_id, agent_id),
  FOREIGN KEY (fact_id) REFERENCES memories(fact_id) ON DELETE CASCADE
);

```

> **注意**：审计日志（audit）不走 MySQL，由代码 `log_audit()` 写入本地 JSONL 文件（`AUDIT_DIR/<日期>.jsonl`，可用 `CROSS_AGENT_MEMORY_BASE` 自定义根目录）。MySQL 层只有 `memories` 和 `memory_agents` 两张表。

---

## MCP 集成模式

所有 MCP 工具函数采用 **MySQL 优先，文件降级** 模式：

```python
if HAS_MYSQL:
    try:
        store = get_store()
        result = store.xxx(...)
        if result["success"]:
            return result
    except Exception as e:
        print(f"[WARN] MySQL 异常，降级到文件: {e}", file=sys.stderr)

# 文件存储降级路径
_ensure_dirs()
# ... 原有文件存储逻辑 ...
```
