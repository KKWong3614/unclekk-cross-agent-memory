# MySQL Windows Setup Guide

**Created**: 2026-06-16
**Purpose**: 在 Windows 上安装和配置 MySQL 8.0

## 安装步骤

### 1. 下载 MySQL 8.0 ZIP 免安装版
```bash
curl -L "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-winx64.zip" -o /tmp/mysql-8.0.36-winx64.zip
```

### 2. 解压到 D:\mysql
```bash
mkdir -p /d/mysql
unzip /tmp/mysql-8.0.36-winx64.zip -d /d/
```

### 3. 创建配置文件 my.ini
```ini
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
```

### 4. 初始化数据库
```bash
/d/mysql-8.0.36-winx64/bin/mysqld.exe --initialize-insecure --console
```

### 5. 启动 MySQL
```bash
# 后台启动
/d/mysql-8.0.36-winx64/bin/mysqld.exe --defaults-file="D:/mysql-8.0.36-winx64/my.ini" &
```

### 6. 创建数据库和表
```sql
CREATE DATABASE IF NOT EXISTS cross_agent_memory 
  CHARACTER SET utf8mb4 
  COLLATE utf8mb4_unicode_ci;

USE cross_agent_memory;

CREATE TABLE IF NOT EXISTS memories (
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
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS memory_agents (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  fact_id VARCHAR(64) NOT NULL,
  agent_id VARCHAR(64) NOT NULL,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_fact_agent (fact_id, agent_id),
  FOREIGN KEY (fact_id) REFERENCES memories(fact_id) ON DELETE CASCADE
) ENGINE=InnoDB;
```

> **注意**：审计日志不走 MySQL。代码通过 `log_audit()` 追加写入本地 JSONL（`AUDIT_DIR/<日期>.jsonl`），无需建表。

## Python 连接

```python
import pymysql

conn = pymysql.connect(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="",  # 初始化时无密码
    database="cross_agent_memory",
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)
```

## 安装 pymysql
```bash
python -m pip install pymysql
```

## Pitfalls

- ❌ 使用 `--initialize` 而非 `--initialize-insecure`（会生成随机密码）
- ❌ 忘记设置 `character-set-server=utf8mb4`（中文乱码）
- ❌ 使用 `default-authentication-plugin=caching_sha2_password`（旧客户端不支持）
- ❌ Windows 服务安装需要管理员权限（直接启动更简单）
