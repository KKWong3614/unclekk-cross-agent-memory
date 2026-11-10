# v6.26.3 (2026-11-04) — TRACE 复评改进（命令可直跑统一 + MySQL 连接自动重试；未发布）

针对 v6.26.2 测评短板（命令示例不能直接照抄 / 文件引用无效 / 维护历史冗长 / 无自动重试）逐项实跑查证后修复：

**修复 1 · cwd 约定统一（C「步骤互相矛盾」/ E「照抄报错」根因）**：README 与 EXAMPLES 原用「`cd scripts` + 裸 `python shared_memory.py`」写法，而 SKILL/quickstart 用「技能根目录 + `python scripts/...`」——用户单抄一行裸命令即报 file not found。全部统一为**技能根目录 + `python scripts/...`**（含 EXAMPLES 真实输出示例中的命令行），并在两处命令块前显式标注「以下命令均在技能根目录执行」。

**修复 2 · requirements.txt 幽灵引用（「文件仅在文档中出现」实锤）**：SKILL.md §3 引用 `requirements.txt`，但 SkillHub 白名单只放行 .md/.py/.json/.toml，**发布包中不存在该文件** → 改为自包含表述「依赖版本以本节清单为准（本清单即权威声明）」。文档提及的 scripts/references/tests 文件已机械核对全部存在。

**修复 3 · MySQL 连接自动重试（R 运行稳定性「偶发故障不会自动重试」）**：`mysql_store._get_connection()` 连接失败自动重试 1 次（1s 退避）再抛 ConnectionError，吸收瞬时网络/服务抖动；重试仍失败由调用方降级 JSONL，行为向后兼容。P1-7 无 pymysql 守卫与 JSONL 路径不受影响。

**修复 4 · README 巨型版本行瘦身（渐进式披露「维护历史冗长」）**：「当前版本」行原为 530 字符的 v6.22.x 起历史长文 → 精简为「当前版本 v6.26.3（2026-11-04）；逐版本修复明细见 CHANGELOG.md」。

**修复 5 · sync_copies 作者机路径降噪**：KNOWN_COPIES 注释明确「外部用户可清空此清单——工具仍可用（--source 指定规范源）」。

**本轮不改（如实披露）**：①CLI 错误「提示后退出」方式不统一——统一化收益低于回归风险；②可选 LLM 提炼依赖海外服务——既有文档已披露且属外部依赖；③反模式提醒分散——v6.26.1 已建三方互链，独立聚合清单收益待测评再验证。

**验证**：py_compile 全过；文档提及文件机械核对全部存在；版本八处一致（VERSION/SKILL frontmatter/detect_storage 常量/CHANGELOG 顶/README 当前版本/quickstart+EXAMPLES footer/tests docstring=6.26.3）；63 项断言由 Hermes 审计环节实跑复验（含 mysql_store 重试改动回归）。**本版本暂不发布（KK 指令），线上仍为 v6.26.2。**

---

# v6.26.2 (2026-11-04) — TRACE v6.26.1 复评改进（旧说明清理 + 开箱路径重构；未发布）

针对 v6.26.1 测评短板（输出准确性 4.0 / 开箱即用 4.0 / 文档质量 4.5 / 国内适配 4.5）逐项实跑查证后修复，共 8 类实证偏差：

**修复 1（最重）· references/quickstart.md 全量重构**：①架构图仍是旧「Hermes ↔ MySQL ↔ WorkBuddy」纯 MySQL 设计，与「默认 JSONL」当前实现矛盾 → 改为「JSONL 共享文件存储为主 + 可选升级 MySQL」新架构图；②「5 步快速接入」在零依赖开箱步骤中诱导新用户复制 db_config.json / 配 DB 环境变量 → 重构为「3 步开箱（纯 JSONL）+ 可选升级 MySQL 分支」；③ db_config.json 示例路径写为 `~/.workbuddy/memory_data/`（实际加载位置是 memory_data 根目录）→ 全部纠正为 `<memory_data>/db_config.json` 并在 Q2 中显式提示；④ 版本 footer v6.26.0 → v6.26.2。

**修复 2 · SKILL.md「重试 3 次指数退避」虚假声明**：代码中无任何重试实现（grep 全 scripts 仅 file_lock 锁等待），失败模式速查如实改为「返回结构化错误（error/message/hint）；MySQL 模式自动降级 JSONL」。

**修复 3 · 存储探测顺序补文档（R 功能完善性「默认存储与说明不一致」根因）**：SKILL.md §10 补明 `detect_storage` 探测顺序（env > db_config.json > 内置默认 127.0.0.1:3306/root）与「连不上即静默落 JSONL，零配置机器体验为纯 JSONL」——此前文档只说"默认 JSONL"，未讲清会主动探测 MySQL 的行为。

**修复 4 · SKILL.md「多副本同步」段去本机路径依赖**：原文含 `C:\Users\Administrator\.workbuddy\...` 与 `D:\hermes\...` 硬路径及作者机工作流长文 → 压缩并标注「作者机维护用，SkillHub 用户可忽略」，副本清单指向 sync_copies.py 顶部注释。

**修复 5 · detect_storage.py 版本常量滞后**：`SKILL_VERSION = "6.26.0"`（代码内常量从未随版本更新）→ 6.26.2，注释标注与 SKILL.md/VERSION 三处同步。

**修复 6 · README 版本历史瘦身**：注意事项区挂 5 条长版本 bullet（v6.24/v6.25 大段历史）→ 只留 v6.26.0/v6.26.1 近两条 + 「完整历史见 CHANGELOG.md」，降低信息密度（C 渐进式披露）。

**修复 7 · SKILL.md 新增「场景 → 工具速配」表**（A 触发方式「缺少场景匹配指导」）：写入/检索/注入/统计/删除/体检/迁移 7 场景对应用法。

