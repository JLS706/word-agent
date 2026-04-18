# -*- coding: utf-8 -*-
"""
DocMaster - Workspace Provider（工作区抽象层）

为每个 Worker 子任务提供隔离的文件系统工作区。

架构分层：
  WorkspaceProvider (ABC)
    ├── LocalFolderWorkspace   ← MVP：本地临时文件夹隔离
    └── WindowsSandboxWorkspace← 未来：Windows Sandbox / Docker 容器
    └── DockerWorkspace        ← 未来：Linux Docker 容器（CI/CD 场景）

设计哲学：
  Worker 永远不直接碰用户的原文件。
  所有修改都在工作区的深拷贝上进行。
  成功 → 把结果拷贝回原路径；失败 → 工作区核平销毁，原文件毫发无伤。

用法（在 DelegateTaskTool 中）：
  workspace = LocalFolderWorkspace()
  with workspace.session(task_id, original_file) as ctx:
      # ctx.work_path  → 工作区中的文件副本路径
      # ctx.workspace_dir → 工作区根目录
      worker.run(f"请处理文件: {ctx.work_path}")
      # 成功：ctx.commit() 将结果回写原路径
  # with 退出时自动 cleanup（无论成功失败）
"""

import os
import shutil
import uuid
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass

from core.logger import logger


# ─────────────────────────────────────────────
# 工作区会话上下文
# ─────────────────────────────────────────────

@dataclass
class WorkspaceContext:
    """工作区会话的运行时上下文，传递给 Worker 使用。"""

    task_id: str
    workspace_dir: str      # 工作区根目录（如 C:\temp\ws_abc123\）
    work_path: str           # 工作区中文档副本的完整路径
    original_path: str       # 用户原始文件路径（仅用于回写）
    _committed: bool = False

    def commit(self, output_path: str = None):
        """
        将工作区中修改后的文件回写到原路径。

        只有显式调用 commit() 才会覆盖原文件。
        如果 Worker 崩溃或任务失败，不调用此方法即可保证原文件安全。

        Args:
            output_path: Worker 实际产出的文件路径。
                - None（默认）→ 使用 self.work_path（modify_in_place 场景）
                - 显式路径 → 必须位于工作区内（防止路径逃逸攻击）
                  适用于 Worker 违规生成衍生文件如 `论文_processed.docx`
        """
        source = output_path if output_path else self.work_path

        # 统一转为绝对路径
        source_abs = os.path.abspath(source)
        workspace_abs = os.path.abspath(self.workspace_dir)

        # 安全检查：source 必须在工作区内（防止路径逃逸）
        # 用 os.path.commonpath 比字符串 startswith 更鲁棒
        try:
            common = os.path.commonpath([source_abs, workspace_abs])
        except ValueError:
            # 不同驱动器等情况
            common = ""
        if common != workspace_abs:
            logger.warning(
                "[Workspace] commit 拒绝：output_path 在工作区外 %s（工作区=%s）",
                source_abs, workspace_abs,
            )
            return

        if not os.path.exists(source_abs):
            logger.warning(
                "[Workspace] commit 失败：文件不存在 %s", source_abs
            )
            return

        shutil.copy2(source_abs, self.original_path)
        self._committed = True
        logger.info(
            "[Workspace] ✅ 已将工作区结果回写: %s → %s",
            source_abs, self.original_path,
        )


# ─────────────────────────────────────────────
# 抽象基类
# ─────────────────────────────────────────────

class WorkspaceProvider(ABC):
    """
    工作区供应商抽象接口。

    所有实现必须保证：
      1. create_workspace  → 返回隔离的工作目录路径
      2. prepare_file      → 将原文件深拷贝到工作区
      3. cleanup           → 核平销毁整个工作区（无论成功失败）
    """

    @abstractmethod
    def create_workspace(self, task_id: str) -> str:
        """
        创建隔离工作区，返回工作区根目录路径。

        Args:
            task_id: 全局唯一的任务标识符

        Returns:
            工作区根目录的绝对路径
        """
        ...

    @abstractmethod
    def prepare_file(self, workspace_dir: str, original_path: str) -> str:
        """
        将原文件深拷贝到工作区中。

        Args:
            workspace_dir: 工作区根目录
            original_path: 用户原文件的绝对路径

        Returns:
            工作区中文件副本的绝对路径
        """
        ...

    @abstractmethod
    def cleanup(self, task_id: str, workspace_dir: str):
        """
        核平销毁工作区（所有文件、子目录一律删除）。

        Args:
            task_id: 任务标识符
            workspace_dir: 工作区根目录
        """
        ...

    @contextmanager
    def session(self, task_id: str, original_path: str):
        """
        工作区会话的上下文管理器。

        用法：
            with provider.session("task_001", "C:\\论文.docx") as ctx:
                worker.run(f"处理文件: {ctx.work_path}")
                if success:
                    ctx.commit()  # 回写原文件
            # 自动 cleanup

        Yields:
            WorkspaceContext 实例
        """
        workspace_dir = self.create_workspace(task_id)
        work_path = self.prepare_file(workspace_dir, original_path)
        ctx = WorkspaceContext(
            task_id=task_id,
            workspace_dir=workspace_dir,
            work_path=work_path,
            original_path=os.path.abspath(original_path),
        )
        try:
            yield ctx
        finally:
            self.cleanup(task_id, workspace_dir)


# ─────────────────────────────────────────────
# MVP 实现：本地文件夹隔离
# ─────────────────────────────────────────────

