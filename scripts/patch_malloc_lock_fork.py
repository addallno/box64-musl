#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
malloc-lock-fork：atfork child handler 强制清零 musl __malloc_lock。

rootcause：steamcmd 多线程下 fork()（guest +login 后的子进程）瞬间，
父进程某 host 线程可能持有静态 musl 的全局锁 __malloc_lock（锁字
0x80000001 = sign-bit locked + congestion 1）。musl fork.c 的 child 清理
只经 __malloc_atfork 重置 oldmalloc 的 bins/split_merge 锁，完全不碰
__malloc_lock，且 fork 后 libc.need_locks 残留 >0 —— 于是 child 首次
malloc 进 __lock(&__malloc_lock)：CAS 失败、fetch_add 后 congestion=2
（0x80000002）、futex_wait 永等（持锁线程已随 fork 消失）。实测
strace 卡 FUTEX_WAIT_PRIVATE val=0x80000002 于 __lock，box64-bin .bss
符号 __malloc_lock 地址与死锁锁字 +0x10000 平移精确吻合。

改动（仅 src/box64context.c 的 atfork_child_box64context）：
  child 单线程，强制 __malloc_lock = 0（congestion 清零安全）；
  need_locks>0 时后续 __lock CAS(0→INT_MIN+1) 立即成功，不再进 futex。

用法: patch_malloc_lock_fork.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: malloc-lock-fork"


def fail(msg):
    print(f"patch_malloc_lock_fork: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "box64context.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_malloc_lock_fork: 已应用过，跳过")
        return 0

    old1 = (
        "static void atfork_child_box64context(void)\n"
        "{\n"
        "    // (re)init mutex if it was lock before the fork\n"
        "    init_mutexes(my_context);\n"
    )
    new1 = (
        "static void atfork_child_box64context(void)\n"
        "{\n"
        "    // fork 后 child 单线程，静态 musl 的 __malloc_lock 若在 fork 瞬间被\n"
        "    // 其他线程持有则继承为脏态，child 后续 malloc 会在 __lock 中 futex 永等；\n"
        "    // musl 自身的 __malloc_atfork 只重置 bins 锁不碰它，此处强制清零\n"
        "    extern volatile int __malloc_lock;\n"
        "    __malloc_lock = 0;  // " + SENTINEL + "\n"
        "    // (re)init mutex if it was lock before the fork\n"
        "    init_mutexes(my_context);\n"
    )
    src = apply(src, old1, new1, 1, "atfork_child_box64context 入口", "box64context.c")

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"patch_malloc_lock_fork: 已应用 -> box64context.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_malloc_lock_fork.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