**修复 8 · EXAMPLES.md 补 detect_storage 真实输出示例**（C 文档质量「完整输出示例可补充」）：`--check-only` 实跑采集（stdout 纯 JSON + stderr 摘要说明），路径已脱敏；MySQL 探测失败标注为正常降级非错误。

**验证**：py_compile 全过；版本六处一致（VERSION / SKILL frontmatter / CHANGELOG 顶条目 / README 当前版本 / detect_storage SKILL_VERSION / quickstart+EXAMPLES footer=6.26.2）；63 项断言由 Hermes 审计环节实跑复验。**本版本暂不发布（KK 指令），线上仍为 v6.26.1。**

---

# v6.26.1 (2026-11-03) — TRACE 4.6 复检改进（文档-实际对齐 + 异常消息精简；未发布）

按 SkillHub TRACE 测评 4.6/5 短板（输出准确性 4.3 / 开箱即用度 4.3 / 结构清晰 4.5 / 异常处理 4.5）逐项实跑查证后修复，共 7 类实证偏差：

**修复 1（最重）· references/json-storage-fallback.md 整篇描述已废弃存储模型**：原文档教用户使用 `memory.json/skills.json/sessions.json` 单文件 JSON 模型——该模型 v6.22 已废弃、当前版本从不创建。整篇重写为真实 JSONL 结构（daily|shared/<日期>.jsonl + index.jsonl + audit/ + trash/）、记录格式、并发锁行为、n-gram 检索限制与 MySQL 迁移路径，并显式标注旧模型废弃声明。

**修复 2 · `verify.py` 命令路径错误（SKILL.md ×2 处 / README ×1 处）**：`python verify.py health/memory/backup` → `python scripts/verify.py ...`。verify.py 实际位于 scripts/，原文档命令照抄会直接 file not found。

**修复 3 · README CLI 示例 `refine --agent main`（参数不存在）**：v6.23.0 已实证 refine 子命令无 `--agent` 参数（agent_id 自动取自各条记录），EXAMPLES.md 当时已改、README 漏改 → 补齐为 `refine --days 7`。

**修复 4 · README 目录树漏列 sync_copies.py**：scripts/ 实际 11 个脚本，目录树只列 10 个 → 补 sync_copies.py（含 --dry-run/--purge/--quarantine 标注）。

**修复 5 · 异常消息不再以原始异常开头（R·异常处理）**：`memory_write` 的 write_failed 与 `memory_delete` 的 delete_failed 返回 message 原先内嵌原始 `{e}`，现改为短 message + 原始异常挪入 hint 字段——保留排查信息，不再污染主消息（对齐 detect_storage 的 _short_mysql_error 模式）。

**修复 6 · 反模式/FAQ 收拢互链（C·反模式与FAQ 内容分散）**：SKILL.md「失败模式速查」↔ EXAMPLES.md「四、故障排除」↔ references/json-storage-fallback.md 三方互链，消除孤岛。

**修复 7 · mysql-integration-guide.md 配置路径补智能默认说明**：db_config.json 路径补注「新环境默认 `~/.cross-agent-memory/memory_data/`，可用 `CROSS_AGENT_MEMORY_BASE` 覆盖」。

**本轮不改（如实披露）**：①渐进式披露「后半信息密」——结构性重排风险大于收益，本轮不动；②高并发建议装 filelock、可选 LLM 提炼需自备服务——两者均已文档化，且测评原文标注为「已知代价」而非缺陷。

**验证**：py_compile 全过；SKILL/README/EXAMPLES/AGENTS 命令逐条与实际文件核对一致；63 项持久化断言由 Hermes 审计环节实跑复验。**本版本暂不发布 SkillHub（KK 指令），线上仍为 v6.26.0。**

---

# v6.26.0 (2026-11-03) — Hermes 独立审计（CONDITIONAL PASS）闭环 + 跨平台门禁

承接 v6.25.0 后，按 `mcp__hermes__hermes_audit` 独立审计（CONDITIONAL PASS，63/63 测试全 PASS）所产生的发现逐项闭环：

**修复 1 · 版本漂移纠正（审计 H-1）**：VERSION/SKILL.md/EXAMPLES 已为 v6.26.0，但 CHANGELOG 与 README 顶条目仍停留在 v6.25.0。本条目补齐 v6.26.0 变更记录，README「当前版本」同步更正。

**修复 2 · 断言数对齐（审计 M-1）**：持久化断言 62 → **63**（补 T-11：filelock 缺失优雅降级），`test_28_assertions.py` 头部 docstring 由「28 项」更正为实际数。

**修复 3 · 敏感检测单源化（审计 M-2）**：`shared_memory._check_sensitive_info` 改为委托 `shared_utils.check_sensitive_info`（权威实现），消除两处重复检测器（审计原报「返回类型不一」经复核为误报——二者均返回 list，真实问题是重复实现）。

**修复 4 · 写入后端显式声明（审计 L-2）**：`memory_write` 返回值新增 `storage` 字段（mysql/jsonl），MySQL 失败降级文件时明确标注，消除读写降级不对称（write 的 try/except 降级分支本已存在，仅补返回字段）。

**新增 · GitHub Actions 跨平台门禁**：`.github/workflows/ci.yml` 新增三 OS（ubuntu/macos/windows）× 双 Python（3.11/3.12）矩阵，运行测试套件与依赖安装校验；修复 `sync_copies.py` 跨平台硬编码路径与 `tests/` 路径相对化（commit a18a4ce）。

---

# v6.25.0 (2026-11-03) — Hermes 独立审计（CONDITIONAL PASS）闭环

