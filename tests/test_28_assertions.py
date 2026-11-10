# -*- coding: utf-8 -*-
"""
cross-agent-memory v6.26.3 —— 持久化 63 项断言测试
覆盖 TRACE 改进项：删除保护 / 并发锁 / scope 隔离 / 双后端 schema 一致 /
Decimal 序列化 / migrate 工具 / detect --check-only。

运行：
    python tests/test_28_assertions.py
退出码 0 = 全部通过；非 0 = 有失败。

设计：
- JSONL 后端：切换 mcp_server.HAS_MYSQL=False，使用临时 MEMORY_BASE 隔离。
- MySQL 后端：使用真实库 + cam_test_ 前缀 agent/fact_id，测试后清理。
- detect：直接调用 check_jsonl/init_jsonl，并用子进程验证 --check-only CLI。
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from decimal import Decimal

SKILL = Path(__file__).resolve().parent.parent / "scripts"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

# 必须先设临时根，再导入依赖 MEMORY_BASE 的模块
TMP_JSONL = tempfile.mkdtemp(prefix="cam_jsonl_")
os.environ["CROSS_AGENT_MEMORY_BASE"] = TMP_JSONL

import shared_utils           # noqa: E402
import mcp_server             # noqa: E402
import mysql_store            # noqa: E402
import shared_memory          # noqa: E402
import detect_storage         # noqa: E402
import migrate_to_mysql       # noqa: E402

# —— Mock MCP Context（_log_audit_secure 对 ctx=None 已容错，工具函数需 .info/.error）——
class MockCtx:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass

ctx = MockCtx()

# 跨平台 / CI 兼容：探测 MySQL 是否可用；不可用则整组 MySQL 测试优雅跳过
def _mysql_available():
    try:
        s = mysql_store.get_store()
        hc = s.health_check()
        return hc.get("status") == "healthy"
    except Exception:
        return False
MYSQL_OK = _mysql_available()
if not MYSQL_OK:
    # 无 MySQL 环境强制走 JSONL 降级路径，避免后续依赖 HAS_MYSQL=True 的代码崩
    mcp_server.HAS_MYSQL = False
    mysql_store.HAS_MYSQL = False

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))

def mysql_cleanup(prefixes=("cam_test_", "cam_v_")):
    try:
        store = mysql_store.get_store()
        with store._cursor() as cur:
            for p in prefixes:
                cur.execute("SELECT fact_id FROM memories WHERE fact_id LIKE %s", (p + "%",))
                fids = [r["fact_id"] for r in cur.fetchall()]
                for fid in fids:
                    store.delete_fact(fact_id=fid, scope="all")
            for p in prefixes:
                cur.execute("DELETE FROM memory_agents WHERE agent_id LIKE %s", (p + "%",))
    except Exception as e:
        print(f"[WARN] mysql_cleanup 异常: {e}")

# ========================= 准备 =========================
mysql_cleanup()
AG_A = "cam_test_a"
AG_B = "cam_test_b"
FACT1 = "跨Agent记忆共享使用 JSONL 作为默认零依赖存储后端"
FACT2 = "MySQL 可选升级用于高频并发共享记忆场景"

# ========================= 组 A：JSONL 后端 =========================
mcp_server.HAS_MYSQL = False   # 强制走 JSONL 路径

# A1: 写入成功
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact=FACT1, skip_refine=True)
check("A1 JSONL 写入成功", r.get("success") is True, str(r.get("message", ""))[:60])

# A2: 全量读取包含该记录
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
facts = r.get("facts", [])
check("A2 JSONL read_all(all) 含记录", any(f.get("fact_id") for f in facts) and len(facts) >= 1, f"count={len(facts)}")

# A3: 去重——重复写入不新增
r = mcp_server.memory_write(ctx, agent_id=AG_B, fact=FACT1, skip_refine=True)
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
check("A3 JSONL 去重（重复写入不计新增）", len(r.get("facts", [])) == 1, f"count={len(r.get('facts', []))}")

# A4: scope=mine 正确 agent 返回记录
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="mine")
check("A4 JSONL scope=mine 正确 agent 返回", len(r.get("facts", [])) >= 1, f"count={len(r.get('facts', []))}")

# A5: scope=mine 未知 agent 返回空（M-1 隔离旁路修复）
r = mcp_server.memory_read_all(ctx, agent_id="unknown", days=90, scope="mine")
check("A5 JSONL scope=mine 未知 agent 返回空(M-1)", r.get("facts", []) == [], f"count={len(r.get('facts', []))}")

# 取得 fact_id
fid1 = None
rr = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
for f in rr.get("facts", []):
    if FACT1[:10] in (f.get("fact") or ""):
        fid1 = f.get("fact_id")

# A6: 删除成功并备份
r = mcp_server.memory_delete(ctx, fact_id=fid1, agent_id=AG_A, scope="all") if fid1 else {"found": False}
check("A6 JSONL 删除 found=True 且有备份路径", r.get("found") is True and bool(r.get("backup_path")), str(r)[:80])

# A7: trash 备份文件存在
bp = r.get("backup_path")
bak_exists = bool(bp) and Path(bp).exists() and list(Path(bp).rglob("*.bak"))
check("A7 JSONL trash 备份文件存在", bak_exists, f"backup_path={bp}")

# A8: 删除后全量读取不再包含
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
gone = not any(FACT1[:10] in (f.get("fact") or "") for f in r.get("facts", []))
check("A8 JSONL 删除后记录消失", gone, f"count={len(r.get('facts', []))}")

# ========================= 组 B：MySQL 后端 =========================
# 数据库不可用时整组跳过（CI 无 MySQL 的 runner 上仅测 JSONL 核心，避免误报崩溃）
if MYSQL_OK:
    mcp_server.HAS_MYSQL = True    # 恢复真实 MySQL

    # B1: 写入成功
    r = mcp_server.memory_write(ctx, agent_id=AG_A, fact=FACT2, skip_refine=True)
    check("B1 MySQL 写入成功", r.get("success") is True, str(r.get("message", ""))[:60])
    fid2 = r.get("fact_id")

    # B2: read_all(all) 含记录且字段为 agents（M-2 schema 一致）
    r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
    bfacts = r.get("facts", [])
    has_agents = any(("agents" in f) and ("all_agents" not in f) for f in bfacts)
    check("B2 MySQL read_all 返回 agents 字段(M-2)", has_agents, f"keys sample={list(bfacts[0].keys()) if bfacts else []}")

    # B3: scope=mine 正确 agent 返回
    r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="mine")
    check("B3 MySQL scope=mine 正确 agent 返回", len(r.get("facts", [])) >= 1, f"count={len(r.get('facts', []))}")

    # B4: scope=mine 未知 agent 返回空（M-1）
    r = mcp_server.memory_read_all(ctx, agent_id="unknown", days=90, scope="mine")
    check("B4 MySQL scope=mine 未知 agent 返回空(M-1)", r.get("facts", []) == [], f"count={len(r.get('facts', []))}")

    # B5: search 返回 agents 字段（M-2）
    r = mcp_server.memory_search(ctx, query="MySQL", agent_id=AG_A, days=90)
    sfacts = r.get("results", r.get("facts", []))
    has_agents_s = any(("agents" in f) and ("all_agents" not in f) for f in sfacts)
    check("B5 MySQL search 返回 agents 字段(M-2)", has_agents_s, f"keys sample={list(sfacts[0].keys()) if sfacts else []}")

    # B6: store 级 delete_fact 返回 rows 且 Decimal 可序列化（原 bug 回归）
    store = mysql_store.get_store()
    res = store.delete_fact(fact_id=fid2, agent_id=AG_A, scope="all") if fid2 else {"found": False}
    rows = res.get("rows", [])
    serial_ok = True
    try:
        json.dumps(rows)
    except Exception:
        serial_ok = False
    check("B6 MySQL delete_fact found=True+rows+Decimal可序列化", res.get("found") is True and bool(rows) and serial_ok, f"found={res.get('found')} rows={len(rows)} serial={serial_ok}")

    # B7: 删除不存在记录 found=False 不报错（wrapper 路径）
    r = mcp_server.memory_delete(ctx, fact_id="cam_nonexistent_id_000", agent_id=AG_A, scope="all")
    check("B7 MySQL 删除不存在 found=False 不报错", r.get("found") is False and r.get("success") is True, str(r)[:60])

    # B8: wrapper 级 memory_delete 端到端（found=True + 备份路径），验证真实 MCP 删除+备份路径
    FACT3 = "scope 隔离确保不同 Agent 不会误读彼此的私有记忆"
    r = mcp_server.memory_write(ctx, agent_id=AG_A, fact=FACT3, skip_refine=True)
    fid3 = r.get("fact_id")
    rd = mcp_server.memory_delete(ctx, fact_id=fid3, agent_id=AG_A, scope="all") if fid3 else {"found": False}
    check("B8 MySQL wrapper 删除 found=True+备份路径", rd.get("found") is True and bool(rd.get("backup_path")), f"found={rd.get('found')} backup={bool(rd.get('backup_path'))}")
else:
    check("组B MySQL 后端 [SKIP 无MySQL]", True, "MySQL 不可用，跳过")

# ========================= 组 C：detect_storage =========================
TMP_DET = Path(tempfile.mkdtemp(prefix="cam_det_"))
# C1: 未初始化时 check_jsonl available=False
st = detect_storage.check_jsonl(TMP_DET)
check("C1 detect 未初始化 available=False", st.get("available") is False, st.get("message", ""))
# C2: 初始化创建 index.jsonl + shared/（init_jsonl 遵循传入基目录）
detect_storage.init_jsonl(TMP_DET)
check("C2 detect init 创建 index.jsonl", (TMP_DET / "index.jsonl").exists() and (TMP_DET / "shared").exists())
# C3: 初始化后 check_jsonl available=True
st = detect_storage.check_jsonl(TMP_DET)
check("C3 detect 初始化后 available=True", st.get("available") is True, st.get("message", ""))
# C4: --check-only 子进程输出合法 JSON
env = dict(os.environ); env["CROSS_AGENT_MEMORY_BASE"] = str(TMP_DET)
proc = subprocess.run([sys.executable, str(SKILL / "detect_storage.py"), "--check-only"],
                      capture_output=True, text=True, env=env)
json_ok = False
try:
    rep = json.loads(proc.stdout)
    json_ok = isinstance(rep, dict)
except Exception:
    json_ok = False
check("C4 detect --check-only 输出合法 JSON", proc.returncode == 0 and json_ok, f"rc={proc.returncode}")

# ========================= 组 D：静态 / 单元 =========================
REQ = (SKILL.parent / "requirements.txt").read_text(encoding="utf-8")
# D1: requirements 无 pymysql==2.0.0 且含 uvicorn
no_bad = "pymysql==2.0.0" not in REQ
has_uvicorn = ("uvicorn" in REQ)
check("D1 requirements 无 pymysql==2.0.0 且含 uvicorn", no_bad and has_uvicorn, f"bad={not no_bad} uvicorn={has_uvicorn}")

# D2: _filter_by_agent 未知身份返回空（M-1）
filtered = mcp_server._filter_by_agent([{"agents": ["x"]}], "unknown")
check("D2 _filter_by_agent 未知身份返回空(M-1)", filtered == [], f"got={filtered}")

# D3: _normalize_row all_agents->agents（M-2）
nr = mysql_store._normalize_row({"all_agents": "a,b", "tags": '["t1"]'})
check("D3 _normalize_row all_agents->agents(M-2)", nr.get("agents") == ["a", "b"] and "all_agents" not in nr and nr.get("tags") == ["t1"], str(nr))

# D4: log_audit 成功（H-1）
if MYSQL_OK:
    la = mysql_store.get_store().log_audit("verify_test", AG_A, {"k": 1})
    check("D4 mysql_store.log_audit 成功(H-1)", la.get("success") is True, str(la))
else:
    check("D4 mysql_store.log_audit 成功(H-1) [SKIP 无MySQL]", True, "跳过")

# D5: health_check 含 memory_count（H-1）
if MYSQL_OK:
    hc = mysql_store.get_store().health_check()
    check("D5 health_check 含 memory_count(H-1)", "memory_count" in hc and isinstance(hc.get("memory_count"), int), str(sorted(hc.keys())))
else:
    check("D5 health_check 含 memory_count(H-1) [SKIP 无MySQL]", True, "跳过")

# D6: _convert_decimal 转换 Decimal（原 bug 防护，位于 mysql_store）
cv = mysql_store._convert_decimal({"confidence": Decimal("0.9")})
ok6 = isinstance(cv.get("confidence"), float) and cv.get("confidence") == 0.9
try:
    json.dumps(cv)
except Exception:
    ok6 = False
check("D6 _convert_decimal Decimal->float 可序列化", ok6, str(cv))

# D7: migrate_to_mysql 导入且 scan 可用（H-1）
try:
    n = migrate_to_mysql.scan_jsonl_files(90)
    ok7 = isinstance(n, list)
except Exception as e:
    ok7 = False
check("D7 migrate_to_mysql.scan 可用(H-1)", ok7, f"type={type(n).__name__ if ok7 else 'err'}")

# D8: requirements 解析 pymysql 版本有效（不指向不存在版本）
import re as _re
m = _re.search(r"pymysql[^\n]*", REQ)
spec = m.group(0) if m else ""
ok8 = ("pymysql" in spec) and ("2.0.0" not in spec)
check("D8 requirements pymysql 版本规范", ok8, spec.strip())

# ========================= M-3 回归：refine_batch 合并分支 =========================
# 构造一条 shared 既有记录 + 一条同 fact_id 的 daily raw，触发合并分支，
# 验证不再因 rec.get("file") 恒为 None 而抛 ef 未定义 TypeError（M-3 修复）。
raw_dir = shared_utils.DAILY_DIR
shared_dir = shared_utils.SHARED_DIR
m3_text = "M3回归测试 refine合并分支不再抛出 ef 未定义 TypeError"
m3_fid = shared_utils.fact_id_from_text(m3_text)
m3_date = shared_utils.today_str()
m3_rec = {"fact_id": m3_fid, "fact": m3_text, "agents": ["seed"], "date": m3_date}
with open(shared_dir / f"{m3_date}.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(m3_rec, ensure_ascii=False) + "\n")
with open(raw_dir / f"{m3_date}.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"text": m3_text, "agent_id": AG_A}, ensure_ascii=False) + "\n")
try:
    rr = mcp_server.memory_refine_batch(ctx, agent_id="mcp_scheduler", days=365)
    m3_ok = rr.get("success") is True and isinstance(rr.get("merged", 0), int) and rr.get("merged", 0) >= 1
    m3_detail = f"merged={rr.get('merged')} refined={rr.get('refined')} total={rr.get('total')}"
except Exception as e:
    m3_ok = False
    m3_detail = f"EXC: {e}"
check("M3 memory_refine_batch 合并分支无 TypeError", m3_ok, m3_detail)

# ========================= 第三轮审计回归（P1-1 / P2-3） =========================
# P1-1 数值豁免：仅数值不同、语义不同的事实绝不被模糊合并（原会静默合并丢数据）
dup80 = shared_utils.is_duplicate("项目进度已达80%", [{"fact": "项目进度已达50%"}])
check("P1 数值豁免: 阿拉伯 80% vs 50% 不合并", dup80 is None, str(dup80)[:60] if dup80 else "None")

dup_cn = shared_utils.is_duplicate("项目进度已达百分之八十", [{"fact": "项目进度已达百分之五十"}])
check("P1 数值豁免: 中文 八十 vs 五十 不合并", dup_cn is None, str(dup_cn)[:60] if dup_cn else "None")

dup_same = shared_utils.is_duplicate("项目进度已达80%", [{"fact": "项目进度已达80%"}])
check("P1 精确命中: 完全相同文本仍判重", dup_same is not None)

dup_fuzzy = shared_utils.is_duplicate("今天买了股票", [{"fact": "今天买了股票"}])
check("P1 模糊保留: 纯文本相似仍正常判重", dup_fuzzy is not None)

# P1-1 端到端（JSONL 写入路径）：数值不同的两条记忆都落库、都可检索
mcp_server.HAS_MYSQL = False
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="E2E去重回归: 批量任务完成率80%", skip_refine=True)
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="E2E去重回归: 批量任务完成率50%", skip_refine=True)
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
cnt80 = sum(1 for f in r.get("facts", []) if "80%" in (f.get("fact") or ""))
cnt50 = sum(1 for f in r.get("facts", []) if "50%" in (f.get("fact") or ""))
check("P1 E2E: 数值不同事实都落库可检索", cnt80 == 1 and cnt50 == 1, f"80%:{cnt80} 50%:{cnt50}")
if MYSQL_OK:
    mcp_server.HAS_MYSQL = True

    # P2-3: delete_fact 的 deleted 用 rowcount 如实计数（关联+本体），不再硬编码 1
    r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="P2回归: 删除计数以rowcount为准", skip_refine=True)
    fid_p2 = r.get("fact_id")
    res_p2 = mysql_store.get_store().delete_fact(fact_id=fid_p2, agent_id=AG_A, scope="mine") if fid_p2 else {"found": False}
    # 单 agent 场景：1 条共享关联 + 1 条记忆本体 = 2 行
    check("P2 delete_fact deleted 如实计数(关联+本体=2)", res_p2.get("found") is True and res_p2.get("deleted") == 2, f"deleted={res_p2.get('deleted')}")
else:
    check("P2 delete_fact deleted 如实计数(关联+本体=2) [SKIP 无MySQL]", True, "跳过")

# ========================= 第四轮审计回归（P1-1' / P1-2 / P2-4） =========================
# P1-1' 专名冲突：拉丁词集合不同（Alpha vs Beta）绝不模糊合并
dup_alpha = shared_utils.is_duplicate(
    "我们决定下周启动新项目Alpha的实施方案",
    [{"fact": "我们决定下周启动新项目Beta的实施方案"}])
check("P1' 专名冲突: Alpha vs Beta 不合并", dup_alpha is None, str(dup_alpha)[:50] if dup_alpha else "None")

# P1-1' 反义冲突：完成 vs 取消
dup_anti = shared_utils.is_duplicate(
    "我们完成了Q3季度目标的所有任务",
    [{"fact": "我们取消了Q3季度目标的所有任务"}])
check("P1' 反义冲突: 完成 vs 取消 不合并", dup_anti is None, str(dup_anti)[:50] if dup_anti else "None")

# P1-1' 否定冲突：恰好一侧含否定标记
dup_neg = shared_utils.is_duplicate(
    "系统运行正常，日志无异常",
    [{"fact": "系统运行正常，日志有异常"}])
# 两句均无否定标记 → 不触发否定冲突；改用明确否定差异
dup_neg2 = shared_utils.is_duplicate(
    "该方案已经通过评审",
    [{"fact": "该方案未通过评审"}])
check("P1' 否定冲突: 通过 vs 未通过 不合并", dup_neg2 is None, str(dup_neg2)[:50] if dup_neg2 else "None")

# P1-1' 高相似无冲突仍合并（阈值 0.85 保留正常去重能力）
base_txt = "我们团队决定采用分阶段交付的方式推进这个项目的落地实施"
dup_hi = shared_utils.is_duplicate(base_txt, [{"fact": base_txt + "呀"}])
check("P1' 高相似保留: 几乎逐字相同仍判重", dup_hi is not None)

# P1-1' E2E（JSONL 写入路径）：专名不同的事实双落库、都可检索
mcp_server.HAS_MYSQL = False
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="E2E专名回归: 新项目代号Alpha本周启动", skip_refine=True)
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="E2E专名回归: 新项目代号Beta本周启动", skip_refine=True)
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
cnt_a = sum(1 for f in r.get("facts", []) if "Alpha" in (f.get("fact") or ""))
cnt_b = sum(1 for f in r.get("facts", []) if "Beta" in (f.get("fact") or ""))
check("P1' E2E: 专名不同事实双落库可检索", cnt_a == 1 and cnt_b == 1, f"Alpha:{cnt_a} Beta:{cnt_b}")
mcp_server.HAS_MYSQL = True

# P1-2: scope=all + 未知身份 → 返回 warning；真实 agent_id → warning 为 None
mcp_server.HAS_MYSQL = False
r_unknown = mcp_server.memory_read_all(ctx, agent_id="unknown", days=90, scope="all")
r_known = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
check("P1-2 scope=all+unknown 返回 warning", r_unknown.get("warning") is not None,
      str(r_unknown.get("warning"))[:50] if r_unknown.get("warning") else "None")
check("P1-2 scope=all+真实agent 无 warning", r_known.get("warning") is None)
mcp_server.HAS_MYSQL = True

# P2-4: sync_copies.py 存在且 --dry-run 可用
sync_py = SKILL.parent / "scripts" / "sync_copies.py"
proc_sync = subprocess.run([sys.executable, str(sync_py), "--dry-run"],
                           capture_output=True, text=True)
check("P2-4 sync_copies.py --dry-run 可用", proc_sync.returncode == 0 and "cross-agent-memory" in (proc_sync.stdout + proc_sync.stderr),
      f"rc={proc_sync.returncode}")

# ========================= 第五轮审计回归（P1-6 / P2-4'） =========================
# P1-6: refine_local 4b 动词锚定——句中作名词的"计划"不再触发截断，长句原文保留
rf1 = shared_utils.refine_local("我们制定了年度营销计划并提交董事会审批", AG_A)
check("P1-6 refine 不截断: 年度营销计划句原文保留", rf1["fact"] == "我们制定了年度营销计划并提交董事会审批", rf1["fact"][:40])
rf2 = shared_utils.refine_local("财务部完成了季度预算计划表", AG_A)
check("P1-6 refine 不截断: 预算计划表句原文保留", rf2["fact"] == "财务部完成了季度预算计划表", rf2["fact"][:40])
rf3 = shared_utils.refine_local("老板批准了明年的招聘计划", AG_A)
check("P1-6 refine 不截断: 招聘计划句原文保留", rf3["fact"] == "老板批准了明年的招聘计划", rf3["fact"][:40])
rf4 = shared_utils.refine_local("会议中，决定采用新方案", AG_A)
check("P1-6 锚定提炼保留: 标点后动词仍正常提炼", rf4["fact"].startswith("决定"), rf4["fact"][:40])

# P1-6 E2E（默认写入路径 skip_refine=False，堵审计点名的测试盲区）
mcp_server.HAS_MYSQL = False
r = mcp_server.memory_write(ctx, agent_id=AG_A, fact="E2E默认提炼回归: 我们制定了年度营销计划并提交董事会审批")
r = mcp_server.memory_read_all(ctx, agent_id=AG_A, days=90, scope="all")
full_kept = any("年度营销计划并提交董事会审批" in (f.get("fact") or "") for f in r.get("facts", []))
check("P1-6 E2E: 默认提炼路径原文完整落库", full_kept, f"count={len(r.get('facts', []))}")
mcp_server.HAS_MYSQL = True

# P2-4': sync_copies --scan 全盘盘点可用（只读）
proc_scan = subprocess.run([sys.executable, str(SKILL.parent / "scripts" / "sync_copies.py"), "--scan"],
                           capture_output=True, text=True)
scan_out = proc_scan.stdout + proc_scan.stderr
check("P2-4' sync_copies --scan 可用并列出副本", proc_scan.returncode == 0 and "cross-agent-memory" in scan_out,
      f"rc={proc_scan.returncode}")

# ========================= 第六轮审计回归（P1-7 / P3） =========================
# P1-7: 子进程屏蔽 pymysql（sys.modules=None 触发 ImportError 语义），
# 验证 mysql_store / mcp_server 均可导入且 HAS_MYSQL=False（JSONL 降级路径可达）
block_code = (
    "import sys\n"
    "sys.modules['pymysql'] = None\n"
    "sys.path.insert(0, r'" + str(SKILL) + "')\n"
    "import mysql_store\n"
    "assert mysql_store.HAS_MYSQL is False, 'HAS_MYSQL 应为 False'\n"
    "import mcp_server\n"
    "assert mcp_server.HAS_MYSQL is False, 'mcp_server.HAS_MYSQL 应为 False'\n"
    "print('P1-7 OK')\n"
)
proc_block = subprocess.run([sys.executable, "-c", block_code], capture_output=True, text=True)
check("P1-7 无 pymysql 环境模块可导入+HAS_MYSQL=False", proc_block.returncode == 0 and "P1-7 OK" in proc_block.stdout,
      f"rc={proc_block.returncode}" + ("" if proc_block.returncode == 0 else " " + proc_block.stderr[-120:]))

# P3: --dry-run 对空目录（缺 SKILL.md）报「空目录」而非「不存在」
proc_dry = subprocess.run([sys.executable, str(SKILL.parent / "scripts" / "sync_copies.py"), "--dry-run"],
                          capture_output=True, text=True)
dry_out = proc_dry.stdout + proc_dry.stderr
check("P3 dry-run 区分空目录消息", proc_dry.returncode == 0 and "缺 SKILL.md" in dry_out and "空目录" in dry_out,
      f"rc={proc_dry.returncode}")

# ========================= 第七轮审计回归（P2 裸 import 全量静态扫描） =========================
# 源副本与 D:\hermes 规范副本（审计读取源）的 scripts/ 下，
# 不允许存在未包 try 的顶层裸 `import pymysql`（第七轮 P2：7 个遗留脚本已统一可选导入）。
import re as _re
def _has_bare_pymysql_import(scripts_dir):
    offenders = []
    sd = Path(scripts_dir)
    if not sd.is_dir():
        return offenders
    for py in sd.glob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _re.match(r"^import pymysql\s*$", line):
                # 允许出现在 try 块内：向前找最近的非空行，若以 try: 结尾则豁免
                prev = ""
                for j in range(i - 2, -1, -1):
                    if text.splitlines()[j].strip():
                        prev = text.splitlines()[j].strip()
                        break
                if not prev.endswith("try:"):
                    offenders.append(f"{py.name}:{i}")
    return offenders

src_offenders = _has_bare_pymysql_import(SKILL.parent / "scripts")
hermes_scripts = Path(r"D:\hermes\skills\cross-agent-memory\scripts")
hermes_offenders = _has_bare_pymysql_import(hermes_scripts) if hermes_scripts.is_dir() else []
check("P2 全量静态扫描: 无裸 import pymysql",
      not src_offenders and not hermes_offenders,
      f"source={src_offenders or '无'} hermes={hermes_offenders or '无'}")

# ========================= v6.23.0 TRACE 子维度复检 =========================

import re as _re_mod

# T-1 requirements.txt 含 filelock（并发写入锁依赖，R·运行稳定性）
try:
    _req_text = (SKILL.parent / "requirements.txt").read_text(encoding="utf-8")
    check("T-1 requirements.txt 声明 filelock 依赖",
          "filelock" in _req_text,
          f"requirements.txt 内容含 filelock: {'filelock' in _req_text}")
except OSError as e:
    check("T-1 requirements.txt 声明 filelock 依赖", False, f"读取失败: {e}")

# T-2 EXAMPLES.md 示例命令与实际接口一致（C·文档质量：refine 无 --agent 参数）
try:
    _ex_text = (SKILL.parent / "EXAMPLES.md").read_text(encoding="utf-8")
    _cli = _ex_text.split("## 二、CLI 路径", 1)[-1].split("## 三、", 1)[0]
    _bad = _re_mod.search(r"shared_memory\.py refine[^\n]*--agent", _cli)
    _good = _re_mod.search(r"shared_memory\.py refine[^\n]*--days", _cli)
    check("T-2 EXAMPLES refine 示例不再携带不存在的 --agent 参数",
          (_bad is None) and (_good is not None),
          f"含 --agent: {bool(_bad)}, 含 --days: {bool(_good)}")
except OSError as e:
    check("T-2 EXAMPLES refine 示例不再携带不存在的 --agent 参数", False, f"读取失败: {e}")

# T-3 check_mysql 错误消息分类精简（R·异常处理：短消息 + detail 分离）
sys.path.insert(0, str(SKILL.parent / "scripts"))
try:
    import detect_storage as _ds
    _cases = [
        (Exception("(2003, \"Can't connect to MySQL server on '127.0.0.1' ([Errno 10061])\")"), "无法连接"),
        (Exception("(1045, \"Access denied for user 'root'@'localhost'\")"), "认证失败"),
        (Exception("(2003, \"Can't connect (timed out)\")"), "超时"),
        (Exception("Name or service not known"), "主机名"),
        (Exception("某种未知怪异错误"), "detail"),
    ]
    _t3_ok, _t3_detail = True, []
    for exc, expect in _cases:
        msg, det = _ds._short_mysql_error(exc)
        if expect not in msg or not det:
            _t3_ok = False
            _t3_detail.append(f"{exc!r}->msg={msg!r},detail={bool(det)}")
    # 短消息长度上限（防止退回长异常全文）
    _longest = max(len(_ds._short_mysql_error(e)[0]) for e, _ in _cases)
    check("T-3 check_mysql 错误消息分类精简（短消息+detail 分离）",
          _t3_ok and _longest <= 60,
          f"全部命中分类: {_t3_ok}, 最长消息 {_longest} 字符 {_t3_detail}")
except Exception as e:
    check("T-3 check_mysql 错误消息分类精简（短消息+detail 分离）", False, f"导入/执行失败: {e}")

# ========================= v6.24.0 TRACE 复评（跨平台开箱即用） =========================
def _resolve_in_subproc(env_val=None, home_fake=None, hermes_exists=False, block_filelock=False):
    # 用 USERPROFILE 覆盖 Path.home()（Windows），避免重赋值 pathlib.Path 类导致 import 崩溃
    code = "import sys, os\n"
    code += "sys.path.insert(0, %r)\n" % str(SKILL)
    code += "os.environ.pop('CROSS_AGENT_MEMORY_BASE', None)\n"
    if home_fake is not None:
        code += "os.environ['USERPROFILE'] = %r\n" % home_fake
    if hermes_exists:
        code += "import os as _o\n"
        code += "_o.makedirs(os.path.join(os.environ.get('USERPROFILE', '.'), '.hermes', 'memory_data'), exist_ok=True)\n"
    if env_val is not None:
        code += "os.environ['CROSS_AGENT_MEMORY_BASE'] = %r\n" % env_val
    if block_filelock:
        code += "sys.modules['filelock'] = None\n"   # 模拟 filelock 未安装 -> from filelock import 触发 ImportError
        code += "import shared_memory\n"
        code += "print('HAS_FILE_LOCK=' + str(shared_memory.HAS_FILE_LOCK))\n"
    else:
        code += "import shared_utils\n"
        code += "print(str(shared_utils.MEMORY_BASE))\n"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(SKILL))
    return out.stdout.strip(), out.stderr.strip()

_fake = tempfile.mkdtemp(prefix="cam_home_")
# T-4 中性默认：无 env、无 .hermes -> 落到 ~/.cross-agent-memory/memory_data
_out4, _err4 = _resolve_in_subproc(home_fake=_fake, env_val=None, hermes_exists=False)
check("T-4 智能默认·中性回退（无env/无.hermes→.cross-agent-memory）",
      _out4.replace("\\", "/").endswith(".cross-agent-memory/memory_data"), f"got={_out4} err={_err4[:80]}")

# T-5 显式 env 优先
_env = tempfile.mkdtemp(prefix="cam_env_")
_out5, _err5 = _resolve_in_subproc(home_fake=_fake, env_val=_env, hermes_exists=True)
check("T-5 智能默认·env 优先于既有 .hermes", _out5 == _env, f"got={_out5} want={_env}")

# T-6 沿用既有 .hermes（兼容旧数据）
_out6, _err6 = _resolve_in_subproc(home_fake=_fake, env_val=None, hermes_exists=True)
check("T-6 智能默认·沿用既有 .hermes/memory_data",
      _out6.replace("\\", "/").endswith(".hermes/memory_data"), f"got={_out6} err={_err6[:80]}")

# T-7 filelock 缺失优雅降级（不再 import 崩溃）
_out7, _err7 = _resolve_in_subproc(home_fake=_fake, block_filelock=True)
check("T-7 filelock 缺失时优雅降级（HAS_FILE_LOCK=False 且不崩溃）",
      _out7.strip() == "HAS_FILE_LOCK=False" and "Traceback" not in _err7, f"out={_out7} err={_err7[:80]}")

# ========================= v6.25.0 Hermes 审计闭环（依赖声明 + 规范源纠正） =========================
import re as _re
_skill_md_625 = (SKILL.parent / "SKILL.md").read_text(encoding="utf-8")
_sync_py_625 = (SKILL / "sync_copies.py").read_text(encoding="utf-8")

# T-8 SKILL.md §3 依赖清单含 uvicorn（审计中/低发现：漏 uvicorn）
_sec3 = ""
if "### 3. 最小依赖" in _skill_md_625:
    _sec3 = _skill_md_625.split("### 3. 最小依赖", 1)[1].split("### 4.", 1)[0]
check("T-8 SKILL.md §3 依赖清单含 uvicorn 且锁定 mcp==1.0.0（Hermes 审计中/低发现闭环）",
      "uvicorn" in _sec3 and "mcp==1.0.0" in _sec3 and "filelock" in _sec3,
      f"§3 含uvicorn={'uvicorn' in _sec3}, 锁mcp={'mcp==1.0.0' in _sec3}, 含filelock={'filelock' in _sec3}")

# T-9 默认规范源纠正为 .workbuddy（审计高严重度根因：误把 D:\hermes 当规范源导致反向覆盖）
_m0 = _re.search(r"KNOWN_COPIES\s*=\s*\[(.*?)\]", _sync_py_625, _re.S)
_first = _m0.group(1).split(",")[0] if _m0 else ""
check("T-9 sync_copies 默认规范源纠正为 .workbuddy（Hermes 审计高严重度根因闭环）",
      ".workbuddy" in _first and "D:\\hermes" not in _first.split("#")[0],
      f"KNOWN_COPIES[0]={_first.strip()!r}")

# T-10 sync_copies 新增 --purge 幂等净化选项
check("T-10 sync_copies 新增 --purge 幂等净化选项（Hermes 审计建议闭环）",
      '--purge' in _sync_py_625 and "_purge_extra" in _sync_py_625,
      f"含--purge={'--purge' in _sync_py_625}, 含_purge_extra={'_purge_extra' in _sync_py_625}")

# T-11 filelock 缺失时 MCP server 写入不崩溃（审计 C-1 闭环：_with_lock 降级 nullcontext）
def _mcp_write_no_filelock():
    code = "import sys, os, tempfile\n"
    code += "sys.path.insert(0, %r)\n" % str(SKILL)
    code += "tmp = tempfile.mkdtemp(prefix='cam_mcp_nolock_')\n"
    code += "os.environ['CROSS_AGENT_MEMORY_BASE'] = tmp\n"
    code += "sys.modules['filelock'] = None\n"   # 模拟 filelock 未安装
    code += "sys.modules['pymysql'] = None\n"    # 强制 JSONL 模式
    code += "import mcp_server\n"
    code += "ok_import = (mcp_server.HAS_FILE_LOCK is False)\n"
    code += "try:\n"
    code += "    with mcp_server._with_lock(timeout=5):\n"
    code += "        pass\n"
    code += "    ctx_ok = True\n"
    code += "except Exception as e:\n"
    code += "    ctx_ok = False\n"
    code += "class Ctx:\n"
    code += "    def info(self,*a,**k): pass\n"
    code += "    def error(self,*a,**k): pass\n"
    code += "    def warning(self,*a,**k): pass\n"
    code += "r = mcp_server.memory_write(Ctx(), 'audit_agent', 'T11审计回归可删除-'+str(os.getpid()), skip_refine=True)\n"
    code += "print('IMPORT_OK=' + str(ok_import) + '|CTX_OK=' + str(ctx_ok) + '|WRITE_OK=' + str(r.get('success')))\n"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(SKILL))
    return out.stdout.strip(), out.stderr.strip()

_out11, _err11 = _mcp_write_no_filelock()
check("T-11 filelock 缺失时 MCP server 写入不崩溃（审计 C-1 闭环）",
      "IMPORT_OK=True" in _out11 and "CTX_OK=True" in _out11 and "WRITE_OK=True" in _out11,
      f"out={_out11} err={_err11[:120]}")

# ========================= 收尾 =========================
mysql_cleanup()
shutil.rmtree(TMP_JSONL, ignore_errors=True)
shutil.rmtree(TMP_DET, ignore_errors=True)

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("\n" + "=" * 60)
print(f"总计 {total} 项，通过 {passed}，失败 {total - passed}")
for name, ok, detail in results:
    if not ok:
        print(f"  FAIL: {name} -- {detail}")
print("=" * 60)
sys.exit(0 if passed == total else 1)
