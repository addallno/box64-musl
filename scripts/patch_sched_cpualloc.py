#!/usr/bin/env python3
# 目的：实现 box64 的 __sched_cpualloc/__sched_cpufree（glibc 专有，musl 缺失）。
# 根因：weak stub glibc_missing_symbols.c 返回 0，Steam steamclient.so
# 启动早期调 __sched_cpualloc 后未判 NULL 直接写位图 →
# SIGSEGV @ steamclient+0x2acb0d3（mov [rax+rsi-8],0，rax=0），
# 100% 启动期崩溃（轮76-87）。box32 已有 my32___sched_cpualloc
# （box32_malloc(CPU_ALLOC_SIZE)），box64 路径补齐。
# CPU_ALLOC_SIZE(count) = ceil(count/64)*8 字节。
# 改动：wrappedlibc_private.h GO→GOM×2；wrappedlibc.c 加实现。
# 幂等：SENTINEL 存在即跳过。
# 用法：python3 patch_sched_cpualloc.py <box64源码根目录>

import sys
import os

SENTINEL = "BOX64-BUILD: sched-cpualloc"
PRIV_REL = "src/wrapped/wrappedlibc_private.h"
LIBC_REL = "src/wrapped/wrappedlibc.c"

PRIV_ANCHOR_ALLOC = "GO(__sched_cpualloc, pFL)\n"
PRIV_ANCHOR_FREE = "GO(__sched_cpufree, vFp)\n"
LIBC_ANCHOR = "static int box64_gai_native_broken = 0;\n"

IMPL = (
    "// BOX64-BUILD: sched-cpualloc：musl 缺 __sched_cpualloc（weak stub 返 0）\n"
    "// 导致 Steam 位图分配后解引用 NULL；按 glibc CPU_ALLOC_SIZE 语义补实现\n"
    "EXPORT void* my___sched_cpualloc(x64emu_t* emu, unsigned long count)\n"
    "{\n"
    "    (void)emu;\n"
    "    if (!count)\n"
    "        return NULL;\n"
    "    size_t sz = ((count + 63) >> 6) << 3;\n"
    "    return calloc(1, sz);\n"
    "}\n"
    "\n"
    "EXPORT void my___sched_cpufree(x64emu_t* emu, void* p)\n"
    "{\n"
    "    (void)emu;\n"
    "    free(p);\n"
    "}\n"
    "\n"
)


def main():
    if len(sys.argv) < 2:
        print("用法: patch_sched_cpualloc.py <box64源码根目录>", file=sys.stderr)
        sys.exit(1)
    root = os.path.abspath(sys.argv[1])

    priv = os.path.join(root, PRIV_REL)
    with open(priv, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL not in src:
        n1 = src.count(PRIV_ANCHOR_ALLOC)
        n2 = src.count(PRIV_ANCHOR_FREE)
        if n1 != 1 or n2 != 1:
            print(f"patch_sched_cpualloc: {PRIV_REL} 锚点命中 alloc={n1} free={n2}（期望各1）",
                  file=sys.stderr)
            sys.exit(2)
        src = src.replace(PRIV_ANCHOR_ALLOC, "GOM(__sched_cpualloc, pFEL)  // " + SENTINEL + "\n", 1)
        src = src.replace(PRIV_ANCHOR_FREE, "GOM(__sched_cpufree, vFEp)\n", 1)
        with open(priv, "w", encoding="utf-8") as f:
            f.write(src)
        print(f"patch_sched_cpualloc: 已应用 GO→GOM -> {priv}")

    libc = os.path.join(root, LIBC_REL)
    with open(libc, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print(f"patch_sched_cpualloc: libc 实现已存在，跳过 ({libc})")
        return
    n = src.count(LIBC_ANCHOR)
    if n != 1:
        print(f"patch_sched_cpualloc: {LIBC_REL} 锚点命中 {n} 次（期望1）", file=sys.stderr)
        sys.exit(2)
    src = src.replace(LIBC_ANCHOR, IMPL + LIBC_ANCHOR, 1)
    with open(libc, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"patch_sched_cpualloc: 已应用实现 -> {libc}")


if __name__ == "__main__":
    main()
