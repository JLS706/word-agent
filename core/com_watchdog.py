# -*- coding: utf-8 -*-
"""
DocMaster Agent - COM 安全锁（心跳看门狗）

为所有 Word COM 操作提供四重防护：
  1. 写前快照（Snapshot）：备份原文件
  2. 进程隔离（Isolation）：DispatchEx 强制新进程，不挂用户的 Word
  3. 心跳看门狗（Heartbeat Watchdog）：外层事件泵监控进度心跳，
     停滞超时后按 PID 精准狙击。大文档不会被误杀，
     弹窗死锁则能被秒级发现。
  4. 异常回滚（Rollback）：异常或超时时自动恢复快照

用法（工具内部）：
  lock = COMSafeLock("论文.docx")
  with lock as doc:
      lock.heartbeat()  # 工具每完成一个子步骤就跟事件泵报平安
      para = doc.Paragraphs(1)
      para.Range.Font.Size = 12
      lock.heartbeat()

心跳服务约定：
  - 工具调用 report_progress() → 异步引擎自动调用 lock.heartbeat()
  - 外层事件泵每 100ms 检查 lock.stall_seconds()
  - stall_seconds() > stall_timeout → lock.kill_target() → 回滚
  - 大文档正常处理 2 分钟？只要心跳不停，绝不误杀
  - 2页文档弹窗死锁？5秒无心跳，精准击杀

关键工程细节：
  - DispatchEx vs Dispatch：前者强制新进程，后者复用已有进程
  - PID 差集法：通过前后 psutil 扫描定位 Agent 拉起的 Word PID
  - DisplayAlerts=False：防止隐藏弹窗导致 COM 线程死锁
  - pythoncom.CoInitialize：确保 COM 线程模型正确初始化
"""

import os
import shutil
import time
from contextlib import ContextDecorator

from core.logger import logger


