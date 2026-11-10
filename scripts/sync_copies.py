#!/usr/bin/env python3
"""
cross-agent-memory 多副本同步工具（P2-4 第四轮审计：终结版本漂移）

背景：本技能在本机存在多个物理副本（Hermes 规范目录 / WorkBuddy / OpenClaw /
QClaw / 发布暂存区），历史上多次出现"改进落在 A 副本、审计与调用读 B 副本"的
版本漂移（审计误判、多 Agent 加载旧缺陷代码的根源）。

用法（默认规范源 = WorkBuddy 技能目录 C:\\Users\\Administrator\\.workbuddy\\skills\\cross-agent-memory，
      即本技能编辑与 SkillHub 发布的源头；Hermes 副本 D:\\hermes 为被同步的消费者之一）:
  python sync_copies.py                 # 把规范源（.workbuddy）同步到所有已知副本
  python sync_copies.py --dry-run       # 只显示计划，不写入
  python sync_copies.py --source <dir>  # 指定其他规范源
  python sync_copies.py --temp          # 额外包含 AppData\\Local\\Temp 下的发布暂存副本
  python sync_copies.py --purge         # 同步后删除目标端 CORE_DIRS 内、源端不存在的多余文件（幂等净化）
  python sync_copies.py --quarantine    # 同步后把目标端 CORE_DIRS 内、源端不存在的多余文件移入 .quarantine_pollution/（可恢复，不删）

同步语义（保守）：
- 只覆盖核心文件：SKILL.md / CHANGELOG.md / EXAMPLES.md / README.md / VERSION /
  requirements.txt 及 scripts/、references/、tests/ 目录
- 绝不删除目标副本的独有产物（memory_data/、templates/、AUDIT-*.md、results.tsv、
  test-prompts.json、trash、dev-notes/、.quarantine_*/ 等运行时/归档文件一律保留）
- --purge 仅清理 CORE_DIRS（scripts/references/tests）内「源端没有、目标端有」的文件，
  用于消除历史上反向同步/污染留下的多余脚本；不触碰运行数据、dev-notes、隔离区
- --quarantine 同 --purge 的判定，但把多余文件**移入目标端的 .quarantine_pollution/ 子目录**
  （保留相对路径、可恢复），而非删除——适合 Documents / 重要副本等不可硬删的场景
- 同步后清理 scripts/tests 下的 __pycache__
- 逐副本校验 version 与 @mcp.tool 计数并打印

注意：运行中的 Agent 进程不会热加载技能文件，同步后建议重启相关会话/服务。
  默认规范源为 .workbuddy（编辑源头）；若从 D:\\hermes 编辑，务必显式加
  `--source D:\\hermes\\skills\\cross-agent-memory`，否则会以 .workbuddy 覆盖 D:\\hermes，
  导致本地编辑被冲掉（历史上「审计误判/加载未修复代码」的根因）。
"""

import argparse
import glob
import shutil
import sys
from pathlib import Path

# 核心文件/目录：跟随规范源覆盖
CORE_FILES = ["SKILL.md", "CHANGELOG.md", "EXAMPLES.md", "README.md", "VERSION", "requirements.txt"]
CORE_DIRS = ["scripts", "references", "tests"]

