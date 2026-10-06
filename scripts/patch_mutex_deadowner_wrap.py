#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mutex-deadowner：wrapped pthread_mutex_lock 层检测已死 robust owner，复位锁字防 fork 死锁。

rootcause（futex-eownerdied 轮次取证破案）：child 死锁的 FUTEX_WAIT 由 box64-bin 内
宿主 musl（wrapped pthread 路径，静态链入）直接 svc 发出，**不经过 x64Syscall_linux**
（guest syscall 层 trace=0 实锤）。fork 时父线程持有的锁在 child 里锁字保留
glibc WAITERS|owner-tid 格式，owner 线程在 child 不存在且 set_robust_list 已被 box64
伪造 → 内核永不置 OWNER_DIED → 宿主 musl 按该值 futex 等待永不变 → 永挂
（实测 child 卡 FUTEX_WAIT val=0x80002F94 owner=父进程活线程 12180）。

改动：
  1) src/wrapped/wrappedlibpthread_private.h：
     GO(pthread_mutex_lock/__pthread_mutex_lock) → GOM(...)，静态映射到 my_ 实现
     （非 STATICBUILD 下 GO/GOM 展开相同，行为不变）。
  2) src/wrapped/wrappedlibpthread.c（STATICBUILD extern 块内、wrappedlib_init.h 之前，
     保证 &my_pthread_mutex_lock 在表展开处可见）：
     my_pthread_mutex_lock：转发宿主前扫描 mutex 对象 offset 0..16，
     发现 WAITERS|owner 且 owner 不在 /proc/self/task（已死）→ CAS 复位锁字为 free，
     FUTEX_WAKE 叫醒可能的等待者，按成功返回 0；正常锁（owner 活/无 WAITERS）透传
     __pthread_mutex_lock。扫描而非固定 offset，规避 x86_64(40B)/ARM64 布局差异。
     此后该锁回到干净 free 态，后续 lock 走宿主 a_swap 快路径，状态机自愈。

用法: patch_mutex_deadowner_wrap.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: mutex-deadowner"


def fail(msg):
    print(f"patch_mutex_deadowner_wrap: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p1 = os.path.join(srcdir, "src", "wrapped", "wrappedlibpthread.c")
    p2 = os.path.join(srcdir, "src", "wrapped", "wrappedlibpthread_private.h")
    for p in (p1, p2):
        if not os.path.isfile(p):
            fail(f"文件不存在: {p}")

    with open(p1, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_mutex_deadowner_wrap: 已应用过，跳过")
        return 0

    block = (
        "extern int __pthread_setspecific(size_t, void*);\n"
        "#include <stdint.h>\n"
        "#include <unistd.h>\n"
        "#include <sys/syscall.h>\n"
        "#include <linux/futex.h>\n"
        "int GetTID();\n"
        "EXPORT int my_pthread_mutex_lock(void* m)\n"
        "{\n"
        "    unsigned char* base = (unsigned char*)m;\n"
        "    int off;\n"
        "    for(off = 0; off <= 16; off += 4) {\n"
        "        uint32_t* w = (uint32_t*)(base + off);\n"
        "        uint32_t v = __atomic_load_n(w, __ATOMIC_RELAXED);\n"
        "        if(!(v & 0x80000000))  // 无 WAITERS 位：非锁字格式或无等待者\n"
        "            continue;\n"
        "        uint32_t fowner = v & 0x3FFFFFFF;\n"
        "        if(fowner <= 0x1000 || fowner == (uint32_t)GetTID())\n"
        "            continue;\n"
        "        char ftask[64];\n"
        "        snprintf(ftask, sizeof(ftask), \"/proc/self/task/%u\", fowner);\n"
        "        if(!access(ftask, F_OK))\n"
        "            continue;  // owner 是活线程：正常锁，透传宿主\n"
        "        uint32_t expect = v;\n"
        "        if(__atomic_compare_exchange_n(w, &expect, 0, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {\n"
        '            printf_log(LOG_NONE, "mutex-deadowner: off=%d owner tid %u 已死, 锁字 0x%x 复位 // ' + SENTINEL + '\\n",\n'
        "                       off, fowner, v);\n"
        "            syscall(SYS_futex, (void*)w, FUTEX_WAKE_PRIVATE, 2147483647, NULL, NULL, 0);\n"
        "            return 0;\n"
        "        }\n"
        "    }\n"
        "    return __pthread_mutex_lock(m);\n"
        "}\n"
        "EXPORT int my___pthread_mutex_lock(void* m) __attribute__((alias(\"my_pthread_mutex_lock\")));\n"
        "#endif\n\n"
        '#include "wrappedlib_init.h"'
    )
    old1 = (
        "extern int __pthread_setspecific(size_t, void*);\n"
        "#endif\n\n"
        '#include "wrappedlib_init.h"'
    )
    src = apply(src, old1, block, 1, "wrappedlibpthread.c STATICBUILD 块", "wrappedlibpthread.c")

    with open(p1, "w", encoding="utf-8") as f:
        f.write(src)

    with open(p2, "r", encoding="utf-8") as f:
        h = f.read()
    if SENTINEL in h:
        print("patch_mutex_deadowner_wrap: private.h 已应用过，跳过")
        return 0
    old2 = (
        "GO(__pthread_mutex_lock, iFp)\n"
        "GO(pthread_mutex_lock, iFp)"
    )
    new2 = (
        "// " + SENTINEL + "：死 owner 检测拦截（见 wrappedlibpthread.c my_pthread_mutex_lock）\n"
        "GOM(__pthread_mutex_lock, iFp)\n"
        "GOM(pthread_mutex_lock, iFp)"
    )
    h = apply(h, old2, new2, 1, "GO 锁表两行", "wrappedlibpthread_private.h")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(h)

    print("patch_mutex_deadowner_wrap: 已应用 -> wrappedlibpthread.c + wrappedlibpthread_private.h")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_mutex_deadowner_wrap.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