对 v6.24.0 调用 `mcp__hermes__hermes_audit` 独立审计，返回**有条件通过**，高严重度发现指向「`D:\hermes` 副本未净化、文档误将其当规范源导致反向覆盖」。实跑验证属实并已净化（`D:\hermes` → 11 核心脚本 / 6 用户文档，43+13 个无关文件可恢复隔离至 `.quarantine_dropped_20261103`），随即闭环以下文档/工具层发现：

**修复 1 · SKILL.md §3 依赖清单补 `uvicorn` + 锁定 `mcp==1.0.0`（审计中/低发现）**：原 `pip install fastmcp mcp filelock` 漏掉 `uvicorn`（fastmcp 以 uvicorn 拉起 MCP server，仅 MCP 模式必需），且未锁定 `mcp` 版本（与 OpenTelemetry 冲突，须 `mcp==1.0.0`）。更正为分模式安装说明，并新增「依赖优雅降级」披露：`filelock` 缺失时自动降级 `HAS_FILE_LOCK=False` 仅警告不崩溃（CLI/JSONL 模式照常），但并发写入无文件锁保护——高并发多 Agent 同写建议安装 `filelock`；`fastmcp`/`uvicorn` 缺失时 MCP server 启动先输出友好报错+安装指引再退出。

**修复 2 · 默认规范源由 `D:\hermes` 纠正为 `.workbuddy`（审计高严重度根因）**：`sync_copies.py` 默认源 `KNOWN_COPIES[0]` 原为 `D:\hermes`，但编辑与 SkillHub 发布源头实为 `C:\Users\Administrator\.workbuddy\skills\cross-agent-memory`——历史上「改完 .workbuddy 跑 sync 反而被 D:\hermes 旧内容覆盖」的反向覆盖根因。现 `.workbuddy` 置为默认规范源、`D:\hermes` 降为被同步的消费者之一；SKILL.md「多副本同步」守则同步纠正，并提示「若从 D:\hermes 编辑务必显式 `--source`」。

**修复 3 · `sync_copies.py` 新增 `--purge` 幂等净化（审计建议）**：同步后删除目标端 `CORE_DIRS`（scripts/references/tests）内、源端不存在的多余文件，用于消除反向同步/污染遗留；绝不触碰运行数据、`dev-notes/`、`.quarantine_*/` 等 CORE_DIRS 外内容。`--dry-run` 会预告拟删除数量。

**测试**：`tests/test_28_assertions.py` 59 → **62 项断言全 PASS**（新增 v6.25.0 的 T-8 §3 含 uvicorn 且锁 mcp==1.0.0 / T-9 默认规范源纠正为 .workbuddy / T-10 --purge 选项存在）。

---

# v6.24.0 (2026-11-03) — TRACE 复评（综合 4.6/5）跨平台开箱即用 + 文档去陈旧

上一轮发布 v6.23.0 后 TRACE 复评：综合 4.3→4.6（R 3.9→4.5 / A 4.5→4.9 / E 3.7→4.5，T·4.8 / C·4.5 持平）。剩余短板高度集中在两点——「说明过时/旧资料混在一起」与「跨平台开箱即用弱/安装环境不完整可能无法启动」——逐条实跑验伪后修复：

**修复 1 · 依赖缺失优雅降级（R「安装环境不完整时可能无法启动」实锤）**：`shared_memory.py` 第 32 行存在 `from filelock import FileLock` 硬导入，导致其下方 57-62 行的 `try/except` 优雅降级成为死代码——filelock 缺失时 CLI 直接 import 崩溃。移除硬导入，保留优雅降级（缺失则 `HAS_FILE_LOCK=False` 仅警告、不报错、照常读写）。`mcp_server.py` 的 `from fastmcp import` 缺失时改为输出友好报错 + `pip install fastmcp` 指引，而非一长串 traceback。

**修复 2 · 跨平台智能默认存储路径（A/E「换设备需手工修正/可能误解存储方式」实锤）**：原默认写死 `~/.hermes/memory_data`。改为三级解析——① 显式 `CROSS_AGENT_MEMORY_BASE` 优先；② 已存在 `~/.hermes/memory_data` 则沿用（兼容旧数据、免迁移）；③ 否则落到中性位置 `~/.cross-agent-memory/memory_data`（全新机器/无 Hermes 环境首次即可用，无需手工建目录）。`shared_utils.py` 与 `shared_memory.py` 同步。

**修复 3 · references 清理内部文档出发布包（C/E「资料稍多、新旧混在一起」实锤）**：references/ 原含 18 个文件，其中 13 个为内部审计/架构/方法论文档（8 份 audit-round、arch-decision-records、audit-patterns、deep-audit-checklist、shared-utility-pattern、darwin-optimization、audit-a03），与用户使用无关且混杂。统一移入 `dev-notes/`（保留不删、但不进发布包），references/ 精简为 6 个用户向文档。

**修复 4 · scripts 隔离无关杂物（C/E「少数旧资料混在参考资料」实锤）**：源副本被早期反向同步（sync_copies 默认源为 D:\hermes）污染，scripts/ 混入 43 个与记忆技能无关的文件（cos_mcp/ima_mcp/monitor/obsidian_history_sync/skills_mcp_server 等）及 references/archive 62 个归档。全部可恢复隔离至工作区 `.quarantine_*` 目录（留 MANIFEST.json 清单，未删除），scripts/ 仅留 11 个核心脚本。

**修复 5 · 文档补真实输出 + quickstart 中立化（C「缺少实际返回结果展示」+ A「首次配置繁琐」）**：EXAMPLES.md 新增「CLI 实际输出示例」段（write/read/stats/verify 真实运行结果）；quickstart.md 步骤 1 改为平台中立（不绑定 Hermes 复制）、步骤 2 标注目录自动创建、删除对已隔离文件 `list_all_records.py` 的失效引用（改指引到 `stats`/`read`），版本号 v6.14→v6.24.0。