# 已知副本（不存在则跳过）。规范源默认放第一位。
# P2-4'（第五轮审计）：补齐全盘盘点发现的全部活跃副本（此前漏 7 个，含 Obsidian 共享记忆库）；
# 备份/历史归档（.bak*、-backup-*、-old-to-remove、Downloads 历史包等）一律不入清单、保持原样。
# ⚠️ 以下路径为本机（Windows / 作者机）实际布局，非可移植清单。
#    外部用户可清空此清单——工具本身仍可用：用 --source <dir> 指定规范源，副本路径按需自行编辑；
#    不存在的路径会被 exists() 守卫自动跳过，不会报错。
KNOWN_COPIES = [
    r"C:\Users\Administrator\.workbuddy\skills\cross-agent-memory",                # [0] 默认规范源：编辑 + SkillHub 发布源头
    r"D:\hermes\skills\cross-agent-memory",                                        # Hermes 副本（审计读取，被同步的消费者）
    r"C:\Users\Administrator\.openclaw\workspace\skills\cross-agent-memory",       # OpenClaw
    r"C:\Users\Administrator\.qclaw\workspace\obsidian-shared-library\02_Skills\cross-agent-memory",  # QClaw Obsidian 共享库
    r"C:\Users\Administrator\.qclaw\workspace-ua58rsb93veqtxl7\skills\cross-agent-memory",            # QClaw workspace
    r"D:\hermes\skills\skills\cross-agent-memory",                                 # Hermes 嵌套路径（工具链引用）
    r"D:\hermes\skills\software-development\cross-agent-memory",                   # Hermes software-development 分类
    r"D:\hermes\skills\cross-agent-memory-workbuddy-sync",                         # Hermes 侧 workbuddy-sync 变体
    r"D:\我的文档\Documents\Obsidian Vault\06-共享记忆库\02_Skills\cross-agent-memory",                   # Obsidian 共享记忆库（跨设备同步中心）
    r"D:\我的文档\Documents\Obsidian Vault\06-共享记忆库\02_Skills\cross-agent-memory-workbuddy-sync",    # Obsidian 侧 workbuddy-sync 变体
    r"D:\我的文档\Documents\Obsidian Vault\06-共享记忆库\02_Skills\skills\cross-agent-memory",            # Obsidian skills 分类（--scan 发现）
    r"D:\我的文档\Documents\Obsidian Vault\06-共享记忆库\02_Skills\software-development\cross-agent-memory",  # Obsidian software-development 分类（--scan 发现）
    r"C:\Users\Administrator\AppData\Local\Temp\cross-agent-memory-release",       # 发布暂存（release）
    r"D:\d\hermes\skills\cross-agent-memory",                                      # 历史镜像路径（顺带统一，防止读到旧版）
]

TEMP_GLOB = r"C:\Users\Administrator\AppData\Local\Temp\tmp.*\*\cross-agent-memory"

# --scan 盘点根目录与深度（只读，不写入）
SCAN_ROOTS = [
    ("C:\\Users\\Administrator", 6),
    ("D:\\", 7),
]


def _clean_pycache(base: Path):
    for sub in ("scripts", "tests"):
        d = base / sub
        if d.is_dir():
            for pyc in d.rglob("__pycache__"):
                shutil.rmtree(pyc, ignore_errors=True)


def _purge_extra(src_dir: Path, dst_dir: Path, dry_run: bool) -> list[str]:
    """删除 dst_dir 中、源端 src_dir 不存在的文件（幂等净化，仅在 CORE_DIRS 内）。

    - src_dir 不存在时跳过该目录（不误删目标端整目录）。
    - 不触碰 src 端也存在的文件；不触及 CORE_DIRS 之外的任何内容
      （memory_data/、dev-notes/、.quarantine_*/ 等均在 CORE_DIRS 外，安全）。
    """
    removed: list[str] = []
    if not dst_dir.is_dir():
        return removed
    if not src_dir.is_dir():
        return removed
    src_files = {str(p.relative_to(src_dir)) for p in src_dir.rglob("*") if p.is_file()}
    for p in sorted(dst_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(dst_dir))
        if rel not in src_files:
            if dry_run:
                removed.append(f"[dry-run] 将删除 {p}")
            else:
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError as e:
                    removed.append(f"[跳过] {p} 删除失败: {e}")
    return removed


QUARANTINE_DIR = ".quarantine_pollution"


def _quarantine_extra(src_dir: Path, dst_dir: Path, dry_run: bool) -> list[str]:
    """把 dst_dir 中、源端 src_dir 不存在的文件移入 dst_dir.parent/.quarantine_pollution/。

    等价 --purge 的判定，但「移动」而非「删除」——可恢复、不破坏 Documents 等目录结构。
    返回被移动（或 dry-run 下拟移动）的文件列表（相对目标端的路径）。
    """
    moved: list[str] = []
    if not dst_dir.is_dir() or not src_dir.is_dir():
        return moved
    src_files = {str(p.relative_to(src_dir)) for p in src_dir.rglob("*") if p.is_file()}
    q_root = dst_dir.parent / QUARANTINE_DIR
    for p in sorted(dst_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(dst_dir))
        if rel in src_files:
            continue
        dest = q_root / p.relative_to(dst_dir)
        if dry_run:
            moved.append(f"[dry-run] 将隔离 {p} -> {dest}")
        else:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                moved.append(str(p.relative_to(dst_dir)))
            except OSError as e:
                moved.append(f"[跳过] {p} 隔离失败: {e}")
    return moved


