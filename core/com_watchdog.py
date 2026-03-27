# -*- coding: utf-8 -*-
"""
DocMaster Agent - COM 安全锁（看门狗）

为所有 Word COM 操作提供四重防护：
  1. 写前快照（Snapshot）：备份原文件
  2. 进程隔离（Isolation）：DispatchEx 强制新进程，不挂用户的 Word
  3. 精准查杀（Watchdog）：Timer 超时后按 PID 精准狙击，不误杀
  4. 异常回滚（Rollback）：异常或超时时自动恢复快照

用法：
  with COMSafeLock("论文.docx", timeout_sec=30) as doc:
      # doc 是一个安全的 Word Document 对象
      # 无论里面发生什么，文件都不会被损坏
      para = doc.Paragraphs(1)
      para.Range.Font.Size = 12

关键工程细节：
  - DispatchEx vs Dispatch：前者强制新进程，后者复用已有进程
  - PID 差集法：通过前后 psutil 扫描定位 Agent 拉起的 Word PID
  - DisplayAlerts=False：防止隐藏弹窗导致 COM 线程死锁
  - pythoncom.CoInitialize：确保 COM 线程模型正确初始化
"""

import os
import shutil
import threading
import time
from contextlib import ContextDecorator

from core.logger import logger


class COMSafeLock(ContextDecorator):
    """
    Word COM 安全上下文管理器。

    封装了"备份→隔离→看门狗→回滚"四重防线，
    让 Word 操作具备类似数据库事务的原子性。
    """

    def __init__(self, doc_path: str, timeout_sec: int = 30):
        self.doc_path = os.path.abspath(doc_path)
        self.backup_path = self.doc_path + ".safebak"
        self.timeout_sec = timeout_sec

        self.word_app = None
        self.doc = None
        self.target_pids: set = set()
        self.timer = None
        self.is_timeout = False

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

    def _watchdog_kill(self):
        """看门狗超时：精准击杀 Agent 拉起的 Word 进程"""
        self.is_timeout = True
        logger.warning(
            "[COMSafeLock] ⏰ 执行超过 %ds！正在强杀僵尸进程...",
            self.timeout_sec,
        )
        try:
            import psutil
            for pid in self.target_pids:
                try:
                    psutil.Process(pid).kill()
                    logger.warning(
                        "[COMSafeLock] 已击杀 Word 进程 (PID: %d)", pid
                    )
                except psutil.NoSuchProcess:
                    pass
        except ImportError:
            # psutil 不可用时回退到 taskkill（只杀我们知道的 PID）
            import subprocess
            for pid in self.target_pids:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                )

    def __enter__(self):
        """
        进入安全区：
          1. 备份原文件
          2. 拉起隔离的 Word 进程
          3. 启动看门狗
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

        # ── 3. 启动看门狗 ──
        self.timer = threading.Timer(self.timeout_sec, self._watchdog_kill)
        self.timer.daemon = True
        self.timer.start()

        # ── 4. 打开文档 ──
        self.doc = self.word_app.Documents.Open(self.doc_path)
        return self.doc

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        退出安全区：
          - 正常退出 → 保存文档
          - 异常/超时 → 放弃保存 + 回滚快照
          - 无论如何 → 关闭 Word + 清理
        """
        # 1. 停止看门狗
        if self.timer:
            self.timer.cancel()

        try:
            if not self.is_timeout and exc_type is None:
                # 正常完成 → 保存
                if self.doc:
                    self.doc.Save()
                logger.debug("[COMSafeLock] ✅ 操作完成，修改已保存")
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

            # 4. 清理快照
            if os.path.exists(self.backup_path):
                os.remove(self.backup_path)

        # 返回 False → 异常继续向外抛，让 Agent 捕获到 Observation
        return False