class LocalFolderWorkspace(WorkspaceProvider):
    """
    MVP 工作区：在本地临时目录下创建以 TaskID 命名的隔离文件夹。

    隔离机制：
      - 每个 Worker 在 {base_dir}/ws_{task_id}/ 下工作
      - 原文档被深拷贝进工作区，Worker 只操作副本
      - 成功 → commit() 回写；失败 → cleanup() 核平，原文件毫发无伤
      - cleanup 使用 shutil.rmtree() 递归删除整个工作区

    局限性（MVP 阶段的已知限制）：
      - 无进程级沙盒：Worker 的 COM 调用仍能访问工作区外的路径（依赖 Worker Prompt 约束）
      - 无网络隔离：Worker 进程可以访问网络
      - 这些限制将在 WindowsSandboxWorkspace 中解决
    """

    def __init__(self, base_dir: str = None):
        """
        Args:
            base_dir: 工作区根目录的父目录。
                默认为系统临时目录下的 docmaster_workspaces/。
        """
        if base_dir is None:
            base_dir = os.path.join(tempfile.gettempdir(), "docmaster_workspaces")
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def create_workspace(self, task_id: str) -> str:
        workspace_dir = os.path.join(self.base_dir, f"ws_{task_id}")
        os.makedirs(workspace_dir, exist_ok=True)
        logger.debug(
            "[LocalWorkspace] 📁 已创建工作区: %s", workspace_dir
        )
        return workspace_dir

    def prepare_file(self, workspace_dir: str, original_path: str) -> str:
        abs_original = os.path.abspath(original_path)
        filename = os.path.basename(abs_original)
        work_path = os.path.join(workspace_dir, filename)
        shutil.copy2(abs_original, work_path)
        logger.debug(
            "[LocalWorkspace] 📋 文件已拷贝: %s → %s", abs_original, work_path
        )
        return work_path

    def cleanup(self, task_id: str, workspace_dir: str):
        try:
            if os.path.exists(workspace_dir):
                shutil.rmtree(workspace_dir)
                logger.debug(
                    "[LocalWorkspace] 🗑️ 工作区已核平: %s", workspace_dir
                )
        except Exception as e:
            logger.warning(
                "[LocalWorkspace] 清理工作区失败（不影响主流程）: %s", e
            )


# ─────────────────────────────────────────────
# 未来实现：Windows Sandbox 容器隔离
# ─────────────────────────────────────────────

class WindowsSandboxWorkspace(WorkspaceProvider):
    """
    进程级沙盒隔离（未实现，架构占位）。

    实现思路：
      1. create_workspace:
         - 调用 Windows Sandbox API 或启动 .wsb 配置文件
         - .wsb 配置中将宿主的工作区文件夹映射为 Sandbox 内的只读/读写共享目录
         - Sandbox 内预装 Python + pywin32 + Word（通过 LogonCommand 自动启动 Agent Worker）

      2. prepare_file:
         - 将原文件拷贝到宿主侧的共享目录
         - Sandbox 内的 Worker 通过映射路径访问

      3. execute（扩展方法）:
         - 通过 WinRM / Named Pipe / Shared File 与 Sandbox 内的 Agent Worker 通信
         - Worker 在 Sandbox 内执行 COM 操作，Sandbox 内的 Word 进程完全隔离
         - 即使 Word 弹窗死锁，也只影响 Sandbox 内部
         - 宿主通过超时检测 → 直接终止整个 Sandbox（比杀进程更彻底）

      4. cleanup:
         - 终止 Sandbox 进程 → 整个虚拟环境被核平
         - 所有注册表、临时文件、COM 残留全部消失
         - 宿主侧的共享目录用 shutil.rmtree() 清理

    依赖条件：
      - Windows 10 Pro / Enterprise（Home 版不支持 Windows Sandbox）
      - 需开启 Hyper-V 和 "Windows Sandbox" 可选功能
      - 首次启动 Sandbox 约需 5-10 秒，后续复用约 2-3 秒

    性能考量：
      - Sandbox 启动开销 vs Worker 任务时长的 ROI
      - 对于 <5 秒的快速任务，LocalFolderWorkspace 更合适
      - 对于 >30 秒的复杂排版任务，Sandbox 隔离的安全收益远超启动开销
    """

    def create_workspace(self, task_id: str) -> str:
        raise NotImplementedError(
            "WindowsSandboxWorkspace 尚未实现。"
            "当前请使用 LocalFolderWorkspace 作为 MVP 方案。"
        )

    def prepare_file(self, workspace_dir: str, original_path: str) -> str:
        raise NotImplementedError

    def cleanup(self, task_id: str, workspace_dir: str):
        raise NotImplementedError


# ─────────────────────────────────────────────
# 工厂函数
# ─────────────────────────────────────────────

def get_workspace_provider(provider_type: str = "local", **kwargs) -> WorkspaceProvider:
    """
    工作区供应商工厂。

    Args:
        provider_type: "local" | "sandbox" | "docker"
        **kwargs: 传递给具体实现的参数

    Returns:
        WorkspaceProvider 实例
    """
    providers = {
        "local": LocalFolderWorkspace,
        "sandbox": WindowsSandboxWorkspace,
    }
    cls = providers.get(provider_type)
    if cls is None:
        raise ValueError(
            f"未知的工作区类型: {provider_type}，"
            f"可选: {list(providers.keys())}"
        )
    return cls(**kwargs)
