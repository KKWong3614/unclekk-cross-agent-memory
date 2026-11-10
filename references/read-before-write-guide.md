# 先读后动 (Read Before Write) Implementation Guide

**Created**: 2026-06-16
**Principle**: 所有Agent必须严格遵循"先读后动"原则，这是数据安全和操作可逆的基础。

## 核心规则

| 操作类型 | 先读（必须） | 后动（条件） |
|----------|-------------|-------------|
| **写入记忆** | 先读取现有记忆，检查是否重复 | 仅在无重复时写入，有重复时合并 |
| **更新记忆** | 先读取当前记忆内容 | 仅更新变化的部分，保留其他字段 |
| **删除记忆** | 先确认记忆存在 | 删除前创建备份 |
| **系统升级** | 先读取当前版本和changelog | 评估兼容性后再升级 |
| **迁移数据** | 先扫描源数据 | 确认目标表结构后再迁移 |

## 实现模式

### 模式1: 写入操作
```python
def write_data(data):
    # 【先读后动】Step 1: 先读取
    existing = read_existing(data.id)
    
    # Step 2: 判断
    if existing:
        # 合并策略
        merged = merge(existing, data)
        return update(merged)
    else:
        # 新写入
        return insert(data)
```

### 模式2: 升级操作
```python
def upgrade_system(component):
    # 【先读后动】Step 1: 先读取当前状态
    current_version = get_version(component)
    changelog = get_changelog(component)
    
    # Step 2: 评估
    if has_breaking_changes(changelog):
        create_backup()
    
    # Step 3: 执行
    perform_upgrade(component)
```

### 模式3: 迁移操作
```python
def migrate_data(source, target):
    # 【先读后动】Step 1: 先扫描源数据
    records = scan_source(source)
    
    # Step 2: 确认目标结构
    verify_target_schema(target)
    
    # Step 3: 执行迁移
    for record in records:
        write_to_target(target, record)
```

## 验证点

🛑 **STOP**: 任何写入/更新/删除操作前 — 必须先读取目标数据，确认当前状态
🔴 **CHECKPOINT**: 写入后 — 必须验证写入成功，读取确认
🛑 **STOP**: 系统升级前 — 必须先读取changelog，评估兼容性

## 注释规范

在代码中使用以下注释标记先读后动原则：
```python
# 【先读后动】Step 1: 先读取，检查是否已存在
# 【先读后动】Step 2a: 已存在，执行合并
# 【先读后动】Step 2b: 不存在，执行新写入
```

## Pitfalls

- ❌ 直接写入而不检查是否存在
- ❌ 升级前不创建备份
- ❌ 迁移前不验证目标表结构
- ❌ 删除前不确认数据存在
