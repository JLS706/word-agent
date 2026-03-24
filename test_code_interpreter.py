# -*- coding: utf-8 -*-
"""验证代码解释器的三重安全机制"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.sandbox import execute_sandboxed


def run_tests():
    print("=" * 50)
    print("  Code Interpreter Security Tests")
    print("  (multiprocessing sandbox)")
    print("=" * 50)

    # 测试1：正常计算
    print("\n--- Test 1: Normal execution ---")
    result = execute_sandboxed(
        'primes = [n for n in range(2, 30) if all(n%d for d in range(2, n))]\n'
        'print(primes)'
    )
    print(result)
    assert "2, 3, 5, 7" in result, f"Should output primes, got: {result}"
    print("[PASS]")

    # 测试2：拦截 import os
    print("\n--- Test 2: Block import os ---")
    result = execute_sandboxed('import os')
    print(result)
    assert "os" in result.lower(), f"Should block os, got: {result}"
    print("[PASS]")

    # 测试3：拦截文件写入
    print("\n--- Test 3: Block file write ---")
    result = execute_sandboxed('open("test.txt", "w")')
    print(result)
    assert "w" in result.lower() or "write" in result.lower() or "open" in result.lower(), \
        f"Should block write, got: {result}"
    print("[PASS]")

    # 测试4：拦截 exec()
    print("\n--- Test 4: Block exec() ---")
    result = execute_sandboxed('exec("print(1)")')
    print(result)
    assert "exec" in result.lower(), f"Should block exec, got: {result}"
    print("[PASS]")

    # 测试5：超时保护 (multiprocessing 强杀)
    print("\n--- Test 5: Timeout + process kill ---")
    result = execute_sandboxed('while True: pass')
    print(result)
    assert "5" in result, f"Should timeout, got: {result}"
    assert "终止" in result or "超时" in result, f"Should mention termination, got: {result}"
    print("[PASS]")

    # 测试6：正则分析
    print("\n--- Test 6: Regex analysis ---")
    result = execute_sandboxed(
        'import re\n'
        'text = "Hello World 123 Test 456"\n'
        'nums = re.findall(r"\\d+", text)\n'
        'print(f"Found {len(nums)} numbers: {nums}")'
    )
    print(result)
    assert "123" in result, f"Should find numbers, got: {result}"
    print("[PASS]")

    # 测试7：拦截 subprocess
    print("\n--- Test 7: Block subprocess ---")
    result = execute_sandboxed('import subprocess')
    print(result)
    assert "subprocess" in result.lower(), f"Should block subprocess, got: {result}"
    print("[PASS]")

    # 测试8：拦截 import requests
    print("\n--- Test 8: Block requests ---")
    result = execute_sandboxed('import requests')
    print(result)
    assert "requests" in result.lower(), f"Should block requests, got: {result}"
    print("[PASS]")

    # 测试9：math 模块正常使用
    print("\n--- Test 9: math module works ---")
    result = execute_sandboxed('import math\nprint(f"pi={math.pi:.4f}")')
    print(result)
    assert "3.141" in result, f"Should compute pi, got: {result}"
    print("[PASS]")

    # 测试10：拦截 win32com
    print("\n--- Test 10: Block win32com ---")
    result = execute_sandboxed('import win32com')
    print(result)
    assert "win32com" in result.lower(), f"Should block win32com, got: {result}"
    print("[PASS]")

    print("\n" + "=" * 50)
    print("  === ALL 10 SECURITY TESTS PASSED ===")
    print("=" * 50)


# Windows multiprocessing (spawn模式) 必须有此 guard
# 否则子进程会重新执行整个脚本，导致无限递归 spawn
if __name__ == '__main__':
    run_tests()
