#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
threads32-deadowner + threads32-mtrace

段 A（deadowner）：32 位 mutex/cond 桥复位已死 owner 锁字，防 KIND_SIGN 透传 musl 死锁。
  rootcause：fake_phtread_mutex_t real_mutex==KIND_SIGN 时 getAlignedMutex 直接 return m，
  宿主 musl 把 glibc 布局 guest 内存当自己的 pthread_mutex_t（aarch64 musl 锁字在 +4）。
  卡死实测 0x40b8cd64 = 0x80000010（WAITERS|locked），/proc/self/task/16 不存在。

段 B（mtrace）：定点追踪。设 BOX64_MTRACE_ADDR=<guest 地址> 后，任何 mutex/cond 桥调用
  只要 guest mutex 或 cond 指针命中该地址，就打一行到 fd2：
  mtrace tid=<tid> <fn>-<entry|exit> m=.. c=.. r=.. w=<m+0..m+16 五个字>
  用于判定死锁对象 0x80000010 是谁写、谁持锁不还。

依赖：box64_fix_dead_mutex 定义于 wrappedlibpthread.c STATICBUILD 块
（patch_mutex_deadowner_wrap.py 注入）；本补丁须排在其后。

用法: patch_threads32_deadowner.py <box64源码目录>
"""
import os
import sys

S_FIX = "BOX64-BUILD: threads32-deadowner"
S_TRACE = "BOX64-BUILD: threads32-mtrace"
EXT_DECL = "extern int box64_fix_dead_mutex(void*);"

HELPER = """
// %s — 定点追踪（BOX64_MTRACE_ADDR 指定 guest mutex/cond 地址）
static int box64_mtrace_on(void* m, void* c)
{
    static void* want = (void*)-1;
    if(want == (void*)-1) {
        const char* e = getenv("BOX64_MTRACE_ADDR");
        want = e ? (void*)strtoull(e, NULL, 0) : NULL;
    }
    return want && (m == want || c == want);
}
static void box64_mtrace(const char* fn, const char* ph, void* m, void* c, int ret)
{
    if(!box64_mtrace_on(m, c)) return;
    uint32_t* w = (uint32_t*)m;
    char b[256];
    int n = snprintf(b, sizeof(b),
        "mtrace tid=%%d %%s-%%s m=%%p c=%%p r=%%d w=%%08x,%%08x,%%08x,%%08x,%%08x // %s\\n",
        (int)GetTID(), fn, ph, m, c, ret,
        w?w[0]:0, w?w[1]:0, w?w[2]:0, w?w[3]:0, w?w[4]:0);
    if(n > 0) write(2, b, (size_t)n);
}
""" % (S_TRACE, S_TRACE)

R_ENTRY = "-999"


def fail(msg):
    print(f"patch_threads32_deadowner: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name):
    n = src.count(old)
    if n != want:
        fail(f"锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:90]!r}")
    return src.replace(old, new)


def repl_once(src, old, new, name):
    if new in src:
        return src
    return apply(src, old, new, 1, name)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "libtools", "threads32.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    # 段 0：头文件（write / uint32_t）
    if "#include <unistd.h>" not in src:
        src = apply(src, "#include <dlfcn.h>\n",
                    "#include <dlfcn.h>\n#include <unistd.h>\n#include <stdint.h>\n",
                    1, "include dlfcn.h")

    # 段 1：extern 声明
    if EXT_DECL not in src:
        src = apply(
            src,
            "pthread_mutex_t* getAlignedMutex(pthread_mutex_t* m);\n",
            "pthread_mutex_t* getAlignedMutex(pthread_mutex_t* m);\n"
            "// " + S_FIX + " — 复位已死 owner 锁字（定义见 wrappedlibpthread.c STATICBUILD）\n"
            + EXT_DECL + "\n",
            1, "getAlignedMutex 前向声明")

    # 段 2：mtrace 辅助函数
    if "static void box64_mtrace(" not in src:
        src = apply(src, EXT_DECL + "\n", EXT_DECL + "\n" + HELPER, 1, "extern 声明后插 helper")

    # 段 3：四个 mutex 函数（trace + fix 合并写入）
    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_lock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_lock(getAlignedMutex(m));\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_lock(pthread_mutex_t *m)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        f"    box64_mtrace(\"lock\", \"entry\", m, NULL, {R_ENTRY});  // " + S_TRACE + "\n"
        "    box64_fix_dead_mutex(am);  // " + S_FIX + "\n"
        "    int r = pthread_mutex_lock(am);\n"
        f"    box64_mtrace(\"lock\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n",
        "my32_pthread_mutex_lock")

    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_timedlock(pthread_mutex_t *m, const struct timespec * t)\n"
        "{\n"
        "    return pthread_mutex_timedlock(getAlignedMutex(m), t);\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_timedlock(pthread_mutex_t *m, const struct timespec * t)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        f"    box64_mtrace(\"timedlock\", \"entry\", m, NULL, {R_ENTRY});  // " + S_TRACE + "\n"
        "    box64_fix_dead_mutex(am);  // " + S_FIX + "\n"
        "    int r = pthread_mutex_timedlock(am, t);\n"
        f"    box64_mtrace(\"timedlock\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n",
        "my32_pthread_mutex_timedlock")

    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_trylock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_trylock(getAlignedMutex(m));\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_trylock(pthread_mutex_t *m)\n"
        "{\n"
        "    pthread_mutex_t* am = getAlignedMutex(m);\n"
        f"    box64_mtrace(\"trylock\", \"entry\", m, NULL, {R_ENTRY});  // " + S_TRACE + "\n"
        "    box64_fix_dead_mutex(am);  // " + S_FIX + "\n"
        "    int r = pthread_mutex_trylock(am);\n"
        f"    box64_mtrace(\"trylock\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n",
        "my32_pthread_mutex_trylock")

    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_unlock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_unlock(getAlignedMutex(m));\n"
        "}\n",
        "EXPORT int my32_pthread_mutex_unlock(pthread_mutex_t *m)\n"
        "{\n"
        f"    box64_mtrace(\"unlock\", \"entry\", m, NULL, {R_ENTRY});  // " + S_TRACE + "\n"
        "    int r = pthread_mutex_unlock(getAlignedMutex(m));\n"
        f"    box64_mtrace(\"unlock\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n",
        "my32_pthread_mutex_unlock")

    # 段 4：四个 cond 桥（trace + fix）
    cond_specs = [
        ("my32_pthread_cond_timedwait_old",
         "EXPORT int my32_pthread_cond_timedwait_old(x64emu_t* emu, pthread_cond_2_0_t* cond, void* mutex, void* abstime)\n"
         "{\n"
         "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
         "    pthread_cond_t * c = get_cond_old(cond);\n"
         "    struct timespec* atime = abstime;\n"
         "    while(atime->tv_nsec>1000000000LL) {\n"
         "        atime->tv_nsec-=1000000000LL;\n"
         "        ++atime->tv_sec;\n"
         "    }\n"
         "    return pthread_cond_timedwait(c, m, atime);\n"
         "}\n",
         "    pthread_cond_t * c = get_cond_old(cond);\n"
         "    struct timespec* atime = abstime;\n"
         "    while(atime->tv_nsec>1000000000LL) {\n"
         "        atime->tv_nsec-=1000000000LL;\n"
         "        ++atime->tv_sec;\n"
         "    }\n"
         "    return pthread_cond_timedwait(c, m, atime);\n"),
        ("my32_pthread_cond_wait_old",
         "EXPORT int my32_pthread_cond_wait_old(x64emu_t* emu, pthread_cond_2_0_t* cond, void* mutex)\n"
         "{\n"
         "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
         "    pthread_cond_t * c = get_cond_old(cond);\n"
         "    return pthread_cond_wait(c, m);\n"
         "}\n",
         "    pthread_cond_t * c = get_cond_old(cond);\n"
         "    return pthread_cond_wait(c, m);\n"),
        ("my32_pthread_cond_timedwait",
         "EXPORT int my32_pthread_cond_timedwait(x64emu_t* emu, void* cond, void* mutex, void* abstime)\n"
         "{\n"
         "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
         "    pthread_cond_t * c = get_cond(cond);\n"
         "    struct timespec* atime = abstime;\n"
         "    while(atime->tv_nsec>1000000000LL) {\n"
         "        atime->tv_nsec-=1000000000LL;\n"
         "        ++atime->tv_sec;\n"
         "    }\n"
         "    return pthread_cond_timedwait(c, m, atime);\n"
         "}\n",
         "    pthread_cond_t * c = get_cond(cond);\n"
         "    struct timespec* atime = abstime;\n"
         "    while(atime->tv_nsec>1000000000LL) {\n"
         "        atime->tv_nsec-=1000000000LL;\n"
         "        ++atime->tv_sec;\n"
         "    }\n"
         "    return pthread_cond_timedwait(c, m, atime);\n"),
        ("my32_pthread_cond_wait",
         "EXPORT int my32_pthread_cond_wait(x64emu_t* emu, void* cond, void* mutex)\n"
         "{\n"
         "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
         "    pthread_cond_t * c = get_cond(cond);\n"
         "    return pthread_cond_wait(c, m);\n"
         "}\n",
         "    pthread_cond_t * c = get_cond(cond);\n"
         "    return pthread_cond_wait(c, m);\n"),
    ]
    for fname, old_full, tail in cond_specs:
        if f"box64_mtrace(\"{fname}\"" in src:
            continue
        # 在 getAlignedMutex 之后插入 trace + fix，返回值改为局部 r
        head = f"    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
        ins = (head
               + f"    box64_mtrace(\"{fname}\", \"entry\", mutex, cond, {R_ENTRY});  // " + S_TRACE + "\n"
               + "    box64_fix_dead_mutex(m);  // " + S_FIX + "\n")
        new_tail = tail.replace("    return ", "    int r = ")
        new_tail = new_tail.rstrip("\n") + f"\n    box64_mtrace(\"{fname}\", \"exit\", mutex, cond, r);  // " + S_TRACE + "\n    return r;\n"
        new_full = old_full.replace(head + tail, ins + new_tail)
        src = apply(src, old_full, new_full, 1, fname)

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print("patch_threads32_deadowner: 应用完成（deadowner + mtrace）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
