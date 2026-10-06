#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mutex-deadowner：检测已死 robust owner 的 glibc 锁字，复位防 fork 死锁。

rootcause（多轮死锁取证破案）：fork child 单线程卡 FUTEX_WAIT(val=0x8000|owner-tid)
的 futex 由 box64-bin 内静态链入的宿主 musl 直接 svc 发出，**不经过 x64Syscall_linux**
（guest 层 trace=0 实锤），且 **不经过 my_pthread_mutex_lock**（child md-enter=0 实锤）。
gdb 栈实锤真实路径：
  guest PC(crashhandler fork 返回点 0x3f000b3c05)
  → box64 cond 桥（my_pthread_cond_wait/timedwait，threads.c）
  → 宿主 musl pthread_cond_* relock 内部直调 __pthread_mutex_lock（同镜像不经 wrap 表）
  → 对 guest mutex 的 glibc 锁字 futex 等待 → owner 是父进程线程、child 不存在、
    set_robust_list 已被 box64 伪造 → 内核永不置 OWNER_DIED → 永挂。

改动：
  1) src/wrapped/wrappedlibpthread_private.h：
     GO(pthread_mutex_lock/__pthread_mutex_lock) → GOM(...)，静态映射到 my_ 实现
     （非 STATICBUILD 下 GO/GOM 展开相同，行为不变）。
  2) src/wrapped/wrappedlibpthread.c（STATICBUILD extern 块内、wrappedlib_init.h 之前）：
     my_pthread_mutex_lock：转发宿主前扫描 mutex offset 0..16，WAITERS|owner 且
     owner 不在 /proc/self/task（已死）→ 按 BOX64_MUTEX_CAS=1 才 CAS 复位（诊断开关）；
     另加 md-enter 入口诊断（限20条，child pid 变化重置）。
     新增 EXPORT box64_fix_dead_mutex(m)：同扫描但**无条件 CAS 复位 + WAKE**，
     供 cond 桥入口预修复（宿主 relock 不经 wrap，只能在进桥前修锁字）。
  3) src/libtools/threads.c：my_pthread_cond_timedwait / my_pthread_cond_wait /
     my_pthread_cond_clockwait 三桥入口调 box64_fix_dead_mutex(mutex)。

