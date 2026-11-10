# cross-agent-memory 使用示例

所有示例均与 v6.26.3 脚本实际接口核对过，CLI 输出为真实运行结果。两条使用路径：MCP 工具（Agent 调用）与 CLI（命令行），共用同一存储层。v6.22 起，`memory_delete` 删除前先备份到 `trash/`（可恢复），搜索/读取/启动注入支持 `scope="mine"` 严格隔离。

## 一、MCP 工具路径（Agent 内调用）

通过 `python scripts/mcp_server.py` 启动 stdio MCP Server 后，可调用 8 个工具：

### 1. 写入记忆（memory_write）

```
memory_write(agent_id="workbuddy_main", fact="KK偏好简洁回复")
```

写入前会自动查重：无重复才写入，有重复则合并（先读后动）。

### 2. 搜索记忆（memory_search）

```
memory_search(query="KK", agent_id="workbuddy_main", days=30)
```

### 3. 全量读取（memory_read_all）

```
memory_read_all(agent_id="workbuddy_main", days=7)
```

### 4. 启动注入（memory_boot_context）

```
memory_boot_context(agent_id="workbuddy_main", max_items=20)
```

启动时调用，自动注入 core/important 级记忆，解决「失忆」问题。

### 5. 统计 / 删除 / 验证 / 提炼

```
memory_stats(days=30)
memory_delete(fact_id="abc123", agent_id="workbuddy_main")   # 删除前先确认存在并备份到 trash/
memory_search(query="KK", agent_id="workbuddy_main", scope="mine")  # 仅返回本 Agent 的记忆
memory_verify(agent_id="workbuddy_main")
memory_refine_batch(agent_id="workbuddy_main", days=7)       # 可选功能：默认本地规则提炼（无需任何国外服务）；仅在设置 LLM_PROVIDER=openai/anthropic 时才调用外部 LLM
```

## 二、CLI 路径（命令行）

以下命令均在**技能根目录**执行：

```bash
# 写入（自动查重合并）
python scripts/shared_memory.py write --fact "今天买入了比亚迪股票" --agent main

# 读取/检索
python scripts/shared_memory.py read --query "比亚迪"

# LLM 批量提炼（可选，需配置 LLM Provider；agent_id 自动取自各条记录，CLI 无 --agent 参数）
python scripts/shared_memory.py refine --days 7

# 统计与完整性验证
python scripts/shared_memory.py stats
python scripts/shared_memory.py verify
```

### CLI 实际输出示例（v6.26.0 真实运行）

```text
$ python scripts/shared_memory.py write --fact "比亚迪是新能源汽车品牌" --agent main
  事实: 比亚迪是新能源汽车品牌

$ python scripts/shared_memory.py read --query "新能源"
找到 1 条相关记忆:

  [2026-11-03] main
  事实: 比亚迪是新能源汽车品牌

$ python scripts/shared_memory.py stats
近 30 天记忆统计:
  总事实数: 2
  参与Agent数: 1
  各Agent贡献:
    main: 2 条

$ python scripts/shared_memory.py verify
==================================================
跨Agent记忆完整性验证
==================================================
  目录结构: [OK]
  共享事实: 2 条
  索引条目: 2 条
  今日审计: 2 条记录
  参与共享的Agent: 1 个
    main
==================================================
[OK] 验证完成
```

> 注：搜索基于 n-gram 相似度，`--query` 建议用 2 字以上关键词（如「新能源」「简洁」）；过短的串（如「KK」）可能不命中，属正常去噪行为。

### detect_storage 真实输出示例（--check-only，stdout 为纯 JSON）

```json
{
  "timestamp": "2026-11-04T08:28:26",
  "agent_id": "agent_PC-20230",
  "memory_base": "C:\\Users\\<你的用户名>\\.hermes\\memory_data",
  "storage_type": "jsonl",
  "mysql": { "available": false, "message": "pymysql 未安装，无法连接 MySQL" },
  "jsonl": { "available": true, "records": 47, "message": "JSONL 本地存储就绪，47 条共享事实" },
  "db_config": { "host": "127.0.0.1", "port": 3306, "database": "cross_agent_memory" }
}
```

