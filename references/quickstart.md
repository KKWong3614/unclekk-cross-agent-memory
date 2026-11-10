# cross-agent-memory 快速入门指南

**适用对象**：所有支持 Python 的 AI Agent（不限特定框架）

---

## 一句话总结

**加载 skill → 运行探测 → 自动共享记忆（默认 JSONL 零依赖，可选升级 MySQL）**

---

## 3 步开箱（默认 JSONL，零数据库依赖）

### 步骤 1：放置 skill 目录

```bash
# 把 cross-agent-memory 放到你的 Agent 的 skills 目录下（任选其一，其他系统同理）
~/.workbuddy/skills/cross-agent-memory/     # WorkBuddy
~/.hermes/skills/cross-agent-memory/        # Hermes
```

无需额外安装：默认 JSONL 本地存储**零数据库依赖**，开箱即用。

### 步骤 2：存储目录（可选）

记忆目录在首次写入/探测时**自动创建**，无需手工建目录。默认位置（三级解析）：
1. 显式 `CROSS_AGENT_MEMORY_BASE` 环境变量（最高优先）；
2. 已存在 `~/.hermes/memory_data` 则沿用（兼容旧数据）；
3. 否则自动落到中性位置 `~/.cross-agent-memory/memory_data`。

自定义示例：
```bash
export CROSS_AGENT_MEMORY_BASE="/path/to/my_memory"   # Linux/macOS
# set CROSS_AGENT_MEMORY_BASE=D:\my_memory            # Windows
```

### 步骤 3：设置 agent_id 并探测

```bash
# 设置唯一 agent_id（必须！多 Agent 共享时用于隔离与归属）
export CROSS_AGENT_MEMORY_AGENT_ID=workbuddy_main

# 执行探测（stdout 输出纯 JSON 状态；JSONL 未初始化会自动初始化）
python scripts/detect_storage.py

# 验证写入 / 读取
python scripts/shared_memory.py write --agent workbuddy_main --fact "测试写入"
python scripts/shared_memory.py read --query "测试" --limit 5
```

> **存储行为说明**：`detect_storage.py` 按 `CROSS_AGENT_MEMORY_DB_*` 环境变量 > `memory_data/db_config.json` > 内置默认值（127.0.0.1:3306/root）的顺序探测 MySQL；**连不上就静默使用 JSONL**——零配置机器的体验就是纯 JSONL，不需要任何数据库操作。

---

## 可选：升级到 MySQL（多机共享 / 大数据量）

> 仅当需要跨机器共享或高频并发写入时才需要；单机使用 JSONL 完全够用。

### 1. 复制配置模板到 memory_data 根目录

```bash
# 目标目录即上面解析出的 memory_data 根（如 ~/.cross-agent-memory/memory_data/）
cp references/shared-db-config-template.json <memory_data根目录>/db_config.json
```

### 2.（可选）用环境变量覆盖连接参数

```bash
export CROSS_AGENT_MEMORY_DB_HOST=127.0.0.1
export CROSS_AGENT_MEMORY_DB_PORT=3306
export CROSS_AGENT_MEMORY_DB_USER=root
export CROSS_AGENT_MEMORY_DB_PASSWORD=
export CROSS_AGENT_MEMORY_DB_NAME=cross_agent_memory
```

### 3. 建库并迁移

```bash
mysql -u root -e "CREATE DATABASE cross_agent_memory CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
python scripts/migrate_to_mysql.py --dry-run   # 先预览
python scripts/migrate_to_mysql.py             # 正式迁移
```

连接配置细节与建库语句见 `mysql-integration-guide.md`。

---

## 配置优先级

| 优先级 | 配置源 | 说明 |
|--------|--------|------|
| 1 (最高) | `CROSS_AGENT_MEMORY_DB_*` 环境变量 | 启动时设置，覆盖配置文件 |
| 2 | `<memory_data>/db_config.json` | memory_data 根目录下的配置文件 |
| 3 (最低) | 默认值 | `127.0.0.1:3306`, `root`, 空密码（连不上自动用 JSONL） |

---

## agent_id 命名规范

| 系统 | 推荐 agent_id 前缀 | 示例 |
|------|-------------------|------|
| Hermes | `hermes_` | `hermes_main`, `hermes_pm` |
| WorkBuddy | `workbuddy_` | `workbuddy_main`, `workbuddy_pm` |
| 其他 Agent | `<系统名>_` | `agentx_main` |

**重要**：每个 Agent 系统必须使用唯一的 `agent_id` 前缀，避免冲突！

---

## 常见问题

### Q1: 必须装 MySQL 吗？
**A**: 不必须。默认 JSONL 零依赖开箱即用；只有跨机器共享或大数据量才建议升级 MySQL。

### Q2: MySQL 连接失败怎么办？
**A**: 自动降级 JSONL，不影响使用。若确需 MySQL，按序检查：服务运行（`mysqladmin -u root ping`）→ 库已建（`SHOW DATABASES`）→ `<memory_data>/db_config.json` 位置正确（注意是 memory_data 根目录，不是 `~/.workbuddy`）。

### Q3: 如何迁移本地 JSONL 数据到 MySQL？
**A**: `python scripts/migrate_to_mysql.py --dry-run` 预览后正式迁移。

### Q4: 多个 Agent 写入同一事实会怎样？
**A**: 自动去重合并，`agents` 列表追加新 Agent，不会产生重复记录。

---

## 架构概览

```
              默认：JSONL 共享文件存储（各 Agent 指向同一 memory_data 目录）
 ┌────────────────┐        ┌────────────────────────┐        ┌────────────────┐
 │  Agent A       │ ─写入─▶ │  shared/<日期>.jsonl    │ ◀─读取─ │  Agent B       │
 │  agent_a_main  │         │  + index.jsonl          │        │  agent_b_main  │
 └────────────────┘         │  （filelock 并发锁保护） │        └────────────────┘
                             └───────────┬────────────┘
                                         │ 可选升级（数据量 > 1 万条 / 跨机器共享）
                              ┌──────────▼───────────┐
                              │ MySQL cross_agent_    │
                              │ memory（3306）        │
                              └───────────────────────┘
```

---

## 文件清单

| 文件 | 用途 |
|------|------|
| `scripts/detect_storage.py` | 首次探测与 JSONL 自动初始化 |
| `scripts/shared_memory.py` | 记忆读写 CLI（write/read/refine/stats/verify） |
| `scripts/mcp_server.py` | MCP Server（8 个工具） |
| `scripts/migrate_to_mysql.py` | JSONL → MySQL 迁移 |
| `references/shared-db-config-template.json` | MySQL 配置模板（仅升级时用） |

---

**版本**: v6.26.3 | **更新日期**: 2026-11-04
