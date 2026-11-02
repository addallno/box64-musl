#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sig32-dlv-probe：32 位 guest 信号投递探针（哨兵 BOX64-BUILD: sig32-dlv-probe）。

背景：32 位 steamcmd 卡死在 crashhandler.so（breakpad）的信号处理器内：
同一线程对 crashhandler 静态普通 mutex 0x40b8cd60 在同一返回地址
（vaddr 0x74463，即 pthread_mutex_lock 调用点）连续两次 lock，中间无 unlock。
此时进程 SigBlk 几乎全屏蔽，说明正处在 breakpad handler 中，即 handler 被重入。

需要在 box64 侧看到每次 32 位信号投递的 sig/guest_rip/esp/handler 以及当时
box64_in_fatal_handler 深度，以确认：
1. 触发崩溃处理器的首个信号是什么（threadtools 断言？仿真 fault？）
2. 重入的那次投递是什么信号、故障 rip 在哪（据此修真正的仿真 bug）

改动（仅 src/libtools/signal32.c 的 my_sigactionhandler_oldcode_32 入口）：
在 LOG_DEBUG 投递日志后插入 dprintf(2, ...) 探针，上限 400 条防刷屏。

用法: patch_sig32_probe.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: sig32-dlv-probe"


def fail(msg):
    print(f"patch_sig32_probe: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "libtools", "signal32.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_sig32_probe: 已应用过，跳过")
        return 0

    old = (
        '    printf_log(LOG_DEBUG, "Sigactionhanlder32 for signal #%d called (jump to %p/%s)\\n",'
        ' sig, (void*)my_context->signals[sig], GetNativeName((void*)my_context->signals[sig], 1));\n'
    )
    new = old + (
        '    {   // ' + SENTINEL + '\n'
        '        static int s_dlv_probe_n = 0;\n'
        '        if(s_dlv_probe_n < 400) {\n'
        '            void* npc = NULL;\n'
        '            unsigned long saddr = info ? (unsigned long)info->si_addr : 0;\n'
        '            int scode = info ? info->si_code : 0;\n'
        '            #ifdef DYNAREC\n'
        '            if(ucntx) npc = (void*)CONTEXT_PC((ucontext_t*)ucntx);\n'
        '            #endif\n'
        '            ++s_dlv_probe_n;\n'
        '            dprintf(2, "BOX64 SIGDLV32 tid=%d sig=%d infatal=%d simple=%d code=%d guest_rip=%p esp=%p native_pc=%p si_addr=%p handler=%p restorer=%p\\n",\n'
        '                GetTID(), sig, box64_in_fatal_handler, simple, scode, (void*)R_EIP, (void*)R_ESP,\n'
        '                npc, (void*)saddr, (void*)my_context->signals[sig], (void*)my_context->restorer[sig]);\n'
        '            {   // BOX64-BUILD: memdbg-state\n'
        '                extern void box64_memdbg_state(void**, int*, int*);\n'
        '                extern int box64_memdbg_mapped(unsigned long);\n'
        '                extern unsigned int getProtection(uintptr_t);\n'
        '                extern int memExist(uintptr_t);\n'
        '                void* mpb = NULL; int mnb = 0, mcb = 0;\n'
        '                box64_memdbg_state(&mpb, &mnb, &mcb);\n'
        '                dprintf(2, "BOX64 MEMDBG p_blocks=%p n_blocks=%d c_blocks=%d si_mapped=%d end_mapped=%d prot=%x exist=%x\\n",\n'
        '                    mpb, mnb, mcb, box64_memdbg_mapped(saddr),\n'
        '                    box64_memdbg_mapped((unsigned long)mpb + (unsigned long)mnb * 40UL),\n'
        '                    getProtection((uintptr_t)saddr), memExist((uintptr_t)saddr));\n'
        '            }\n'
        '        }\n'
        '    }\n'
    )

    n = src.count(old)
    if n != 1:
        fail(f"锚点命中 {n} 次（期望 1）: {old[:90]!r}")
    src = src.replace(old, new)

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print("patch_sig32_probe: 已应用 -> signal32.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_sig32_probe.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
