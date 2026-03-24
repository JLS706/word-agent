# -*- coding: utf-8 -*-
"""
DocMaster Agent - 统一沙盒引擎

所有代码执行的安全防护都在这里，提供两种模式：
  1. 严格模式（execute_sandboxed）— 只读分析，用于 execute_python 工具
  2. 工具模式（test_tool_sandboxed）— 允许系统操作，用于 create_tool 测试

三层防护架构：
  Layer 1: AST 静态分析 — 在执行前扫描代码，拦截危险 import 和操作
  Layer 2: builtins 白名单 — 运行时移除 exec/eval/__import__ 等危险内置函数
  Layer 3: 进程隔离 + 超时强杀 — multiprocessing 子进程，两级终止
"""

import ast
import io
import os
import sys
import json
import multiprocessing
import traceback
from typing import Optional


# ═════════════════════════════════════════════
# 模块白名单 / 黑名单
# ═════════════════════════════════════════════

# 严格模式白名单（只读分析，不允许系统操作）
STRICT_ALLOWED_MODULES = {
    "re", "string", "textwrap", "difflib",
    "math", "statistics", "decimal", "fractions", "random",
    "collections", "itertools", "functools", "operator",
    "datetime", "time",
    "json", "csv",
    "typing", "enum",
    "copy", "pprint", "hashlib", "unicodedata",
}

# 工具模式白名单（额外允许系统操作）
TOOL_ALLOWED_MODULES = STRICT_ALLOWED_MODULES | {
    "os", "subprocess", "pathlib", "shutil",
    "glob", "fnmatch",
}

# 绝对禁止的模块（两种模式都禁止）
BLOCKED_MODULES = {
    "socket", "http", "urllib", "requests", "ftplib",  # 网络
    "sqlite3", "pickle", "shelve", "marshal",           # 反序列化
    "ctypes", "importlib", "runpy", "code", "codeop",   # 元编程
    "signal", "multiprocessing", "threading",            # 进程/线程
    "win32com", "win32api", "win32con",                  # Windows COM
    "builtins", "__builtin__",                           # 内置覆盖
}

# 严格模式额外禁止（系统操作模块）
STRICT_BLOCKED_MODULES = BLOCKED_MODULES | {
    "os", "sys", "subprocess", "shutil", "pathlib",
}

# 危险函数调用
BLOCKED_CALLS = {"exec", "eval", "__import__", "compile"}

# 执行超时
STRICT_TIMEOUT = 5
TOOL_TIMEOUT = 10

# 输出最大长度
MAX_OUTPUT_LENGTH = 2000


# ═════════════════════════════════════════════
# Layer 1: AST 安全检查
# ═════════════════════════════════════════════

