#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alignedmutex-tag — 诊断探针：可见化 getAlignedMutex 分支 + my32 mutex 桥锁点

背景（本轮死锁 pid=8108）：3 线程全部 futex_wait 在 guest 字 0x40b8cd64
（*p=0x80000010 = LOCKED|WAITERS，glibc i386 语义），而按 fake mutex 布局、
0x40b8cd60 处 __kind=KIND_SIGN 且 real_mutex=0x30899580（host 锁）。
需分辨：getAlignedMutex 走了哪个分支（t0 内联锁 fake？real 直锁？fallback？）。

注入（src/libtools/threads32.c）：
  1) getAlignedMutex 整体替换为带打点版本（分支限频打 LOG_NONE，永不吞）：
     init t0 / init t1 / t0 inline（限频）/ KIND_SIGN real / FALLBACK（全打出）。
  2) my32_pthread_mutex_lock 入口加 per-pointer-once 表（64 上限）：
     首见指针打一行（fake 全字段快照 + rip/ra），之后每 4096 次再打一次。
哨兵：BOX64-BUILD: alignedmutex-tag（幂等）
用法：patch_alignedmutex_tag.py <box64源码目录>
"""
import re
import sys

S = "BOX64-BUILD: alignedmutex-tag"

TAGGED_BODY = r"""pthread_mutex_t* getAlignedMutex(pthread_mutex_t* m)
{
    // %s — 分支打点版（语义与原版一致）
    fake_phtread_mutex_t* fake = (fake_phtread_mutex_t*)m;
    if(!fake->__lock && !fake->__count && !fake->__owner && !fake->__kind && !fake->i386__kind && !fake->real_mutex) {
        static int amv_i0 = 0;
        if(amv_i0++ < 20) printf_log(LOG_NONE, "[amv] init t0 m=%%p\n", m);
        fake->real_mutex = KIND_SIGN;
    }
    if(!fake->__lock && !fake->__count && !fake->__owner && !fake->__kind && fake->i386__kind==1 && !fake->real_mutex) {
        static int amv_i1 = 0;
        if(amv_i1++ < 20) printf_log(LOG_NONE, "[amv] init t1 m=%%p\n", m);
        fake->real_mutex = KIND_SIGN;
        fake->__kind = 1;
        fake->i386__kind = 0;
    }
    if(fake->real_mutex==KIND_SIGN) {
        static int amv_t0 = 0;
        if(!(amv_t0%%20000)) printf_log(LOG_NONE, "[amv] t0-inline m=%%p lock=%%d count=%%d owner=%%d kind=%%x\n", m, fake->__lock, fake->__count, fake->__owner, fake->__kind);
        return m;    // type 0 can fit...
    }
    if(fake->__kind==KIND_SIGN) {
        static int amv_re = 0;
        if(!(amv_re%%40000)) printf_log(LOG_NONE, "[amv] real m=%%p realp=%%p lock=%%d\n", m, from_ptrv(fake->real_mutex), *(int*)from_ptrv(fake->real_mutex));
        return from_ptrv(fake->real_mutex);
    }
    // this should not appens!
    printf_log(LOG_NONE, "[amv] FALLBACK aligned mutex m=%%p i386kind=%%d real=%%p\n", m, fake->i386__kind, (void*)fake->real_mutex);
    fake->real_mutex = to_ptrv(createNewMutex());
    pthread_mutexattr_t attr = {0};
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, fake->i386__kind);
    pthread_mutex_init(from_ptrv(fake->real_mutex), &attr);
    pthread_mutexattr_destroy(&attr);
    fake->__kind = KIND_SIGN;
    return from_ptrv(fake->real_mutex);
}
""" % S

LOCK_INSERT = r"""
    // %s — per-pointer-once 锁点快照（table 64 上限）
    {
        static void* amv_seen[64]; static int amv_cnt[64]; static int amv_n = 0;
        static pthread_mutex_t amv_tablk = PTHREAD_MUTEX_INITIALIZER;
        pthread_mutex_lock(&amv_tablk);
        int amv_idx = -1;
        for(int i=0; i<amv_n; ++i) if(amv_seen[i]==(void*)m) { amv_idx=i; break; }
        if(amv_idx<0 && amv_n<64) { amv_seen[amv_n]=(void*)m; amv_cnt[amv_n]=0; amv_idx=amv_n++; }
        if(amv_idx>=0) {
            fake_phtread_mutex_t* amv_f = (fake_phtread_mutex_t*)m;
            if(!(amv_cnt[amv_idx]%%4096))
                printf_log(LOG_NONE, "[amv] lock m=%%p c=%%d lock=%%d count=%%d owner=%%d i386k=%%d kind=%%x real=%%p\n",
                    m, amv_cnt[amv_idx], amv_f->__lock, amv_f->__count, amv_f->__owner, amv_f->i386__kind, amv_f->__kind, (void*)amv_f->real_mutex);
            if(amv_cnt[amv_idx] < 0x40000000) ++amv_cnt[amv_idx];
        }
        pthread_mutex_unlock(&amv_tablk);
    }
""" % S

def main(srcdir):
    p = srcdir + "/src/libtools/threads32.c"
    src = open(p, encoding="utf-8").read()
    if S in src:
        print("already patched (idempotent)")
        return 0
    # 1) 整体替换 getAlignedMutex
    m = re.search(r"pthread_mutex_t\* getAlignedMutex\(pthread_mutex_t\* m\)\n\{.*?\n\}\n", src, re.S)
    if not m:
        print("FAIL: getAlignedMutex not found"); return 1
    src = src[:m.start()] + TAGGED_BODY + src[m.end():]
    # 2) my32_pthread_mutex_lock 入口注入
    m2 = re.search(r"EXPORT int my32_pthread_mutex_lock\(pthread_mutex_t \*m\)\n\{\n", src)
    if not m2:
        print("FAIL: my32_pthread_mutex_lock not found"); return 1
    src = src[:m2.end()] + LOCK_INSERT + src[m2.end():]
    open(p, "w", encoding="utf-8").write(src)
    print("patched", p)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