> 同一命令的 stderr 输出人类可读摘要（探测报告 + 「MySQL 不可用，使用 JSONL 本地存储」提示）。MySQL 探测失败属正常降级路径，不是错误。

## 三、存储配置

### 0. 存储路径（跨平台智能默认，无需手动建目录）

记忆根目录按以下顺序解析（v6.24.0 起）：

1. 显式 `CROSS_AGENT_MEMORY_BASE` 环境变量（最高优先，适合换设备/自定义位置）
2. 若已存在 `~/.hermes/memory_data`（兼容旧数据，免迁移）则沿用
3. 否则自动落到中性位置 `~/.cross-agent-memory/memory_data`（首次在全新机器/无 Hermes 环境直接可用，**无需手工建目录**）

设置示例：`export CROSS_AGENT_MEMORY_BASE="/path/to/my_memory"`（Linux/macOS）或 `set CROSS_AGENT_MEMORY_BASE=D:\my_memory`（Windows）。

### 1. 首次加载探测（强制初始化 JSONL）

```bash
python scripts/detect_storage.py               # 初始化并输出状态
python scripts/detect_storage.py --check-only  # 仅检查
```

stdout 为纯 JSON（`storage_type` / `mysql` / `jsonl` 字段），stderr 为人类可读摘要。

### 2. 升级到 MySQL

```bash
# 环境变量（或用 references/shared-db-config-template.json 配置文件）
export CROSS_AGENT_MEMORY_DB_HOST=127.0.0.1
export CROSS_AGENT_MEMORY_DB_PORT=3306
export CROSS_AGENT_MEMORY_DB_USER=root
export CROSS_AGENT_MEMORY_DB_PASSWORD=your_password
export CROSS_AGENT_MEMORY_DB_NAME=cross_agent_memory

# 迁移（先预览再执行）
python scripts/migrate_to_mysql.py --dry-run
python scripts/migrate_to_mysql.py

# 健康检查
python scripts/verify.py health
```

### 3. 跨系统共享（Hermes + WorkBuddy）

1. 各系统的配置指向同一 MySQL 库（模板：`references/shared-db-config-template.json`）
2. 各自设置唯一 agent_id 前缀：`hermes_main` / `workbuddy_main`
3. 建库：`CREATE DATABASE cross_agent_memory CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`

## 四、故障排除

> 常见失败模式速查表见 SKILL.md「失败模式速查」；存储结构与恢复机制详见 references/json-storage-fallback.md。

| 现象 | 处理 |
|------|------|
| 数据库连接失败 | `python scripts/detect_storage.py` 查看状态；MySQL 不可用会自动降级 JSONL |
| 文件锁残留 | 记忆根目录（默认 `~/.hermes/memory_data/` 或中性 `~/.cross-agent-memory/memory_data/`，可用 `CROSS_AGENT_MEMORY_BASE` 覆盖）下的 `*.lock` 文件；并发锁依赖 `filelock`，缺失时自动降级为无锁（仅警告，不报错） |
| 数据异常 | `python scripts/verify.py memory`（记忆完整性）/ `python scripts/verify.py backup`（先备份再操作） |
| Agent 在线状态 | `python scripts/verify.py agents` |

## 最佳实践

1. **先读后动**：写入/更新/删除前必须先读取
2. **agent_id 隔离**：每个 Agent 系统使用唯一 agent_id 前缀
3. **分级存储**：核心记忆用 `core`，临时记忆用 `temp`
4. **定期备份**：升级或批量操作前先 `python scripts/verify.py backup`
5. **先预览再迁移**：`migrate_to_mysql.py --dry-run` 确认无误再正式执行

---

**文档版本**: v6.26.3 ｜ **最后更新**: 2026-11-04
