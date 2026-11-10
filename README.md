# UncleKK Cross-Agent Memory 跨Agent记忆共享

多个 AI Agent 共用同一份记忆库：默认 JSONL 零依赖起步，可选升级 MySQL，agent_id 隔离互不串台，先读后动防丢数据。
Multiple AI agents share one memory store: JSONL by default with zero dependencies, optional MySQL upgrade, agent_id isolation, read-before-write safety.

## 目录结构（与实际文件一致）

```
cross-agent-memory/
├── SKILL.md              # 技能定义（先读这份）
├── README.md             # 本文件
├── EXAMPLES.md           # CLI 与 MCP 调用示例
├── CHANGELOG.md          # 版本历史
├── AGENTS.md             # Agent 执行准则（先读后动）
├── references/           # 深入文档（按需阅读）
│   ├── quickstart.md             # 快速上手
│   ├── mysql-integration-guide.md # MySQL 集成要点
│   ├── mysql-windows-setup.md     # Windows 安装 MySQL
│   ├── json-storage-fallback.md   # JSONL 降级说明
│   ├── read-before-write-guide.md # 先读后动指南
│   └── shared-db-config-template.json # 共享库配置模板
└── scripts/
    ├── mcp_server.py      # MCP Server（8 个工具）
    ├── shared_memory.py   # CLI 主程序（write/read/refine/verify/stats）
    ├── detect_storage.py  # 首次加载探测与 JSONL 强制初始化
    ├── init_jsonl.py      # JSONL 初始化
    ├── migrate_to_mysql.py # JSONL → MySQL 迁移
    ├── mysql_store.py     # MySQL 数据层（不可用时降级 JSONL）
    ├── shared_utils.py    # 共享工具函数
    ├── file_lock.py       # 跨平台文件锁
    ├── llm_client.py      # LLM 提炼客户端（可选）
    ├── sync_copies.py     # 多副本同步（规范源→其余副本，支持 --dry-run/--purge/--quarantine）
    └── verify.py          # 完整性验证（agents/memory/health/backup）
```

## 快速使用

### 1. 首次加载（强制初始化 JSONL）

```bash
python scripts/detect_storage.py          # 初始化并输出状态（stdout 为纯 JSON）
python scripts/detect_storage.py --check-only  # 仅检查
```

### 2. MCP Server（stdio 模式，8 个工具）

以下命令均在**技能根目录**执行：

```bash
python scripts/mcp_server.py
```

工具清单：`memory_write` / `memory_search` / `memory_read_all` / `memory_boot_context` / `memory_stats` / `memory_delete` / `memory_verify` / `memory_refine_batch`

### 3. CLI（与 MCP 同一存储层）

```bash
python scripts/shared_memory.py write --fact "今天买入了比亚迪股票" --agent main
python scripts/shared_memory.py read --query "比亚迪"
python scripts/shared_memory.py refine --days 7
python scripts/shared_memory.py stats
python scripts/shared_memory.py verify
```

### 4. 升级到 MySQL（可选）

```bash
# 依赖：pip install pymysql
python scripts/migrate_to_mysql.py --dry-run   # 先预览
python scripts/migrate_to_mysql.py             # 正式迁移
python scripts/verify.py health                  # 健康检查
```

## 最小依赖

```bash
# JSONL 模式（默认，零数据库）
pip install fastmcp mcp filelock
# MySQL 模式追加
pip install pymysql
# LLM 提炼（可选）
pip install openai anthropic
```

注意：`mcp` 与 OpenTelemetry 库冲突，请使用 `pip install mcp==1.0.0`。

## 环境变量

```bash
# MySQL 连接（全部可选，默认 localhost:3306 / root / 空密码）
export CROSS_AGENT_MEMORY_DB_HOST=127.0.0.1
export CROSS_AGENT_MEMORY_DB_PORT=3306
export CROSS_AGENT_MEMORY_DB_USER=root
export CROSS_AGENT_MEMORY_DB_PASSWORD=your_password
export CROSS_AGENT_MEMORY_DB_NAME=cross_agent_memory

# Agent 身份（跨系统共享时必设，各系统唯一）
export CROSS_AGENT_MEMORY_AGENT_ID=workbuddy_main

# 存储根目录覆盖（智能默认：沿用 ~/.hermes/memory_data，否则中性 ~/.cross-agent-memory/memory_data）
export CROSS_AGENT_MEMORY_BASE=/your/path/memory_data
```

## 注意事项

- 先读后动：写入/更新/删除前必须先读取（详见 `AGENTS.md` 与 SKILL.md）
- 跨系统共享：各系统配置指向同一数据库 + 各自唯一 agent_id（模板见 `references/shared-db-config-template.json`）
- MySQL 不可用时自动降级 JSONL，不影响基本功能
- 当前版本 v6.26.3（2026-11-04）；逐版本修复明细见 CHANGELOG.md。
- v6.26.0（2026-11-03）Hermes 独立审计闭环 + 跨平台门禁：新增 GitHub Actions 三 OS × 双 Python 门禁；修复 sync_copies 跨平台硬编码路径与 tests 路径相对化；持久化断言 62 → 63（补 T-11 filelock 缺失优雅降级）；`memory_write` 返回新增 `storage` 字段；敏感检测单源化。审计结论 CONDITIONAL PASS（63/63 测试全 PASS）。
- v6.26.1（2026-11-03，**未发布**）TRACE 4.6 复检改进：references/json-storage-fallback.md 整篇重写为真实 JSONL 存储结构（原文档描述的 memory.json/skills.json/sessions.json 已废弃）；修正 verify.py 命令路径（scripts/ 前缀）与 README `refine --agent` 失效参数；README 目录树补 sync_copies.py；write/delete 失败消息改短文案、原始异常挪入 hint；失败模式/故障排除/存储文档三方互链；mysql-integration-guide 补智能默认路径说明。
- v6.26.2（2026-11-04，**未发布**）TRACE 复评改进：quickstart.md 全量重构（JSONL 主架构图 + 3 步开箱 + MySQL 移入可选分支 + db_config 路径纠错）；失败模式速查删除未实现的「重试 3 次指数退避」声明；补存储探测顺序说明（连不上静默落 JSONL，零配置=纯 JSONL）；多副本同步段去本机硬路径并标注作者机用；detect_storage SKILL_VERSION 常量同步；README 版本历史瘦身；新增「场景→工具速配」表；EXAMPLES 补 detect_storage 真实输出示例。
- 完整版本历史（v6.22 起逐条修复明细）见 CHANGELOG.md。
