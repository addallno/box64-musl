#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
threads32-deadowner：32 位 mutex/cond 桥复位已死 owner 锁字，防 KIND_SIGN 透传 musl 死锁。

rootcause（32位 steamcmd Loading Steam API 永挂取证）：
  - fake_phtread_mutex_t real_mutex==KIND_SIGN 时 getAlignedMutex 直接 return m，
    宿主 musl 把 glibc 布局 guest 内存当自己的 pthread_mutex_t（aarch64 musl 锁字在 +4）。
  - 卡死实测 0x40b8cd64 = 0x80000010（WAITERS|owner=0x10），/proc/self/task/16 不存在
    → 死 owner；host 栈 my32_pthread_mutex_lock → musl pthread_mutex_timedlock → futex 永挂。
  - 64 位已有 box64_fix_dead_mutex（wrappedlibpthread.c），threads.c cond 桥已调；
    32 位 threads32.c 的 mutex_lock/timedlock/trylock 与四个 cond 桥均无此修复。

改动（src/libtools/threads32.c）：
  1) getAlignedMutex 前向声明后加 extern box64_fix_dead_mutex。
  2) my32_pthread_mutex_lock/timedlock/trylock：getAlignedMutex 结果先 fix 再转宿主。
  3) 四个 my32_pthread_cond_*wait*：getAlignedMutex 后 fix（宿主 relock 不经 wrap，
     与 64 位 cond 三桥同策略）。

依赖：box64_fix_dead_mutex 定义于 wrappedlibpthread.c STATICBUILD 块
（patch_mutex_deadowner_wrap.py 注入）；本补丁须排在其后。

用法: patch_threads32_deadowner.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: threads32-deadowner"


def fail(msg):
    print(f"patch_threads32_deadowner: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "libtools", "threads32.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_threads32_deadowner: 已应用过，跳过")
        return 0

    # 段1：extern 声明（紧跟 getAlignedMutex 前向声明）
    src = apply(
        src,
        "pthread_mutex_t* getAlignedMutex(pthread_mutex_t* m);\n",
        "pthread_mutex_t* getAlignedMutex(pthread_mutex_t* m);\n"
        "// " + SENTINEL + " — 复位已死 owner 锁字（定义见 wrappedlibpthread.c STATICBUILD）\n"
        "extern int box64_fix_dead_mutex(void*);\n",
        1,
        "getAlignedMutex 前向声明",
        "threads32.c",
    )

    # 段2：四个 cond 桥 — getAlignedMutex 后 fix
    old_cond = "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
    new_cond = (
        "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
        "    box64_fix_dead_mutex(m);  // " + SENTINEL + "\n"
    )
    src = apply(src, old_cond, new_cond, 4, "cond 桥 getAlignedMutex×4", "threads32.c")

    # 段3：mutex_lock
    src = apply(
        src,
        "EXPORT int my32_pthread_mutex_lock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_lock(getAlignedMutex(m));\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_lock(pthread_mutex_t *m)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        "    box64_fix_dead_mutex(am);  // " + SENTINEL + "\n"
        "    return pthread_mutex_lock(am);\n"
        "}\n",
        1,
        "my32_pthread_mutex_lock",
        "threads32.c",
    )

    # 段4：mutex_timedlock
    src = apply(
        src,
        "EXPORT int my32_pthread_mutex_timedlock(pthread_mutex_t *m, const struct timespec * t)\n"
        "{\n"
        "    return pthread_mutex_timedlock(getAlignedMutex(m), t);\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_timedlock(pthread_mutex_t *m, const struct timespec * t)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        "    box64_fix_dead_mutex(am);  // " + SENTINEL + "\n"
        "    return pthread_mutex_timedlock(am, t);\n"
        "}\n",
        1,
        "my32_pthread_mutex_timedlock",
        "threads32.c",
    )

    # 段5：mutex_trylock
    src = apply(
        src,
        "EXPORT int my32_pthread_mutex_trylock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_trylock(getAlignedMutex(m));\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_trylock(pthread_mutex_t *m)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        "    box64_fix_dead_mutex(am);  // " + SENTINEL + "\n"
        "    return pthread_mutex_trylock(am);\n"
        "}\n",
        1,
        "my32_pthread_mutex_trylock",
        "threads32.c",
    )

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print("patch_threads32_deadowner: 已应用 -> threads32.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_threads32_deadowner.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