def _write_quarantine_manifest(target: Path, moved: list[str]) -> None:
    """在 target/.quarantine_pollution/ 下写 MANIFEST.json，便于恢复。"""
    if not moved:
        return
    q_root = target / QUARANTINE_DIR
    q_root.mkdir(parents=True, exist_ok=True)
    import json
    manifest = q_root / "MANIFEST.json"
    data = {
        "quarantined_from": str(target),
        "mode": "moved (recoverable, not deleted)",
        "file_count": len([m for m in moved if not m.startswith("[")]),
        "files": [m for m in moved if not m.startswith("[")],
    }
    try:
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _skill_version(base: Path) -> str:
    f = base / "SKILL.md"
    if not f.exists():
        return "(无 SKILL.md)"
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "(未声明)"


def _tool_count(base: Path) -> int:
    f = base / "scripts" / "mcp_server.py"
    if not f.exists():
        return -1
    return f.read_text(encoding="utf-8", errors="replace").count("@mcp.tool")


def sync(source: Path, target: Path, dry_run: bool, purge: bool = False,
         quarantine: bool = False) -> tuple[bool, str]:
    """把 source 核心文件覆盖到 target。返回 (成功?, 描述)。

    purge=True 时，覆盖后在每个 CORE_DIRS 内删除「源端没有、目标端有」的文件
    （幂等净化，用于消除反向同步/污染遗留）。绝不触碰运行数据、dev-notes、隔离区。
    quarantine=True 同 purge 的判定，但把多余文件**移入目标端 .quarantine_pollution/**
    （可恢复、不删），并在该目录写 MANIFEST.json。purge 优先于 quarantine。
    """
    if not (source / "SKILL.md").exists():
        return False, f"源无效（缺 SKILL.md）: {source}"
    if source.resolve() == target.resolve():
        return True, "跳过（与源相同）"
    if dry_run:
        plan = f"[dry-run] 将覆盖 {len(CORE_FILES)} 文件 + {len(CORE_DIRS)} 目录 → {target}"
        if purge:
            extra = []
            for d in CORE_DIRS:
                extra += _purge_extra(source / d, target / d, dry_run=True)
            plan += f" [+purge 拟删除 {len(extra)} 个目标多余文件]"
        elif quarantine:
            extra = []
            for d in CORE_DIRS:
                extra += _quarantine_extra(source / d, target / d, dry_run=True)
            plan += f" [+quarantine 拟隔离 {len(extra)} 个目标多余文件]"
        return True, plan
    try:
        quarantined: list[str] = []
        for f in CORE_FILES:
            src = source / f
            if src.exists():
                shutil.copy2(src, target / f)
        for d in CORE_DIRS:
            src = source / d
            if not src.is_dir():
                continue
            dst = target / d
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.rglob("*"):
                rel = item.relative_to(src)
                dest = dst / rel
                if item.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
            if purge:
                _purge_extra(src, dst, dry_run=False)
            elif quarantine:
                quarantined += _quarantine_extra(src, dst, dry_run=False)
        if quarantine:
            _write_quarantine_manifest(target, quarantined)
        _clean_pycache(target)
        v = _skill_version(target)
        t = _tool_count(target)
        ok = v.startswith("(未") is False
        if purge:
            tail = " [purge 已清理目标多余文件]"
        elif quarantine:
            tail = f" [quarantine 已隔离 {len([m for m in quarantined if not m.startswith('[')])} 个目标多余文件]"
        else:
            tail = ""
        return ok, f"同步完成 version={v} tools={t}{tail}"
    except Exception as e:
        return False, f"同步失败: {e}"


