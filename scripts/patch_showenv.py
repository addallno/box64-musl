#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
showenv：box64 增加 `-e, --show-env` 选项（哨兵 BOX64-BUILD: showenv）。

打印 LoadEnvVariables() 解析后的 box64env 结构（含全部 BOX64_* 开关的
生效值），用于部署后快速核对 pathmap 等环境变量是否被 box64 正确读取。
printf_log_prefix 条件 (L)<=BOX64ENV(log)，log 默认 LOG_NONE=0，
故必须传 LOG_NONE(0) 才无条件打印（LOG_INFO 需 BOX64_LOG>=1）。

改动（仅 src/core.c）：
1. PrintHelp()：-h 行后插 -e 行
2. 选项解析循环：-h 分支后插 -e/--show-env 分支（LoadEnvVariables() 已先行）

用法: patch_showenv.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: showenv"


def fail(msg):
    print(f"patch_showenv: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p_core = os.path.join(srcdir, "src", "core.c")
    if not os.path.isfile(p_core):
        fail(f"文件不存在: {p_core}")

    with open(p_core, "r", encoding="utf-8") as f:
        core = f.read()

    if SENTINEL in core:
        print("patch_showenv: 已应用过，跳过")
        return 0

    # 1) PrintHelp：-h 行后插 -e 行（源码中 \t \n 为字面反斜杠）
    old_help = '    PrintfFtrace(0, "\\t-h, --help             print this and quit\\n");\n'
    new_help = (
        old_help
        + '    PrintfFtrace(0, "\\t-e, --show-env         print box64 environment variables and quit\\n");'
        + "  // " + SENTINEL + "\n"
    )
    core = apply(core, old_help, new_help, 1, "PrintHelp -h 行", "core.c")

    # 2) 选项解析：-h 分支后插 -e 分支（LoadEnvVariables() 在循环前已执行）
    old_opt = (
        '        if(!strcmp(prog, "-h") || !strcmp(prog, "--help")) {\n'
        "            PrintHelp();\n"
        "            exit(0);\n"
        "        }\n"
    )
    new_opt = (
        old_opt
        + '        if(!strcmp(prog, "-e") || !strcmp(prog, "--show-env")) {\n'
        + "            PrintEnvVariables(&box64env, LOG_NONE);  // " + SENTINEL + " LOG_NONE=0\n"
        + "            exit(0);\n"
        + "        }\n"
    )
    core = apply(core, old_opt, new_opt, 1, "-h 选项分支", "core.c")

    with open(p_core, "w", encoding="utf-8") as f:
        f.write(core)
    print("patch_showenv: 已应用 -> core.c(2处)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <box64源码目录>")
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