class COMSafeLock(ContextDecorator):
    """
    Word COM 安全上下文管理器（心跳看门狗版）。

    封装了“备份→隔离→心跳→回滚”四重防线。
    不再内置绝对超时 Timer，而是暴露 heartbeat()/stall_seconds()/
    kill_target() 给外层事件泵做心跳停滞检测。
    """

    # 类级别 PID 注册表：所有活跃 COMSafeLock 实例的目标 PID 汇总。
    # 供外层事件泵在心跳停滞时精准击杀，无需持有 lock 实例引用。
    # CPython GIL 保证 set 操作的线程安全。
    _active_target_pids: set = set()

    @classmethod
    def get_active_pids(cls) -> set:
        """返回所有活跃 COMSafeLock 正在监控的 Word PID 集合的副本。"""
        return set(cls._active_target_pids)

    def __init__(self, doc_path: str, stall_timeout: float = 5.0,
                 read_only: bool = False):
        """
        Args:
            doc_path: Word 文档路径
            stall_timeout: 心跳停滞超时（秒）。
                连续 stall_timeout 秒没有收到 heartbeat() 调用则视为假死。
                默认 5.0秒 —— 对 COM 弹窗死锁的发现速度是原来 30秒的 6倍。
            read_only: 只读模式。为 True 时退出不保存文档（用于只读检测工具）。
        """
        self.doc_path = os.path.abspath(doc_path)
        self.backup_path = self.doc_path + ".safebak"
        self.stall_timeout = stall_timeout
        self.read_only = read_only

        self.word_app = None
        self.doc = None
        self.target_pids: set = set()
        self.is_timeout = False

        # 心跳时间戳（线程安全：time.time() 是原子读写）
        self._last_heartbeat: float = time.time()

    def heartbeat(self) -> None:
        """工具每完成一个子步骤就调用一次，刷新心跳时间戳。"""
        self._last_heartbeat = time.time()

    def stall_seconds(self) -> float:
        """返回距离上次心跳已过去的秒数。"""
        return time.time() - self._last_heartbeat

    def is_stalled(self) -> bool:
        """心跳是否已停滞超过阈值。"""
        return self.stall_seconds() > self.stall_timeout

    def _get_word_pids(self) -> set:
        """获取当前所有 WINWORD.EXE 进程 PID"""
        try:
            import psutil
            return {
                p.pid for p in psutil.process_iter(['name'])
                if p.info['name']
                and p.info['name'].lower() == 'winword.exe'
            }
        except ImportError:
            logger.warning("[COMSafeLock] psutil 未安装，无法精准追踪 PID")
            return set()

    def kill_target(self) -> list[int]:
        """
        精准击杀 Agent 拉起的 Word 进程。

        由外层事件泵在检测到心跳停滞后调用。

        Returns:
            已击杀的 PID 列表
        """
        self.is_timeout = True
        killed = []
        stall = self.stall_seconds()
        logger.warning(
            "[COMSafeLock] ⏰ 心跳停滞 %.1f秒（阈值 %.1f秒），正在精准击杀...",
            stall, self.stall_timeout,
        )
        try:
            import psutil
            for pid in self.target_pids:
                try:
                    psutil.Process(pid).kill()
                    killed.append(pid)
                    logger.warning(
                        "[COMSafeLock] 已击杀 Word 进程 (PID: %d)", pid
                    )
                except psutil.NoSuchProcess:
                    pass
        except ImportError:
            import subprocess
            for pid in self.target_pids:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
                killed.append(pid)
        return killed

    @staticmethod
    def kill_pids(pids: set) -> list[int]:
        """
        静态方法：按 PID 集合击杀 Word 进程。

        供 Agent 事件泵在没有 COMSafeLock 实例时直接调用。
        """
        killed = []
        try:
            import psutil
            for pid in pids:
                try:
                    psutil.Process(pid).kill()
                    killed.append(pid)
                except psutil.NoSuchProcess:
                    pass
        except ImportError:
            import subprocess
            for pid in pids:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
                killed.append(pid)
        return killed

    def __enter__(self):
        """
        进入安全区：
          1. 备份原文件
          2. 拉起隔离的 Word 进程
          3. 初始化心跳时间戳
          4. 打开文档
        """
        # ── 1. 写前快照 ──
        shutil.copy2(self.doc_path, self.backup_path)
        logger.debug("[COMSafeLock] 📸 快照已创建: %s", self.backup_path)

        # 记录启动前的 Word 进程
        pids_before = self._get_word_pids()

        # ── 2. 进程隔离 ──
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()

        # 核心：DispatchEx 强制创建新进程（不复用用户的 Word）
        self.word_app = win32com.client.DispatchEx("Word.Application")
        self.word_app.Visible = False
        self.word_app.DisplayAlerts = False  # 防弹窗死锁

        # PID 差集法：精准定位 Agent 拉起的 Word
        time.sleep(0.5)
        pids_after = self._get_word_pids()
        self.target_pids = pids_after - pids_before

        if self.target_pids:
            logger.debug(
                "[COMSafeLock] 🎯 锁定 Word PID: %s", self.target_pids
            )
            # 注册到类级别 PID 表，供外层事件泵使用
            COMSafeLock._active_target_pids |= self.target_pids

        # ── 3. 初始化心跳 ──
        self._last_heartbeat = time.time()

        # ── 4. 打开文档 ──
        self.doc = self.word_app.Documents.Open(self.doc_path)
        self.heartbeat()  # 文档打开成功 = 第一次心跳
        return self.word_app, self.doc

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        退出安全区：
          - 正常退出 → 保存文档
          - 异常/心跳停滞超时 → 放弃保存 + 回滚快照
          - 无论如何 → 关闭 Word + 清理
        """
        try:
            if not self.is_timeout and exc_type is None:
                # 正常完成 → 保存（只读模式跳过）
                if self.doc and not self.read_only:
                    self.doc.Save()
                logger.debug("[COMSafeLock] ✅ 操作完成%s",
                             "（只读，未保存）" if self.read_only else "，修改已保存")
            else:
                logger.warning(
                    "[COMSafeLock] ⚠️ 异常或超时，放弃保存 (exc=%s)",
                    exc_type.__name__ if exc_type else "timeout",
                )
        except Exception as e:
            logger.error("[COMSafeLock] 保存/关闭时出错: %s", e)
        finally:
            # 2. 优雅退出
            try:
                if self.doc:
                    self.doc.Close(SaveChanges=0)
            except Exception:
                pass
            try:
                if self.word_app:
                    self.word_app.Quit()
            except Exception:
                pass  # 被看门狗杀了就会报错，忽略

            # COM 反初始化
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

            # 3. 事务回滚
            if self.is_timeout or exc_type is not None:
                logger.warning("[COMSafeLock] 🔄 正在回滚到快照...")
                shutil.copy2(self.backup_path, self.doc_path)
                logger.info("[COMSafeLock] ✅ 回滚完成，文件已恢复")

            # 4. 从类级别 PID 表中注销
            COMSafeLock._active_target_pids -= self.target_pids

            # 5. 清理快照
            if os.path.exists(self.backup_path):
                os.remove(self.backup_path)

        # 返回 False → 异常继续向外抛，让 Agent 捕获到 Observation
        return False
