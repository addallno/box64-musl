#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dlclose-log：my_dlclose_internal 关键日志升 LOG_INFO（哨兵 BOX64-BUILD: dlclose-log）。

退出 SIGSEGV 定位需要 dlclose 的 handle 与内部状态，但 printf_dlsym(LOG_DEBUG)
仅 BOX64_LOG>=2 才打印，而 LOG=2 的逐调用日志会把 steamcmd 拖死（实测 600-900s
走不完登录）。故将 3 处 printf_dlsym(LOG_DEBUG) 升为 printf_log(LOG_INFO)
（BOX64_LOG=1 即可见），并在成功路径补打 nlib/lib/h 状态。

改动（仅 src/wrapped/wrappedlibdl.c 的 my_dlclose_internal）：
1. 入口 "Call to dlclose(%p)" 丰富 dl/lib_sz 上下文并升 LOG_INFO
2. 两处 "dlclose: %s"（Bad handle 分支）升 LOG_INFO
3. GetElf/DecRefCount 前补打 nlib/lib/h/actualy_closing

用法: patch_dlclose_log.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: dlclose-log"


def fail(msg):
    print(f"patch_dlclose_log: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "wrapped", "wrappedlibdl.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_dlclose_log: 已应用过，跳过")
        return 0

    # 1) 入口：升 LOG_INFO 并带 dl/lib_sz 上下文
    old1 = '    printf_dlsym(LOG_DEBUG, "Call to dlclose(%p)\\n", handle);\n'
    new1 = (
        '    printf_log(LOG_INFO, "Call to dlclose(%p) dl=%p lib_sz=%zu  // ' + SENTINEL + '\\n",\n'
        '               handle, (void*)my_context->dlprivate, my_context->dlprivate->lib_sz);\n'
    )
    src = apply(src, old1, new1, 1, "入口 Call to dlclose", "wrappedlibdl.c")

    # 2) 两处 Bad handle 分支升 LOG_INFO
    old2 = '        printf_dlsym(LOG_DEBUG, "dlclose: %s\\n", dl->last_msg);\n'
    new2 = '        printf_log(LOG_INFO, "dlclose: %s\\n", dl->last_msg);  // ' + SENTINEL + '\n'
    src = apply(src, old2, new2, 2, "Bad handle 分支 x2", "wrappedlibdl.c")

    # 3) 成功路径：GetElf 后补打 nlib/lib/h
    old3 = (
        "    elfheader_t* h = GetElf(dl->dllibs[nlib].lib);\n"
        "    if((h && !h->gnuunique) || !h || actualy_closing)\n"
        "        DecRefCount(&dl->dllibs[nlib].lib, emu);\n"
    )
    new3 = (
        "    elfheader_t* h = GetElf(dl->dllibs[nlib].lib);\n"
        '    printf_log(LOG_INFO, "dlclose: nlib=%zu count=%d full=%d lib=%p h=%p gnuunique=%d closing=%d  // ' + SENTINEL + '\\n",\n'
        "               nlib, dl->dllibs[nlib].count, dl->dllibs[nlib].full,\n"
        "               (void*)dl->dllibs[nlib].lib, (void*)h,\n"
        "               h ? h->gnuunique : -1, actualy_closing);\n"
        "    if((h && !h->gnuunique) || !h || actualy_closing)\n"
        "        DecRefCount(&dl->dllibs[nlib].lib, emu);\n"
    )
    src = apply(src, old3, new3, 1, "GetElf 后补打", "wrappedlibdl.c")

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"patch_dlclose_log: 已应用 -> wrappedlibdl.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_dlclose_log.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