用法: patch_mutex_deadowner_wrap.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: mutex-deadowner"
FIXFN = "md-fix-enter pid"


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
    p3 = os.path.join(srcdir, "src", "libtools", "threads.c")
    for p in (p1, p2, p3):
        if not os.path.isfile(p):
            fail(f"文件不存在: {p}")

    with open(p1, "r", encoding="utf-8") as f:
        src = f.read()

    # 段1：my_pthread_mutex_lock wrap（GOM 配套）
    if SENTINEL in src:
        print("patch_mutex_deadowner_wrap: 段1 已应用过，跳过")
    else:
        block = (
            "extern int __pthread_setspecific(size_t, void*);\n"
            "#include <stdint.h>\n"
            "#include <stdlib.h>\n"
            "#include <unistd.h>\n"
            "#include <sys/syscall.h>\n"
            "#include <linux/futex.h>\n"
            "int GetTID();\n"
            "EXPORT int my_pthread_mutex_lock(void* m)\n"
            "{\n"
            "    // 入口诊断：确认 wrap 表生效（child pid 变化时重置计数，fork 后必打前20次）\n"
            "    {\n"
            "        static pid_t mdpid = 0;\n"
            "        static volatile int mdent = 0;\n"
            "        pid_t mdcur = getpid();\n"
            "        if(mdcur != mdpid) { mdpid = mdcur; mdent = 0; }\n"
            "        if(mdent < 20) {\n"
            "            ++mdent;\n"
            '            char mdb[112];\n'
            '            int mdn = snprintf(mdb, sizeof(mdb), "md-enter pid=%d #%d v0=0x%x // ' + SENTINEL + '\\n",\n'
            "                               (int)mdcur, mdent, *(volatile uint32_t*)m);\n"
            "            if(mdn > 0) write(2, mdb, (size_t)mdn);\n"
            "        }\n"
            "    }\n"
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
            "        {\n"
            "            static volatile int mdlog = 0;\n"
            "            char mdbuf[160];\n"
            "            int mdn;\n"
            "            if(mdlog < 30) {\n"
            "                ++mdlog;\n"
            '                mdn = snprintf(mdbuf, sizeof(mdbuf), "mutex-deadowner: off=%d owner=%u val=0x%x gettid=%d // ' + SENTINEL + '\\n",\n'
            "                               off, fowner, v, GetTID());\n"
            "                if(mdn > 0) write(2, mdbuf, (size_t)mdn);\n"
            "            }\n"
            "            const char* mdcas = getenv(\"BOX64_MUTEX_CAS\");\n"
            "            if(mdcas && mdcas[0] == '1') {\n"
            "                uint32_t expect = v;\n"
            "                if(__atomic_compare_exchange_n(w, &expect, 0, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {\n"
            "                    syscall(SYS_futex, (void*)w, FUTEX_WAKE_PRIVATE, 2147483647, NULL, NULL, 0);\n"
            "                    return 0;\n"
            "                }\n"
            "            }\n"
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

    # 段2：box64_fix_dead_mutex（cond 桥预修复用，无条件 CAS）
    if FIXFN in src:
        print("patch_mutex_deadowner_wrap: 段2 已应用过，跳过")
    else:
        alias = 'EXPORT int my___pthread_mutex_lock(void* m) __attribute__((alias("my_pthread_mutex_lock")));\n'
        fixfn = (
            "\n"
            "// " + SENTINEL + " — cond 桥预修复：宿主 pthread_cond_* relock 直调 musl 内部\n"
            "// __pthread_mutex_lock 不经 wrap 表，只能在进桥前复位死 owner 锁字\n"
            "EXPORT int box64_fix_dead_mutex(void* m)\n"
            "{\n"
            "    // md-fix-enter 入口诊断：cond 桥被调则此处必打（fork child pid 变化重置计数）\n"
            "    static int mdfixpid = 0;\n"
            "    static int mdfixn = 0;\n"
            "    if(getpid() != mdfixpid) { mdfixpid = (int)getpid(); mdfixn = 0; }\n"
            "    if(mdfixn < 20) {\n"
            "        ++mdfixn;\n"
            "        char mdibuf[160];\n"
            '        int mdin = snprintf(mdibuf, sizeof(mdibuf), "md-fix-enter pid=%d #%d m=%p v0=0x%x // ' + SENTINEL + '\\n",\n'
            "                             (int)getpid(), mdfixn, m, __atomic_load_n((uint32_t*)m, __ATOMIC_RELAXED));\n"
            "        if(mdin > 0) write(2, mdibuf, (size_t)mdin);\n"
            "    }\n"
            "    unsigned char* base = (unsigned char*)m;\n"
            "    int off;\n"
            "    for(off = 0; off <= 16; off += 4) {\n"
            "        uint32_t* w = (uint32_t*)(base + off);\n"
            "        uint32_t v = __atomic_load_n(w, __ATOMIC_RELAXED);\n"
            "        if(!(v & 0x80000000))\n"
            "            continue;\n"
            "        uint32_t fowner = v & 0x3FFFFFFF;\n"
            "        if(fowner <= 0x1000 || fowner == (uint32_t)GetTID())\n"
            "            continue;\n"
            "        char ftask[64];\n"
            "        snprintf(ftask, sizeof(ftask), \"/proc/self/task/%u\", fowner);\n"
            "        if(!access(ftask, F_OK))\n"
            "            continue;\n"
            "        uint32_t expect = v;\n"
            "        if(__atomic_compare_exchange_n(w, &expect, 0, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {\n"
            '            char mdbuf[160];\n'
            '            int mdn = snprintf(mdbuf, sizeof(mdbuf), "mutex-deadowner: cond-fix off=%d owner=%u val=0x%x // ' + SENTINEL + '\\n",\n'
            "                               off, fowner, v);\n"
            "            if(mdn > 0) write(2, mdbuf, (size_t)mdn);\n"
            "            syscall(SYS_futex, (void*)w, FUTEX_WAKE_PRIVATE, 2147483647, NULL, NULL, 0);\n"
            "            return 1;\n"
            "        }\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
        )
        src = apply(src, alias, alias + fixfn, 1, "alias 行（插 fix 函数）", "wrappedlibpthread.c")

    with open(p1, "w", encoding="utf-8") as f:
        f.write(src)

    # 段1b：private.h GOM
    with open(p2, "r", encoding="utf-8") as f:
        h = f.read()
    if SENTINEL in h:
        print("patch_mutex_deadowner_wrap: private.h 已应用过，跳过")
    else:
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
        print("patch_mutex_deadowner_wrap: 已应用 -> wrappedlibpthread_private.h")

    # 段3：threads.c 三个 cond 桥入口预修复
    with open(p3, "r", encoding="utf-8") as f:
        t = f.read()
    if "box64_fix_dead_mutex(mutex)" in t:
        print("patch_mutex_deadowner_wrap: 段3 已应用过，跳过")
        print("patch_mutex_deadowner_wrap: 完成")
        return 0

    old_t1 = (
        "EXPORT int my_pthread_cond_timedwait(x64emu_t* emu, pthread_cond_t* cond, void* mutex, void* abstime)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tint ret = pthread_cond_timedwait(alignCond(cond), mutex, (const struct timespec*)abstime);"
    )
    new_t1 = (
        "extern int box64_fix_dead_mutex(void*);\n"
        "EXPORT int my_pthread_cond_timedwait(x64emu_t* emu, pthread_cond_t* cond, void* mutex, void* abstime)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tbox64_fix_dead_mutex(mutex);\n"
        "\tint ret = pthread_cond_timedwait(alignCond(cond), mutex, (const struct timespec*)abstime);"
    )
    t = apply(t, old_t1, new_t1, 1, "my_pthread_cond_timedwait", "threads.c")

    old_t2 = (
        "EXPORT int my_pthread_cond_wait(x64emu_t* emu, pthread_cond_t* cond, void* mutex)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tint ret = pthread_cond_wait(alignCond(cond), mutex);"
    )
    new_t2 = (
        "EXPORT int my_pthread_cond_wait(x64emu_t* emu, pthread_cond_t* cond, void* mutex)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tbox64_fix_dead_mutex(mutex);\n"
        "\tint ret = pthread_cond_wait(alignCond(cond), mutex);"
    )
    t = apply(t, old_t2, new_t2, 1, "my_pthread_cond_wait", "threads.c")

    old_t3 = (
        "EXPORT int my_pthread_cond_clockwait(x64emu_t *emu, pthread_cond_t* cond, void* mutex, clockid_t __clock_id, const struct timespec* __abstime)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tint ret;"
    )
    new_t3 = (
        "EXPORT int my_pthread_cond_clockwait(x64emu_t *emu, pthread_cond_t* cond, void* mutex, clockid_t __clock_id, const struct timespec* __abstime)\n"
        "{\n"
        "\t(void)emu;\n"
        "\tbox64_fix_dead_mutex(mutex);\n"
        "\tint ret;"
    )
    t = apply(t, old_t3, new_t3, 1, "my_pthread_cond_clockwait", "threads.c")

    with open(p3, "w", encoding="utf-8") as f:
        f.write(t)

    print("patch_mutex_deadowner_wrap: 已应用 -> wrappedlibpthread.c + wrappedlibpthread_private.h + threads.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_mutex_deadowner_wrap.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