def do_scan() -> int:
    """全盘盘点所有 cross-agent-memory* 副本（只读）：版本、工具数、是否在同步清单内。

    用 os.walk + 剪枝（跳过 node_modules/.git/缓存/虚拟环境等重目录）+ 深度上限，
    避免整盘 glob 过慢（P2-4' 实测 Path.glob("**") 在用户主目录下会超时）。
    """
    import os

    known = {str(Path(p).resolve()).lower() for p in KNOWN_COPIES}
    prune_names = {
        "node_modules", ".git", "__pycache__", "site-packages", "venv", ".venv",
        "anaconda3", "miniconda3", ".cache", ".npm", ".nuget", ".vscode",
        "WindowsApps", "Recycle Bin", "$Recycle.Bin", "System Volume Information",
    }
    print("=" * 66)
    print("cross-agent-memory 全盘副本盘点（只读，不写入）")
    print("=" * 66)
    found = 0
    unregistered = 0
    for root, depth in SCAN_ROOTS:
        root_p = Path(root)
        if not root_p.exists():
            continue
        root_depth = len(root_p.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_depth = len(Path(dirpath).parts) - root_depth
            if rel_depth >= depth + 1:
                dirnames[:] = []          # 超深，停止下钻
                continue
            if Path(dirpath).name in prune_names:
                dirnames[:] = []          # 剪枝，不下钻
                continue
            dirnames[:] = [d for d in dirnames if d not in prune_names]
            name = Path(dirpath).name
            if name.startswith("cross-agent-memory"):
                h = Path(dirpath)
                found += 1
                rp = str(h.resolve()).lower()
                tag = "已登记" if rp in known else "清单外!"
                if rp not in known:
                    unregistered += 1
                print(f"[{tag}] {h}  (version={_skill_version(h)}, tools={_tool_count(h)})")
    print()
    print(f"共发现 {found} 个副本目录，其中 {unregistered} 个不在同步清单内。")
    print("说明：清单外目录若为备份/历史归档属正常；若为活跃副本，请手动加入 KNOWN_COPIES。")
    return 0


def _resolve_default_source() -> Path:
    """解析默认规范源。

    优先用 KNOWN_COPIES[0]（作者机器上的 .workbuddy 源）；若该路径不存在
    （如 CI runner 或非 Windows 环境），回退到本脚本所在技能目录
    （scripts/ 的上一级），保证任意平台都可运行，不会被写死的 Windows 路径卡死。
    """
    preferred = Path(KNOWN_COPIES[0])
    if (preferred / "SKILL.md").exists():
        return preferred
    fallback = Path(__file__).resolve().parent.parent  # <skill>/
    if (fallback / "SKILL.md").exists():
        return fallback
    return preferred  # 实在找不到就保留，让下方校验给出明确报错


def main() -> int:
    parser = argparse.ArgumentParser(description="cross-agent-memory 多副本同步（终结版本漂移）")
    parser.add_argument("--source", type=str, default=None,
                        help="规范源目录（默认 C:\\Users\\Administrator\\.workbuddy\\skills\\cross-agent-memory）")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不写入")
    parser.add_argument("--temp", action="store_true",
                        help="额外包含 AppData\\Local\\Temp 下的发布暂存副本")
    parser.add_argument("--purge", action="store_true",
                        help="同步后删除目标端 CORE_DIRS 内、源端不存在的多余文件（幂等净化；"
                             "不触碰运行数据/归档/dev-notes/.quarantine）")
    parser.add_argument("--quarantine", action="store_true",
                        help="同步后把目标端 CORE_DIRS 内、源端不存在的多余文件移入 .quarantine_pollution/（可恢复、不删）")
    parser.add_argument("--scan", action="store_true",
                        help="全盘盘点所有 cross-agent-memory* 目录的版本（只读，不写入），"
                             "用于发现清单外副本（P2-4' 新增）")
    args = parser.parse_args()

    if args.scan:
        return do_scan()

    source = Path(args.source) if args.source else _resolve_default_source()
    if not (source / "SKILL.md").exists():
        print(f"[ERROR] 规范源无效（缺 SKILL.md）: {source}")
        return 1

    targets = [Path(p) for p in KNOWN_COPIES]
    if args.temp:
        targets += [Path(p) for p in glob.glob(TEMP_GLOB)]

    print("=" * 66)
    print("cross-agent-memory 多副本同步")
    print("=" * 66)
    print(f"规范源: {source}  (version={_skill_version(source)}, tools={_tool_count(source)})")
    _mode = "dry-run 预览" if args.dry_run else "实际同步"
    _flag = " [+purge]" if args.purge else (" [+quarantine]" if args.quarantine else "")
    print(f"模式: {_mode}{_flag}")
    print()

    all_ok = True
    for t in targets:
        if t.resolve() == source.resolve():
            label = "[源]"
        elif not t.exists():
            label = "[不存在→跳过]"
            print(f"{label} {t} —— 目录不存在，跳过")
            continue
        elif not (t / "SKILL.md").exists():
            # P3（第六轮审计）：区分「目录不存在」与「目录存在但缺 SKILL.md」
            label = "[空目录→跳过]"
            print(f"{label} {t} —— 目录存在但缺 SKILL.md（空目录或损坏），跳过；若为活跃副本请先检查")
            continue
        else:
            label = "[副本]"
        ok, msg = sync(source, t, args.dry_run, args.purge, args.quarantine)
        all_ok = all_ok and ok
        mark = "OK " if ok else "FAIL"
        print(f"[{mark}] {label} {t}")
        print(f"       {msg}")
        if not ok:
            all_ok = False

    print()
    print("同步完成，全部副本一致。" if all_ok else "存在失败项，请检查上方输出。")
    print("提示：运行中的 Agent 不会热加载技能文件，同步后建议重启相关会话。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
