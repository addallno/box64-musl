#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
threads32-deadowner + threads32-mtrace

段 A（deadowner）：32 位 mutex/cond 桥复位已死 owner 锁字，防 KIND_SIGN 透传 musl 死锁。
段 B（mtrace/mhold）：诊断探针，两种模式可独立开启：
  - BOX64_MTRACE_ADDR=<guest地址> [BOX64_MTRACE_LEN=<范围，默认4>]
      命中地址范围的 mutex/cond 桥调用打一行（含 guest rip/esp/返回地址）。
  - BOX64_MTRACE_HOLD=1
      维护"mutex → 持有者 tid/获取点"全局表；同一线程重入自己已持有的 mutex 时
      打印 mhold SELFRELOCK（含首次获取点 hrip/hra 与本次 rip/ra/栈顶 4 字）。
      用于定位 32 位 steamcmd "同线程连续两次 lock 同一普通 mutex" 的调用点。

依赖 box64_fix_dead_mutex（wrappedlibpthread.c STATICBUILD，patch_mutex_deadowner_wrap.py 注入）。
用法: patch_threads32_deadowner.py <box64源码目录>
"""
import os
import sys

S_FIX = "BOX64-BUILD: threads32-deadowner"
S_TRACE = "BOX64-BUILD: threads32-mtrace"
EXT_DECL = "extern int box64_fix_dead_mutex(void*);"

HELPER = """
// %s — 诊断探针（BOX64_MTRACE_ADDR/LEN 地址模式；BOX64_MTRACE_HOLD 持有者表模式）
#define MTR_HOLD_MAX 256
typedef struct { void* m; int tid; uint32_t rip; uint32_t ra; } mtr_hold_t;
static mtr_hold_t mtr_hold_tab[MTR_HOLD_MAX];
static volatile int mtr_hold_busy = 0;
static int mtr_hold_logs = 0;
static int box64_mtrace_on(void* m, void* c)
{
    static void* want = (void*)-1;
    static uintptr_t len = 4;
    if(want == (void*)-1) {
        const char* e = getenv("BOX64_MTRACE_ADDR");
        want = e ? (void*)strtoull(e, NULL, 0) : NULL;
        const char* l = getenv("BOX64_MTRACE_LEN");
        if(l) len = (uintptr_t)strtoull(l, NULL, 0);
        if(!len) len = 4;
    }
    if(!want) return 0;
    if((uintptr_t)m - (uintptr_t)want < len) return 1;
    if(c && (uintptr_t)c - (uintptr_t)want < len) return 1;
    return 0;
}
static int box64_mhold_on(void)
{
    static int e = -1;
    if(e < 0) e = getenv("BOX64_MTRACE_HOLD") ? 1 : 0;
    return e;
}
static void box64_mhold_lock(void) { while(__sync_lock_test_and_set(&mtr_hold_busy, 1)) { while(mtr_hold_busy) ; } }
static void box64_mhold_unlock(void) { __sync_lock_release(&mtr_hold_busy); }
static void box64_mtrace(const char* fn, const char* ph, void* m, void* c, int ret)
{
    if(!box64_mtrace_on(m, c)) return;
    uint32_t* w = (uint32_t*)m;
    uint32_t rip = 0, esp = 0, ra = 0;
    x64emu_t* emu = thread_get_emu_no_create();
    if(emu) {
        rip = (uint32_t)R_RIP;
        esp = (uint32_t)R_ESP;
        if(esp > 0x10000u) ra = *(uint32_t*)(uintptr_t)esp;
    }
    char b[320];
    int n = snprintf(b, sizeof(b),
        "mtrace tid=%%d %%s-%%s m=%%p c=%%p r=%%d w=%%08x,%%08x,%%08x,%%08x,%%08x rip=%%08x esp=%%08x ra=%%08x // %s\\n",
        (int)GetTID(), fn, ph, m, c, ret,
        w?w[0]:0, w?w[1]:0, w?w[2]:0, w?w[3]:0, w?w[4]:0,
        rip, esp, ra);
    if(n > 0) write(2, b, (size_t)n);
}
static void box64_mhold_check(const char* fn, void* m)
{
    if(!box64_mhold_on()) return;
    int me = (int)GetTID();
    int i;
    box64_mhold_lock();
    for(i = 0; i < MTR_HOLD_MAX; ++i) {
        if(mtr_hold_tab[i].m == m) {
            if(mtr_hold_tab[i].tid == me && mtr_hold_logs < 200) {
                uint32_t* w = (uint32_t*)m;
                uint32_t rip = 0, esp = 0, ra = 0, s0 = 0, s1 = 0, s2 = 0, s3 = 0;
                x64emu_t* emu = thread_get_emu_no_create();
                if(emu) {
                    rip = (uint32_t)R_RIP;
                    esp = (uint32_t)R_ESP;
                    if(esp > 0x10000u) {
                        uint32_t* sp = (uint32_t*)(uintptr_t)esp;
                        ra = sp[0]; s0 = sp[1]; s1 = sp[2]; s2 = sp[3]; s3 = sp[4];
                    }
                }
                ++mtr_hold_logs;
                char b[384];
                int n = snprintf(b, sizeof(b),
                    "mhold tid=%%d SELFRELOCK %%s m=%%p w=%%08x,%%08x,%%08x,%%08x,%%08x cur_rip=%%08x cur_ra=%%08x hold_rip=%%08x hold_ra=%%08x stk=%%08x,%%08x,%%08x,%%08x // %s\\n",
                    me, fn, m,
                    w?w[0]:0, w?w[1]:0, w?w[2]:0, w?w[3]:0, w?w[4]:0,
                    rip, ra, mtr_hold_tab[i].rip, mtr_hold_tab[i].ra,
                    s0, s1, s2, s3);
                if(n > 0) write(2, b, (size_t)n);
            }
            break;
        }
    }
    box64_mhold_unlock();
}
static void box64_mhold_add(void* m)
{
    if(!box64_mhold_on()) return;
    int me = (int)GetTID();
    int i, free_i = -1;
    uint32_t rip = 0, esp = 0, ra = 0;
    x64emu_t* emu = thread_get_emu_no_create();
    if(emu) {
        rip = (uint32_t)R_RIP;
        esp = (uint32_t)R_ESP;
        if(esp > 0x10000u) ra = *(uint32_t*)(uintptr_t)esp;
    }
    box64_mhold_lock();
    for(i = 0; i < MTR_HOLD_MAX; ++i) {
        if(mtr_hold_tab[i].m == m) { free_i = i; break; }
        if(!mtr_hold_tab[i].m && free_i < 0) free_i = i;
    }
    if(free_i >= 0) {
        mtr_hold_tab[free_i].m = m;
        mtr_hold_tab[free_i].tid = me;
        mtr_hold_tab[free_i].rip = rip;
        mtr_hold_tab[free_i].ra = ra;
    }
    box64_mhold_unlock();
}
static void box64_mhold_del(void* m)
{
    if(!box64_mhold_on()) return;
    int i;
    box64_mhold_lock();
    for(i = 0; i < MTR_HOLD_MAX; ++i)
        if(mtr_hold_tab[i].m == m) { mtr_hold_tab[i].m = NULL; mtr_hold_tab[i].tid = 0; break; }
    box64_mhold_unlock();
}
""" % (S_TRACE, S_TRACE, S_TRACE)

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


def mutex_body(fn, call, sig):
    """生成 mutex 函数体：trace 入口 → 持有者检查 → fix → 调用 → 记录持有 → trace 出口"""
    return (
        f"EXPORT int {fn}({sig})\n"
        "{\n"
        f"    pthread_mutex_t* am = getAlignedMutex(m);\n"
        f"    box64_mtrace(\"{fn}\", \"entry\", m, NULL, {R_ENTRY});  // " + S_TRACE + "\n"
        f"    box64_mhold_check(\"{fn}\", m);  // " + S_TRACE + "\n"
        "    box64_fix_dead_mutex(am);  // " + S_FIX + "\n"
        f"    int r = {call};\n"
        "    if(!r) box64_mhold_add(m);  // " + S_TRACE + "\n"
        f"    box64_mtrace(\"{fn}\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n"
    )


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

    # 段 2：探针辅助函数（整块替换，保证与脚本一致）
    start = src.find("// " + S_TRACE + " — ")
    if start >= 0:
        end = src.find("\n}\n", src.find("static void box64_mhold_del", start))
        end = src.find("\n", end + 1) if end >= 0 else -1
        if end < 0:
            fail("已有 mtrace 辅助块无法定位结尾")
        src = src[:start] + HELPER.lstrip("\n") + src[end + 1:]
    else:
        src = apply(src, EXT_DECL + "\n", EXT_DECL + "\n" + HELPER, 1, "extern 声明后插 helper")

    # 段 3：四个 mutex 函数
    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_lock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_lock(getAlignedMutex(m));\n"
        "}\n",
        mutex_body("my32_pthread_mutex_lock", "pthread_mutex_lock(am)", "pthread_mutex_t *m"),
        "my32_pthread_mutex_lock")

    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_timedlock(pthread_mutex_t *m, const struct timespec * t)\n"
        "{\n"
        "    return pthread_mutex_timedlock(getAlignedMutex(m), t);\n"
        "}\n",
        mutex_body("my32_pthread_mutex_timedlock", "pthread_mutex_timedlock(am, t)",
                   "pthread_mutex_t *m, const struct timespec * t"),
        "my32_pthread_mutex_timedlock")

    src = repl_once(
        src,
        "EXPORT int my32_pthread_mutex_trylock(pthread_mutex_t *m)\n"
        "{\n"
        "    return pthread_mutex_trylock(getAlignedMutex(m));\n"
        "}\n",
        mutex_body("my32_pthread_mutex_trylock", "pthread_mutex_trylock(am)", "pthread_mutex_t *m"),
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
        "    if(!r) box64_mhold_del(m);  // " + S_TRACE + "\n"
        f"    box64_mtrace(\"unlock\", \"exit\", m, NULL, r);  // " + S_TRACE + "\n"
        "    return r;\n"
        "}\n",
        "my32_pthread_mutex_unlock")

    # 段 4：四个 cond 桥（入口先摘表，出口重挂：musl 内部会释放/重获 mutex）
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
        if f"box64_mhold_del(mutex)" in src and f"\"{fname}\"" in src:
            continue
        head = "    pthread_mutex_t* m = getAlignedMutex((pthread_mutex_t*)mutex);\n"
        ins = (head
               + f"    box64_mtrace(\"{fname}\", \"entry\", mutex, cond, {R_ENTRY});  // " + S_TRACE + "\n"
               + "    box64_mhold_del(mutex);  // " + S_TRACE + "\n"
               + "    box64_fix_dead_mutex(m);  // " + S_FIX + "\n")
        new_tail = tail.replace("    return ", "    int r = ")
        new_tail = (new_tail.rstrip("\n") + "\n"
                    + "    box64_mhold_add(mutex);  // " + S_TRACE + "\n"
                    + f"    box64_mtrace(\"{fname}\", \"exit\", mutex, cond, r);  // " + S_TRACE + "\n"
                    + "    return r;\n")
        new_full = old_full.replace(head + tail, ins + new_tail)
        src = apply(src, old_full, new_full, 1, fname)

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print("patch_threads32_deadowner: 应用完成（deadowner + mtrace/mhold）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