**测试**：`tests/test_28_assertions.py` 59 项断言全 PASS（沿用 v6.23 的 52 + 3，新增 v6.24 的 4：T-4 中性回退 / T-5 env 优先 / T-6 沿用 .hermes / T-7 filelock 缺失降级）。

---

# v6.23.0 (2026-11-03) — SkillHub 发布后 TRACE 子维度复检（示例命令实跑核对 + 错误消息精简）

v6.22.6 发布 SkillHub 后，按 TRACE 评测子维度反馈（R·异常处理「信息晦涩」、C·文档质量「部分示例命令已不适用」、E·开箱即用度）逐条实跑验伪，区分已修复/误报/真实存在，只修实证问题：

**修复 1 · EXAMPLES.md `refine --agent` 与实际接口不符（实跑报 unrecognized arguments）**：`shared_memory.py refine` 子命令只定义 `--days`（agent_id 自动取自各条记录，CLI 无 --agent 参数），文档示例改为 `python shared_memory.py refine --days 7`。同时故障排除中「文件锁残留」路径表述改为「记忆根目录（默认 ~/.hermes/memory_data，可用 CROSS_AGENT_MEMORY_BASE 覆盖）」，消除硬编码歧义。
**修复 2 · detect_storage.check_mysql 错误消息分类精简**：旧版把 pymysql 原始异常全文塞进 message（长而晦涩）。新增 `_short_mysql_error()`：按异常特征分类为一句人类可读短消息（认证失败/数据库不存在/主机无法解析/连接超时/无法连接服务器），完整原始信息移入 `detail` 字段供排查，stdout JSON 报告新增 detail。
**修复 3 · 文档补强（T·国内适配性）**：EXAMPLES.md `memory_refine_batch` 注释明确「默认本地规则提炼，无需任何国外服务；仅设置 LLM_PROVIDER=openai/anthropic 时才调用外部 LLM」。

**验伪结论（不修的项及依据）**：
- 「迁移/仅检查报错」→ 误报：v6.22.5 起 HAS_MYSQL 守卫 + 清晰中文提示，--check-only 实跑 exit=0；
- 「高并发重复/覆盖风险」→ 误报：filelock 在 requirements.txt（v6.22.0 起）且 README/SKILL.md 均有 `pip install fastmcp mcp filelock` 安装说明；
- 「删除备份/隔离/初始化欠缺」→ 已在 v6.22.x 修复（trash/ 备份、scope="mine"、init_jsonl 对齐真实存储），本轮实跑复核通过；
- verify.py agents/memory/health/backup 四子命令与文档一致（实跑核对）。

**测试**：新增 3 项断言（requirements 含 filelock、EXAMPLES 不含 `refine --agent` 且含 `refine --days`、_short_mysql_error 各类异常输出短消息且成功分支无 detail），共 55 项。

# v6.22.6 (2026-11-02) — 第七轮 Hermes 审计收尾修复（1×P2 + 2×P3）

第七轮复审（78/100，报告 references/audit-round7-v6.22.5.md）确认第六轮 3 项修复全部真实落地（P1-7 主链路闭合实测通过、51/51 测试），新发现仅 1×P2 + 2×P3，全部修复：

**P2 · 7 个遗留辅助脚本顶层裸 `import pymysql`**：这些脚本（list_all_records / query_mysql / test_mysql_rw / test_workbuddy_shared / test_workbuddy_write / verify_shared_memory / export_to_vault）是历史遗留，仅存在于规范侧副本（D:\hermes 主副本、skills\skills、software-development、Obsidian 02_Skills），源副本本无。统一改为 try/except 可选导入 + 无 pymysql 时清晰报错退出（同 mysql_store P1-7 模式），共修复 4 个副本 34 个文件实例。
**P3 · 残留死文件**：`scripts/export_to_vault.py`（CHANGELOG v6.19 已记录移除但未物理删除）在 4 个副本物理删除。
**P3 · requirements.txt 注释过时**：更正为「仅 MySQL 模式需要；v6.22.5 起 JSONL 模式真零数据库依赖，pymysql 为可选导入」。

**测试**：新增 1 项断言（源副本与 D:\hermes 规范副本 scripts/ 全量静态扫描：不存在未包 try 的裸 `import pymysql`），共 52 项。

# v6.22.5 (2026-11-02) — 第六轮 Hermes 审计新发现修复（P1-7 裸 import + P2 + P3）

第六轮复审（78/100，报告 references/audit-round6-v6.22.4.md）确认 P1-6/P2-4' 全部真实落地，新发现 3 项，全部修复：

**P1-7 · 部署健壮性：pymysql 顶层裸 import 使 JSONL 降级不可达**（mysql_store.py）
- 根因：L24 顶层裸 `import pymysql` + mcp_server L32 顶层裸 `import mysql_store`——无 pymysql 的机器上 `import mcp_server` 直接 ImportError，mcp_server L141 的降级兜底（try/except ImportError）根本不可达，与 SKILL.md「JSONL 零数据库依赖」矛盾；L92 的 HAS_MYSQL 检测块因裸 import 先崩而成为死代码。
- 修复：pymysql 改为 try/except 可选导入（无 pymysql 时模块可正常导入，HAS_MYSQL=False）；删除重复死代码检测块；新增 `_DB_ERRORS` 统一异常元组替换全部 7 处 `pymysql.Error` 引用；`_get_connection` 类型注解改字符串（延迟求值）并新增 HAS_MYSQL 守卫（无 pymysql 时报清晰 RuntimeError，调用方捕获后降级 JSONL）。
- 回归：子进程屏蔽 pymysql（`sys.modules['pymysql']=None`）实测 `import mysql_store`/`import mcp_server` 均成功且 HAS_MYSQL=False。

