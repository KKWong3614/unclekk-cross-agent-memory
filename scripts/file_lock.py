#!/usr/bin/env python3
"""
跨平台文件锁工具 - Cross-Platform File Locking (改进版)

使用单独的锁文件（.lock）来避免 Windows 文件权限问题。
支持 Windows 和 Unix/Linux/macOS 系统。

用法:
    from file_lock import FileLock

    # 锁定数据文件
    lock = FileLock("/path/to/data.jsonl")
    lock.acquire()
    try:
        # 执行写入操作
        with open("/path/to/data.jsonl", "a") as f:
            f.write(data)
    finally:
        lock.release()

    # 或使用上下文管理器
    with FileLock("/path/to/data.jsonl"):
        with open("/path/to/data.jsonl", "a") as f:
            f.write(data)
"""

import os
import sys
import time
import platform
from pathlib import Path
from typing import Optional


class FileLockException(Exception):
    """文件锁异常"""
    pass


class FileLock:
    """
    跨平台文件锁实现（使用单独的锁文件）

    使用单独的 .lock 文件来避免 Windows 文件权限问题。

    使用方法:
        with FileLock("/path/to/file"):
            # 执行文件操作

    注意:
        - 锁文件: /path/to/file.lock
        - 锁类型: 排他锁
        - 超时机制: 默认 30 秒
    """

    def __init__(self, file_path: str, timeout: float = 30.0, delay: float = 0.05):
        """
        初始化文件锁

        Args:
            file_path: 要保护的数据文件路径
            timeout: 获取锁的超时时间（秒）
            delay: 重试间隔（秒）
        """
        self.file_path = Path(file_path)
        self.lock_file = Path(str(file_path) + ".lock")
        self.timeout = timeout
        self.delay = delay
        self.is_locked = False
        self._lock_fd = None

        # 确保目录存在
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        # 根据系统选择锁实现
        self._is_windows = platform.system() == "Windows"

    def acquire(self):
        """获取文件锁"""
        start_time = time.time()

        while True:
            try:
                # 创建锁文件
                if self._is_windows:
                    self._acquire_windows()
                else:
                    self._acquire_unix()
                self.is_locked = True
                return
            except (IOError, OSError) as e:
                # 锁被占用或失败
                if time.time() - start_time > self.timeout:
                    raise FileLockException(
                        f"无法获取文件锁: {self.lock_file} (超时 {self.timeout}秒, 错误: {e})"
                    )
                time.sleep(self.delay)

    def release(self):
        """释放文件锁"""
        if not self.is_locked:
            return

        try:
            if self._is_windows:
                self._release_windows()
            else:
                self._release_unix()
            self.is_locked = False
        except Exception as e:
            # 释放锁失败不应该影响程序继续运行
            print(f"警告: 释放文件锁失败: {e}", file=sys.stderr)

    def _acquire_windows(self):
        """Windows 平台获取锁"""
        import msvcrt

        # 以读写模式创建/打开锁文件
        self._lock_fd = open(self.lock_file, "w+b", buffering=0)

        try:
            # 获取排他锁（非阻塞）
            msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            # 写入锁定信息
            self._lock_fd.write(f"locked by pid={os.getpid()}\n".encode())
        except (IOError, OSError):
            self._lock_fd.close()
            self._lock_fd = None
            raise

    def _release_windows(self):
        """Windows 平台释放锁"""
        import msvcrt

        if self._lock_fd:
            try:
                # 释放锁
                self._lock_fd.seek(0)
                msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self._lock_fd.close()
                self._lock_fd = None

        # 删除锁文件
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception as e:
                import sys
                print(f"[WARN] file_lock: 删除锁文件失败: {e}", file=sys.stderr)

    def _acquire_unix(self):
        """Unix/Linux/macOS 平台获取锁"""
        import fcntl

        # 打开锁文件
        self._lock_fd = open(self.lock_file, "w")

        try:
            # 获取排他锁（非阻塞）
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 写入锁定信息
            self._lock_fd.write(f"locked by pid={os.getpid()}\n")
            self._lock_fd.flush()
        except (IOError, OSError):
            self._lock_fd.close()
            self._lock_fd = None
            raise

    def _release_unix(self):
        """Unix/Linux/macOS 平台释放锁"""
        import fcntl

        if self._lock_fd:
            try:
                # 释放锁
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_fd.close()
                self._lock_fd = None

        # 删除锁文件
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception as e:
                import sys
                print(f"[WARN] file_lock: 删除锁文件失败: {e}", file=sys.stderr)

    def __enter__(self):
        """上下文管理器入口"""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.release()
        return False  # 不抑制异常

    def __del__(self):
        """析构函数，确保锁被释放"""
        if self.is_locked:
            self.release()


def with_file_lock(file_path: str, timeout: float = 30.0):
    """
    文件锁上下文管理器（工厂函数）

    用法:
        with with_file_lock("/path/to/file"):
            with open("/path/to/file", "a") as f:
                f.write(data)

    Args:
        file_path: 文件路径
        timeout: 超时时间

    Returns:
        FileLock 实例
    """
    return FileLock(file_path, timeout=timeout)


# 测试代码
if __name__ == "__main__":
    import tempfile

    # 创建临时文件测试
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as tmp:
        test_file = tmp.name

    print(f"测试文件: {test_file}")

    # 测试 1: 基本锁定和解锁
    print("\n测试 1: 基本锁定和解锁")
    try:
        with FileLock(test_file) as lock:
            print("  [OK] 获取锁成功")
            time.sleep(0.5)  # 模拟写入操作
        print("  [OK] 释放锁成功")
    except Exception as e:
        print(f"  [X] 测试失败: {e}")

    # 测试 2: 写入操作
    print("\n测试 2: 写入操作")
    try:
        with FileLock(test_file):
            # 在持有锁的情况下写入数据文件（不是锁文件）
            with open(test_file, "a", encoding="utf-8") as f:
                f.write('{"test": "data"}\n')
        print("  [OK] 写入成功")
    except Exception as e:
        print(f"  [X] 写入失败: {e}")

    # 测试 3: 上下文管理器
    print("\n测试 3: 上下文管理器")
    try:
        with with_file_lock(test_file):
            with open(test_file, "a", encoding="utf-8") as f:
                f.write('{"test": "data2"}\n')
        print("  [OK] 上下文管理器测试成功")
    except Exception as e:
        print(f"  [X] 测试失败: {e}")

    # 测试 4: 读取文件内容
    print("\n测试 4: 读取文件内容")
    try:
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
        print(f"  文件内容: {content.strip()}")
    except Exception as e:
        print(f"  [X] 读取失败: {e}")

    # 清理
    Path(test_file).unlink()
    print("\n所有测试完成！")
