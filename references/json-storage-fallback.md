# JSONL 文件存储（默认后端，MySQL 不可用时自动降级）

**适用场景**: MySQL 无法安装、或单机轻量场景时的默认存储后端。纯 Python 标准库实现，零数据库依赖，首次加载自动初始化。

> **v6.26 勘误**：旧版文档描述的 `memory.json / skills.json / sessions.json` 单文件 JSON 模型已**废弃**，当前版本从不创建这三个文件。真实存储结构如下。

## 真实文件结构

```
/path/to/memory_data/          # 智能默认：已存在 ~/.hermes/memory_data 则沿用（兼容旧数据），
                               # 否则落到 ~/.cross-agent-memory/memory_data（CROSS_AGENT_MEMORY_BASE 可覆盖）
├── daily/<日期>.jsonl         # 原始记忆（按日期追加）
├── shared/<日期>.jsonl        # 共享事实（提炼后；每行一个 JSON 对象）
├── refined/                   # LLM 提炼缓存
├── audit/<日期>.jsonl         # 操作审计日志
├── index.jsonl                # 全局索引（fact_id → 文件路径）
├── config.json                # 初始化时生成的配置
└── trash/<时间戳>/            # memory_delete 删除前备份（误删可恢复）
```

## 记录格式（shared/<日期>.jsonl，每行一个 JSON 对象）

```json
{
  "fact_id": "a1b2c3d4e5f60718",
  "fact": "提炼后的事实描述",
  "original": "用户原始输入",
  "agents": ["workbuddy_main"],
  "date": "2026-11-03",
  "refined_at": "2026-11-03T15:00:00",
  "tags": ["示例"],
  "confidence": 0.5,
  "level": "normal",
  "refinement_method": "local"
}
```

## 初始化与验证

```bash
python scripts/detect_storage.py               # 首次加载自动初始化（stdout 为纯 JSON）
python scripts/detect_storage.py --check-only  # 仅检查，不初始化（CI / 状态巡检）
python scripts/verify.py memory                # 记忆完整性验证
```

## 并发安全

- 写入 / 删除 / 批量提炼统一走 `memory.lock` 文件锁（依赖 `filelock`）
- `filelock` 缺失时自动降级为无锁模式（`HAS_FILE_LOCK=False`，仅打印警告，不崩溃）——单 Agent 串行或低频写入不受影响；**多 Agent 高频同写同一存储时建议安装 filelock**

## 检索说明

- 基于字符 n-gram 相似度匹配（非全文索引 FTS），`--query` 建议 2 字以上关键词
- 过短查询可能不命中，属正常去噪行为，不是故障

## 限制

- **单机**: 跨设备同步需自备方案（如网盘 / Obsidian Vault 同步 memory_data 目录）
- **无事务**: 并发一致性依赖文件锁（见上），不是数据库级事务
- **容量**: 建议单机 < 10,000 条；数据量更大或高频并发写入时升级 MySQL

## 迁移到 MySQL

```bash
pip install "pymysql>=1.1,<2"
python scripts/migrate_to_mysql.py --dry-run   # 先预览
python scripts/migrate_to_mysql.py             # 正式迁移（连接配置见 mysql-integration-guide.md）
```

## 相关文档

- SKILL.md「失败模式速查」：常见失败场景与一线修复 / 兜底对照表
- EXAMPLES.md「四、故障排除」：排错命令与恢复步骤
- references/mysql-integration-guide.md：升级 MySQL 的连接配置与建库语句