**P2 · CHANGELOG 副本计数漂移**：v6.22.4 条目"现 12 个"修正为 14 条（另 4 个 Temp 副本经 --temp 覆盖）。

**P3 · sync_copies 消息语义**：`--dry-run` 区分「目录不存在」与「目录存在但缺 SKILL.md（空目录或损坏）」，不再误报。

**测试**：新增 2 项断言（pymysql 缺失模拟导入、--dry-run 空目录消息），共 52 项。

# v6.22.4 (2026-11-02) — 第五轮 Hermes 正式审计新发现修复（P1-6 + P2-4'）

第五轮正式完整审计（报告 references/audit-round5-v6.22.3.md）确认第四轮修复中 P1-1' 真实生效（Alpha/Beta、完成/取消、敏捷/瀑布、张三/李四 全部不判重）、P1-2 定性获认可；新发现 2 项，经 KK 授权修复。

**P1-6 · 数据完整性：refine_local 过度截断（默认写入路径丢数据）**（shared_utils.refine_local 4b 分支）
- 根因：`(计划|打算|准备|决定|...)` 动宾提取正则无位置约束，句中作**名词**的"计划"也被当作动词命中，默认 `memory_write`（skip_refine=False）即触发，JSONL/MySQL 双后端共用。实测：「我们制定了年度营销计划并提交董事会审批」→「计划并提交董事会审批」（保留 53%）、「财务部完成了季度预算计划表」→「计划表」（23%）、「老板批准了明年的招聘计划」→「计划」（17%）。
- **测试盲区（铁律案例）**：既有 43 项测试全部用 `skip_refine=True`，从未覆盖默认 refine 路径——实现方测试全绿 ≠ 无洞。
- 修复：动词必须**锚定在句首或标点/空白之后**（`(?:^|[，。；、：！？\s])`），锚定不到即落入 4c 保留原句——"宁可多存，绝不截断"。上述三条受害句现全部原文落库；「会议中，决定采用新方案」这类标点后动词仍正常提炼。
- 盲区封堵：新增默认提炼路径（skip_refine=False）的单元 + 端到端回归断言。