class SafetyChecker(ast.NodeVisitor):
    """遍历 AST 树，检测并拦截危险操作。"""

    def __init__(self, allowed_modules: set, blocked_modules: set):
        self.allowed = allowed_modules
        self.blocked = blocked_modules
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            mod_root = alias.name.split(".")[0]
            if mod_root in self.blocked:
                self.errors.append(f"🚫 禁止导入模块 '{alias.name}'")
            elif mod_root not in self.allowed and mod_root != "tools":
                self.errors.append(
                    f"🚫 模块 '{alias.name}' 不在白名单中"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            mod_root = node.module.split(".")[0]
            if mod_root in self.blocked:
                self.errors.append(f"🚫 禁止从 '{node.module}' 导入")
            elif mod_root not in self.allowed and mod_root != "tools":
                self.errors.append(
                    f"🚫 模块 '{node.module}' 不在白名单中"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                self.errors.append(f"🚫 禁止调用 '{node.func.id}()'")
        if isinstance(node.func, ast.Attribute):
            dangerous_attrs = {
                "system", "popen", "remove", "unlink", "rmdir",
                "rename", "makedirs", "mkdir",
            }
            if node.func.attr in dangerous_attrs:
                self.errors.append(f"🚫 禁止调用 '.{node.func.attr}()'")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            safe_dunders = {
                "__init__", "__str__", "__repr__", "__len__",
                "__getitem__", "__setitem__", "__contains__",
                "__iter__", "__next__", "__enter__", "__exit__",
                "__add__", "__sub__", "__mul__", "__truediv__",
                "__eq__", "__lt__", "__gt__", "__le__", "__ge__",
                "__hash__", "__bool__", "__name__",
            }
            if node.attr not in safe_dunders:
                self.errors.append(f"🚫 禁止访问 '{node.attr}'")
        self.generic_visit(node)


def check_code_safety(
    code: str,
    mode: str = "strict",
) -> Optional[str]:
    """
    对代码进行 AST 安全检查。

    Args:
        code: 要检查的代码
        mode: "strict"（只读分析）或 "tool"（允许系统操作）

    Returns:
        None = 安全, 否则返回错误信息
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"❌ 代码语法错误: {e}"

    if mode == "strict":
        checker = SafetyChecker(STRICT_ALLOWED_MODULES, STRICT_BLOCKED_MODULES)
    else:
        checker = SafetyChecker(TOOL_ALLOWED_MODULES, BLOCKED_MODULES)

    checker.visit(tree)

    if checker.errors:
        return "代码安全检查未通过:\n" + "\n".join(checker.errors)

    return None


# ═════════════════════════════════════════════
# Layer 2: 受限 builtins
# ═════════════════════════════════════════════

def _make_safe_builtins(mode: str = "strict") -> dict:
    """
    构建安全的 builtins 字典。

    Args:
        mode: "strict"（只读，open 限制为 r 模式）
              "tool"（允许文件写入和更多操作）
    """
    import builtins as _builtins

    allowed_builtins = {
        # 类型转换
        "int", "float", "str", "bool", "bytes", "bytearray",
        "list", "tuple", "dict", "set", "frozenset",
        "complex", "memoryview",
        # 常用函数
        "abs", "all", "any", "bin", "chr", "divmod",
        "enumerate", "filter", "format", "hash", "hex",
        "isinstance", "issubclass", "iter", "len", "map",
        "max", "min", "next", "oct", "ord", "pow",
        "print", "range", "repr", "reversed", "round",
        "slice", "sorted", "sum", "type", "zip",
        # 其他
        "ascii", "callable", "id", "dir", "vars",
        "hasattr", "getattr",
        "open",
        # 异常类
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "AttributeError", "RuntimeError",
        "StopIteration", "ZeroDivisionError", "FileNotFoundError",
        "PermissionError", "OSError", "IOError",
        "True", "False", "None",
    }

    # 工具模式额外允许
    if mode == "tool":
        allowed_builtins.add("setattr")

    safe = {}
    for name in allowed_builtins:
        obj = getattr(_builtins, name, None)
        if obj is not None:
            safe[name] = obj

    # 严格模式：替换 open 为只读版本
    if mode == "strict":
        original_open = _builtins.open

        def safe_open(file, mode_str="r", *args, **kwargs):
            if any(c in mode_str for c in ("w", "a", "x", "+")):
                raise PermissionError(
                    f"🚫 安全限制: 只允许读取文件 (mode='r')，"
                    f"不允许写入 (mode='{mode_str}')"
                )
            return original_open(file, mode_str, *args, **kwargs)

        safe["open"] = safe_open

    # 受限的 __import__
    allowed_modules = (
        STRICT_ALLOWED_MODULES if mode == "strict" else TOOL_ALLOWED_MODULES
    )
    blocked_modules = (
        STRICT_BLOCKED_MODULES if mode == "strict" else BLOCKED_MODULES
    )
    original_import = _builtins.__import__

    def safe_import(name, *args, **kwargs):
        module_root = name.split(".")[0]
        if module_root in blocked_modules:
            raise ImportError(f"模块 '{name}' 被禁止导入（安全限制）")
        if module_root not in allowed_modules and module_root != "tools":
            raise ImportError(f"模块 '{name}' 不在白名单中")
        return original_import(name, *args, **kwargs)

    safe["__import__"] = safe_import
    safe["__build_class__"] = _builtins.__build_class__

    return safe


# ═════════════════════════════════════════════
# Layer 3: 进程隔离 + 超时强杀
# ═════════════════════════════════════════════

# ── 严格模式 Worker（只读分析）──

def _strict_worker(code: str, result_queue: multiprocessing.Queue):
    """严格模式子进程：执行只读分析代码"""
    try:
        safe_builtins = _make_safe_builtins("strict")

        restricted_globals = {
            "__builtins__": safe_builtins,
            "__name__": "__sandbox__",
        }

        # 预导入严格白名单模块
        for mod_name in ("re", "math", "json", "collections", "datetime"):
            try:
                restricted_globals[mod_name] = __import__(mod_name)
            except ImportError:
                pass

        result = {"stdout": "", "result": "", "error": ""}
        captured_stdout = io.StringIO()
        old_stdout = sys.stdout

        try:
            sys.stdout = captured_stdout
            exec(compile(code, "<agent_sandbox>", "exec"), restricted_globals)
            result["stdout"] = captured_stdout.getvalue()

            # 尝试获取最后一个表达式的值（类似 REPL）
            try:
                tree = ast.parse(code)
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last_expr = ast.Expression(body=tree.body[-1].value)
                    ast.fix_missing_locations(last_expr)
                    val = eval(
                        compile(last_expr, "<agent_sandbox>", "eval"),
                        restricted_globals,
                    )
                    if val is not None:
                        result["result"] = repr(val)
            except Exception:
                pass

        except PermissionError as e:
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        finally:
            sys.stdout = old_stdout

        result_queue.put(result)
    except Exception as e:
        result_queue.put({"stdout": "", "result": "", "error": str(e)})


# ── 工具模式 Worker（测试自定义工具）──

def _tool_worker(code: str, result_queue: multiprocessing.Queue):
    """工具模式子进程：试执行自定义工具代码"""
    try:
        safe_builtins = _make_safe_builtins("tool")

        restricted_globals = {
            "__builtins__": safe_builtins,
            "__name__": "__sandbox__",
        }

        # 预导入工具白名单模块
        for mod_name in ("re", "os", "json", "subprocess", "datetime", "pathlib"):
            try:
                restricted_globals[mod_name] = __import__(mod_name)
            except ImportError:
                pass

        captured_stdout = io.StringIO()
        old_stdout = sys.stdout

        try:
            sys.stdout = captured_stdout

            # 执行代码（定义类和函数）
            exec(compile(code, "<tool_sandbox>", "exec"), restricted_globals)

            # 找到 CustomTool 类并试执行
            custom_tool_cls = restricted_globals.get("CustomTool")
            if custom_tool_cls is None:
                result_queue.put({
                    "success": False,
                    "stdout": captured_stdout.getvalue(),
                    "test_output": "",
                    "error": "代码中未找到 CustomTool 类定义",
                })
                return

            tool_instance = custom_tool_cls()

            try:
                test_result = tool_instance.execute()
                result_queue.put({
                    "success": True,
                    "stdout": captured_stdout.getvalue(),
                    "test_output": str(test_result)[:500],
                    "error": "",
                })
            except TypeError as e:
                # 缺少必要参数 → 结构正常
                if "required" in str(e) or "argument" in str(e):
                    result_queue.put({
                        "success": True,
                        "stdout": captured_stdout.getvalue(),
                        "test_output": f"(空跑触发参数缺失，结构正常: {e})",
                        "error": "",
                    })
                else:
                    result_queue.put({
                        "success": False,
                        "stdout": captured_stdout.getvalue(),
                        "test_output": "",
                        "error": f"TypeError: {e}",
                    })
            except Exception as e:
                result_queue.put({
                    "success": False,
                    "stdout": captured_stdout.getvalue(),
                    "test_output": "",
                    "error": f"{type(e).__name__}: {e}",
                })

        finally:
            sys.stdout = old_stdout

    except Exception as e:
        result_queue.put({
            "success": False,
            "stdout": "",
            "test_output": "",
            "error": f"沙盒执行异常: {type(e).__name__}: {e}",
        })


# ── 两级超时强杀 ──

def _run_in_process(worker, code: str, timeout: int) -> dict:
    """
    在独立子进程中执行 worker 函数，带两级超时强杀。

    这是所有沙盒执行的统一入口：
      1. 启动子进程
      2. 等待 timeout 秒
      3. 若超时：terminate → join(2s) → kill → join(1s)
    """
    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=worker,
        args=(code, result_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout)

    # 超时处理
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1)
        return {
            "error": f"⏰ 代码执行超时（超过 {timeout} 秒），已强制终止子进程！"
                     f"可能原因: 死循环或计算量过大。",
        }

    # 子进程异常退出
    if proc.exitcode is not None and proc.exitcode != 0 and result_queue.empty():
        return {"error": f"❌ 沙盒子进程异常退出 (exit code: {proc.exitcode})"}

    # 获取结果
    if result_queue.empty():
        return {"error": "❌ 执行异常: 子进程未返回结果"}

    try:
        return result_queue.get_nowait()
    except Exception:
        return {"error": "❌ 执行异常: 无法读取子进程结果"}


# ═════════════════════════════════════════════
# 公开 API
# ═════════════════════════════════════════════

def execute_sandboxed(code: str, timeout: int = STRICT_TIMEOUT) -> str:
    """
    严格模式沙盒执行（只读分析）。

    用于 execute_python 工具：
      - 优先 Docker 沙盒微服务（SANDBOX_URL 环境变量）
      - 回退到本地 multiprocessing 沙盒
      - 白名单：re, math, json, collections 等
      - 禁止：os, subprocess, 文件写入, 网络
      - 超时 5 秒

    Returns:
        执行结果的文本描述
    """
    # ── 尝试 Docker 沙盒微服务 ──
    sandbox_url = os.environ.get("SANDBOX_URL")
    if sandbox_url:
        try:
            import urllib.request
            import urllib.error

            url = f"{sandbox_url.rstrip('/')}/execute"
            payload = json.dumps({"code": code, "timeout": timeout}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("success"):
                    return result.get("output", "✅ 代码执行完成（无输出）")
                else:
                    return result.get("error", "❌ 未知错误")
        except (urllib.error.URLError, Exception):
            pass  # Docker 沙盒不可用，回退到本地

    # ── 回退：本地沙盒 ──
    # Layer 1: AST 安全检查
    safety_error = check_code_safety(code, mode="strict")
    if safety_error:
        return safety_error

    # Layer 2 + 3: 进程隔离执行
    result = _run_in_process(_strict_worker, code, timeout)

    # 组装输出
    output_parts = []

    if result.get("error"):
        output_parts.append(f"❌ 运行报错:\n{result['error']}")

    stdout = result.get("stdout", "")
    if stdout:
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + \
                     f"\n... (输出过长，已截断。共 {len(result['stdout'])} 字符)"
        output_parts.append(f"📤 输出:\n{stdout}")

    if result.get("result"):
        output_parts.append(f"📊 返回值: {result['result']}")

    if not output_parts:
        output_parts.append("✅ 代码执行完成（无输出）")

    return "\n".join(output_parts)


def test_tool_sandboxed(code: str, timeout: int = TOOL_TIMEOUT) -> dict:
    """
    工具模式沙盒执行（测试自定义工具代码）。

    用于 create_tool 工具：
      - 优先 Docker 沙盒微服务（SANDBOX_URL 环境变量）
      - 回退到本地 multiprocessing 沙盒
      - 白名单：含 os, subprocess, pathlib, shutil
      - 超时 10 秒

    Returns:
        {"success": bool, "stdout": str, "test_output": str, "error": str}
    """
    # ── 尝试 Docker 沙盒微服务 ──
    sandbox_url = os.environ.get("SANDBOX_URL")
    if sandbox_url:
        try:
            import urllib.request
            import urllib.error

            url = f"{sandbox_url.rstrip('/')}/test-tool"
            payload = json.dumps({"code": code, "timeout": timeout}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return {
                    "success": result.get("success", False),
                    "stdout": result.get("output", ""),
                    "test_output": result.get("test_output", ""),
                    "error": result.get("error", ""),
                }
        except (urllib.error.URLError, Exception):
            pass  # Docker 沙盒不可用，回退到本地

    # ── Layer 1: AST 安全检查 ──
    safety_error = check_code_safety(code, mode="tool")
    if safety_error:
        return {"success": False, "stdout": "", "test_output": "", "error": safety_error}

    # ── Layer 2 + 3: 本地进程隔离执行 ──
    result = _run_in_process(_tool_worker, code, timeout)

    # 统一返回格式
    if "success" not in result:
        result["success"] = not bool(result.get("error"))
    if "stdout" not in result:
        result["stdout"] = ""
    if "test_output" not in result:
        result["test_output"] = ""

    return result