**P2-4' · sync_copies.py 副本清单补全 + --scan 盘点**
- 全盘盘点发现共 28 个 `cross-agent-memory*` 目录，同步清单漏 7 个真实活跃副本：Hermes 嵌套 `skills\skills\` 与 `software-development\`、**Obsidian 共享记忆库**（`D:\我的文档\Documents\Obsidian Vault\06-共享记忆库\02_Skills\`）及其 workbuddy-sync 变体、Hermes 侧 workbuddy-sync 变体、Temp release 暂存、历史镜像 `D:\d\hermes`。已全部补入 KNOWN_COPIES（现 14 条，另 4 个 Temp 副本经 --temp 覆盖）。
- 新增 `--scan` 只读盘点模式：扫描常见根目录下全部 `cross-agent-memory*` 目录并打印版本/工具数/是否在清单内，杜绝未来再有清单外副本。备份/历史归档（.bak*、-old-to-remove 等 16 个）不入清单、保持原样。

**测试**：`tests/test_28_assertions.py` 扩至 51 项断言（新增 refine 不截断 3 例、锚定提炼 1 例、默认提炼路径 E2E、--scan）。

# v6.22.3 (2026-11-02) — 第四轮 Hermes 独立审计新发现修复（P1-1' + P1-2 + P2-4）

第四轮审计确认 v6.22.2 的 4 项修复全部落地（数值豁免 80%/50%、200万/150万 等全不判重），但独立实跑发现 2 项"修复不完整"通道与 1 项一致性新问题。经 KK 授权（含设计取舍）全部修复。

**P1-1' · 数据完整性：无数值通道仍丢数据**（shared_utils.is_duplicate）
- 第四轮实测端到端：「启动新项目**Alpha**」vs「**Beta**」（相似度 0.625-0.714）、「**完成了**Q3目标」vs「**取消了**…」仍被 0.6 阈值静默合并——数值豁免只堵了"含数字"通道，无数值、共享长中文前缀、语义不同的文本照丢。
- 修复（双管齐下，策略取"宁可多存一条，不可丢一条"）：
  1. **模糊阈值 0.6 → 0.85**（默认参数）：中文共享长前缀下 2-gram Jaccard 天然偏高，0.85 只保留"几乎逐字相同"的模糊合并；查重主体由精确 hash（fact_id / MySQL SHA256）承担，正常去重能力不受影响。
  2. **极性冲突检查 `_polarity_conflict`**（任一命中即不合并）：① 专名冲突（拉丁词集合不同，如 Alpha vs Beta）；② 反义冲突（同对反义词分列两侧，如 完成/取消、买入/卖出 等 20 对）；③ 否定冲突（恰好一侧含否定标记 不/没/未/取消/暂停…）。

**P1-2 · scope 隔离默认路径：显式声明 + warning**（mcp_server）
- 现状确认：`_filter_by_agent` 仅在 `scope='mine'` 被调用，默认 `scope='all'` 下未知身份可读全量。
- 设计取舍（KK 授权）：`scope='all'` 是**跨 Agent 共享读设计而非隔离边界**（本技能定位即共享记忆层，MCP 协议无身份验证层），不把 unknown+all 改为拒绝以免破坏未传 agent_id 的现有调用方。落地：
  1. `memory_search` / `memory_read_all` / `memory_boot_context` 在 `scope='all'` 且身份未知时，响应附加 `warning` 字段显式提示（真实 agent_id 则为 null）；
  2. SKILL.md 第 6 节新增「设计边界声明」，明确隔离边界只有 `scope='mine'`，要求更高隔离应在 MCP 网关层做准入控制。

**P2-4 · 多副本物理同步断裂**（新增 scripts/sync_copies.py）
- 第四轮发现主副本 v6.22.2 与其余 3 副本（v6.22.0/6.22.1）核心文件 MD5 全不同——历史"审计误判/加载旧缺陷代码"的根源。
- 新增 `sync_copies.py`：以 `D:\hermes\skills\cross-agent-memory`（Hermes 审计源）为默认规范源，一键同步到全部已知副本（WorkBuddy/OpenClaw/QClaw/发布暂存区，`--temp` 可含 Temp）；只覆盖核心文件（SKILL.md/CHANGELOG/EXAMPLES/README/VERSION/requirements + scripts/references/tests），绝不删除各副本独有运行产物；`--dry-run` 预览；逐副本校验 version 与 @mcp.tool 计数。SKILL.md 定期维护节新增"改后必同步"守则。

**测试**：`tests/test_28_assertions.py` 扩至 43 项断言（新增：Alpha/Beta 专名不合并、完成/取消反义不合并、否定冲突不合并、高相似无冲突仍合并、JSONL E2E 专名双落库、scope=all+unknown 返回 warning、sync_copies.py --dry-run 可用）。

# v6.22.2 (2026-11-02) — 第三轮 Hermes 独立审计新发现修复（P1-1 + 3×P2）

第三轮审计（独立隔离 venv 实跑验伪）确认 v6.22.1 的 6 项修复全部落地，同时新发现 1 项数据完整性风险（P1-1）与 3 项一致性问题（P2）。全部修复并纳入持久化回归测试。

**P1-1 · 数据完整性：JSONL 模糊去重误判丢数据**（shared_utils.is_duplicate）
- 实测案例：「项目进度已达百分之八十」与「百分之五十」2-gram Jaccard 相似度 0.778 > 0.6，被静默合并 → 仅数值不同、语义不同的记忆不可检索（进度/预算/比例类高频受影响）。
- 修复（三重）：
  1. **精确去重先行**——归一化空白后完全一致直接判重；
  2. **数值豁免**——提取数值 token（阿拉伯数字含小数/百分号 + 中文数字序列「八十/五十/三点五…」），两侧数值 token 不一致时**绝不模糊合并**，数值不同即视为不同事实；
  3. 数值 token 相同的候选仍走模糊合并，正常去重能力不受影响。
- MySQL 路径本用精确 SHA256 去重，不受影响；修复后双后端对「仅数值不同」的行为一致（都保留）。

**P2-1 · 版本号漂移**：`detect_storage.py` 内 `SKILL_VERSION` 从 6.22.0 同步到 6.22.2，与 VERSION/SKILL.md 对齐。

**P2-2 · 残留旧脚本与存储模型矛盾**：`scripts/init_jsonl.py`（v6.19 旧物）曾创建已废弃的 `memory.json/skills.json/sessions.json` 并写入测试数据；重写为 `detect_storage.init_jsonl` 的薄封装（保持 `--storage-dir/--check-only` CLI 兼容），不再触碰废弃文件。

**P2-3 · 删除计数虚高**：`mysql_store.delete_fact` 的 `deleted` 由硬编码 1 改为 `cursor.rowcount` 如实计数（共享关联 + 记忆本体），docstring 同步说明。

**测试**：`tests/test_28_assertions.py` 新增 6 项回归（阿拉伯/中文数值豁免、精确命中、模糊保留、JSONL 端到端双落库、deleted rowcount 计数），共 35 项断言。

# v6.22.1 (2026-11-02) — Hermes 独立审计（CONDITIONAL PASS）复审修复

对 v6.22.0 通过 Hermes 异族独立审计暴露的真实缺陷逐项修复，全部经 `tests/test_28_assertions.py` 共 29 项断言实测覆盖（原 H-2「不可复现」已落盘持久化测试解决）。

**H-1 · 迁移工具完全不可用**
- `migrate_to_mysql.py` 补全缺失 import（`datetime`/`timedelta`/`Path`/`get_store`/`health_check` 计数字段），并移除对不存在的 `store.log_audit` 的误调用链。
- `mysql_store.log_audit` 落地：写入真实存在的 `memory_audit` 表结构（`id/action/agent_id/fact_id/details/json/created_at`），将 `mcp_session_id`、`data` 并入 `details` JSON，`DELETE` 旧表结构不匹配问题；`health_check` 补齐 `memory_count/agent_count/audit_count` 计数键，供迁移工具读取。
- 验证：`--dry-run` 全流程跑通，无 NameError/AttributeError/KeyError。

**M-4 · 依赖声明错误**
- `requirements.txt`：`pymysql==2.0.0`（真实最新 1.2.x，按此必装失败）改为规范版本区间；补充缺失的 `uvicorn` 依赖。

**M-1 · scope 隔离旁路**
- `_filter_by_agent` 修复：默认/未知身份（`unknown`/`mcp_server`/`mcp_scheduler`）**返回空列表**而非全量，与 `memory_delete` 的 `scope='mine'` 守卫对称，杜绝默认参数下泄漏全部跨 Agent 数据。

**M-2 · 双后端返回 schema 不一致**
- `mysql_store` 新增 `_normalize_row`：将 MySQL 的 `GROUP_CONCAT` 字符串 `all_agents` 统一转为 `agents` 列表、`tags` JSON 串转 list，与 JSONL 后端字段结构对齐（下游 `_filter_by_agent` / scope 隔离依赖 `agents` 字段）。

**M-3 · memory_refine_batch 合并分支 TypeError**
- 合并分支原用 `rec.get("file")`（记录无此字段，恒为 None）→ `Path(None)` 构造即抛 TypeError；改为按 `rec['date']` 定位共享文件 `SHARED_DIR / f"{date}.jsonl"`，`ef` 始终有效。

**E · 初始化健壮性**
- `detect_storage.init_jsonl(memory_base)` 修复：原实现忽略传入参数、一律写到模块全局 `MEMORY_BASE`；现严格基于传入 `memory_base` 创建 `daily/shared/refined/audit/index.jsonl/config.json`，对任意指定基目录正确。

**H-2 · 验证可复现性**
- 新增落盘持久化测试 `tests/test_28_assertions.py`（29 项断言：JSONL 删除保护/去重/隔离 8 + MySQL schema/隔离/删除备份 8 + detect 4 + 静态/单元 8 + M3 回归 1），解决原临时测试删除后无法复证的问题。

# v6.22.0 (2026-11-02) — 按 SkillHub TRACE 评测报告深挖：可靠性 R / 有效性 E

针对 TRACE 评测短板（可靠性 3.9 / 有效性 3.7）的工程化改进，定位并修复了「文档已承诺、代码未兑现」的实况不一致问题。

**R · 可靠性（3.9 → 目标 4.3+）**
- 修复 `detect_storage.py` 存储模型错配：旧版用 `memory.json` 判断 JSONL 就绪、`init_jsonl` 创建 `memory.json/skills.json/sessions.json`，与真实存储（`shared/*.jsonl` + `index.jsonl`）不符，导致状态检查/自动初始化失真。现检测与初始化真实目录结构（`daily/ shared/ refined/ audit/ index.jsonl`），并**补齐文档早已写出但代码缺失的 `--check-only` 参数**。
- 统一并发锁：新增 `memory.lock` 单一写入锁，覆盖 `memory_write`（查重→写→索引原子化，并修复旧版「新写入漏写 index」「查重在锁外」的并发重复/覆盖竞态）、`memory_delete`、`memory_refine_batch`；旧版 refine_batch 与 merge 写文件完全无锁。
- 迁移稳定性：`migrate_to_mysql.py` 沿用先读后动 + 跳过审计开关；配合校验后的状态检查，迁移前可 `--dry-run` 预览。

**R · 功能完善性 / E · 内容完整度：删除保护**
- `memory_delete` 重写：① 先确认存在（找不到返回 `found=False`，不报错）；② **删除前把命中记录备份到 `trash/<时间戳>/` 可恢复，绝不裸删**；③ 统一写入锁内完成，避免与并发写入竞争损坏；④ 结构化返回 `success/found/deleted/backup_path`。新增 `scope='mine'` 仅删本 agent 记录。

**R · 功能完善性：agent_id 隔离**
- `memory_search` / `memory_read_all` / `memory_boot_context` 新增 `scope` 参数：`"all"`（默认，跨Agent共享，保持向后兼容）/`"mine"`（严格隔离，仅返回含本 agent_id 的记录）。旧版隔离仅靠声明、读取全量泄漏。

**R · 异常处理**
- 新增统一错误结构 `_err(code, message, hint)`，写/删失败返回结构化错误而非向 Agent 泄漏原始 traceback；新增 `_safe_read_lines` 坏行/IO 一律跳过。

**E · 输出准确性 / 开箱即用度**
- 代码已兑现文档承诺（`--check-only`、`删除先确认并备份`），消除「文档与实况不一致」；SKILL.md / README / EXAMPLES 同步更新版本与隔离、删除保护、维护说明。

**E · 高级命令健壮性（验证中修复）**
- 修复 `mysql_store.delete_fact` 返回 `rows` 时未转换 `Decimal`/`datetime`，导致 `mcp_server.memory_delete` 备份阶段 `json.dumps` 抛 `Object of type Decimal is not JSON serializable`，进而异常降级到文件模式、误报 `found=False`（正对应 TRACE 指出的"高级命令失败需人工排查"）。现返回前统一 `_convert_decimal`，MySQL 删除分支端到端可用（实测：write→scope 隔离→delete 备份→不可见 全通过）。
- 双后端验证脚本（临时）覆盖：JSONL 强制后端 8 项 + MySQL 真实后端 8 项 + detect `--check-only`，共 28 项断言全部 PASS。

## v6.21.1 (2026-11-01) — 按 SkillHub TRACE 评测报告改进（4.4/5）

- 修正 MCP 工具数：实际 8 个（漏列 memory_read_all），SKILL.md/README/EXAMPLES 三处同步
- 删除幽灵表 audit_logs（代码中不存在），表名以 mysql_store.py 为准（memories/memory_agents）
- 重写 README.md：目录结构与实际文件一致，移除不存在的 test_mcp_server.py/upgrade_manager.py/审计报告目录
- 重写 EXAMPLES.md：全部示例改用已验证的 CLI 与 MCP 接口，删除 init_mysql.py 等幽灵脚本引用
- references/ 清理：删除 6 个内部流程文档（ADR/审计记录/darwin 指南等），保留 6 份用户文档
- 清除 TiDB 旧说法与硬编码路径，环境变量补记 CROSS_AGENT_MEMORY_BASE
- 代码修复：CROSS_AGENT_MEMORY_BASE 此前仅 detect_storage.py 读取，主存储层硬编码 ~/.hermes/memory_data；现 shared_utils.py / shared_memory.py / verify.py 均优先读该环境变量（未设置回退默认路径，已通过端到端冒烟：写入落自定义目录，audit JSONL 同步跟随）

## v6.20 (2026-07-05)

**Round 2 审计修复**：独立第三方审计长深度审计（69/100），修复 2 个 P0 + 5 个 P1。

**P0 修复**：
- P0-1: memory_refine_batch 函数体截断（末尾 pass 替代实现）→ 补充完整循环+return
- P0-2: _ensure_dirs 中 new_lines NameError → 改为空字符串

**P1 修复**：
- P1-1: memory_delete MCP 工具缺失 → 实现完整工具（支持 3 目录扫描删除）
- P1-2: _log_audit_secure 双重定义（L126/L171 签名冲突）→ 删除 L171 覆盖版本
- P1-3: 版本号不一致 → init_jsonl.py/detect_storage.py 统一为 6.20
- P1-4: file_lock/filelock import 不一致 → 统一为 from filelock import FileLock
- P1-5: SKILL.md 工具表格与实际不符 → 更新为 6 个工具（+memory_delete）

**P2 修复**：
- 架构图更新为单 MCP 架构（v6.19 拆分后仍含旧图）
- verify.py 冗余 import argparse 清理

**新增工具**：
- `memory_delete`: 删除指定事实 ID 的记录（支持 DAILY/SHARED/REFINED 三目录）
- 工具总数：5 → 6

---

## v6.19 (2026-07-05)

**奥卡姆剃刀整理**：独立第三方审计长按奥卡姆剃刀原则（如无必要，勿增实体）深度审计。

**审计发现**：
- 118 个文件 → 10 个核心脚本
- 19 个 MCP 工具 → 5 个核心工具（跨 Agent 记忆共享）
- 3 个独立子系统（IMA/COS/Skills）与核心功能解耦

**P0 修复**：
- P0-2: verify.py 死 import (upgrade_manager) → 本地 run_cmd 替换
- P0-1: 根目录历史审计残留 11 个文件 → 归档/删除
- P0-6: memory_data/memory.json 数据文件 → 移除

**P1 修复**：
- P1-1: SKILL.md 架构图升级_manager 引用 → 移除
- P1-2: requirements.txt 移除 qcloud-cos/requests（非核心）
- P1-3: requirements.txt LLM 客户端改为可选注释
- P1-5: README.md 过时 → 更新
- P1-7: templates/ 精简
- P1-8: SKILL.md MCP 工具表格 → 仅保留 5 个核心工具

**P2 修复**：
- P2-1/P2-2: mcp_server.py 重复 import 清理
- P2-3: 版本号同步 (6.18 → 6.19)

**拆分记录**：
| 原文件 | 功能 | 去向 |
|--------|------|------|
| ima_mcp_server.py | IMA 知识库 | 独立 skill |
| cos_mcp_server.py | 腾讯云 COS | 独立 skill |
| skills_mcp_server.py | 技能管理 | 独立 skill |
| obsidian_history_sync.py | Obsidian 同步 | 移除 |
| export_to_vault.py | Vault 导出 | 移除 |

**验证**：
- verify.py 无死 import
- mcp_server.py 无重复 import
- requirements.txt 无冗余依赖
- 文件数：118 → 30（75% 精简）

---

## v6.18 (2026-07-04)

**架构重构**：基于架构师-A02 评估报告（61/100）修复所有 P0/P1/P2 级问题

**P0 修复**：
- P0-1: 添加 hasattr 守卫，修复 mysql_store 缺失方法调用
- P0-2: 填充 memory_write 去重分支，消除数据静默丢失

**P1 修复**：
- P1-NEW: 移除硬编码路径（obsidian_history_sync.py, skills_mcp_server.py）
- P1-7: 固定依赖版本（requirements.txt）
- P1-3: 添加 mysql_store schema 初始化
- P1-4: 添加 mysql_store 连接池
- P1-1: 清理 upgrade_manager.py 职责越界

**P2 修复**：
- P2-2: 添加 SKILL.md MCP 工具表格
- P2-3: 同步版本号（SKILL.md 6.18）
- P2-4: 同步 B/C references/ 归档
- P2-6: 删除 query_tidb_lite.py（TiDB Lite 残留）

**验证**：
- 三副本一致性验证通过（A/B/C MD5 相同）
- 审计评分：61/100 → 预期 75+/100

---

## v6.17 (2026-07-04)

**独立第三方审计长-705 深度审计**（评分 61/100 → 目标 70+）

### Fixed
- 空密码默认值（mysql_store.py + detect_storage.py）→ 强制从环境变量读取
- 裸 except 残留（obsidian_history_sync.py line 216/228）→ 改为具体类型
- 冗余依赖声明（requirements.txt 移除 fastmcp + mysql-connector-python）
- 版本号同步（SKILL.md + CHANGELOG）
- 备份文件清理（4 个 .bak/.backup 文件）
- skill.txt 过时文件删除
- references 历史垃圾清理（28 个审计/测试/会话记录归档）

### Changed
- DEFAULT_CONFIG[password] 默认值从 "" 改为 os.getenv("CROSS_AGENT_MEMORY_DB_PASSWORD", None)

### Security
- 空密码漏洞修复（P1 → Fixed）
- 备份残留清理（P2 → Fixed）

---

# cross-agent-memory Skill Changelog

## v6.0 (2026-06-04) — 本地数据主权

**MySQL 存储层**：Windows 原生 MySQL 8.0，降级到 JSONL 文件存储

## v5.0 (2026-05-15) — 安全加固

- 审计报告最终版：6 轮修复，15 个安全漏洞修复
- 去重双写 BUG 修复
- 并发文件锁（Windows msvcrt / Unix fcntl）

## v4.0 (2026-04-23) — 审计驱动修复

- 审计后评级：A-（生产可用）
- 第一批安全修复：shell=True 移除、内容安全过滤

## v3.0 (2026-04-01) — 记忆分级系统

- 记忆分级（core/important/normal/temp）
- 启动注入（memory_boot_context）
- 衰减权重排序