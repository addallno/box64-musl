#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
killarray-probe：p_blocks 数组页被删凶手定位（四探针，哨兵 BOX64-BUILD: killarray-probe）。

背景：32 位 steamcmd SIGDLV32 崩在 internal_customMalloc/map128_customMalloc 读
p_blocks[i]，MEMDBG 显示 si_mapped=-1（mincore 页不存在）但 exist=0x11（簿记仍在），
现场 0x7fb7503000-0x7fb750b000 是无 VMA 的洞。历史结论"所有 munmap 通道探针全 0"
→ 删页不走 EXPORT munmap/free/box_munmap/DelMmaplist 常规通道。
剩余嫌疑通道与本探针对应：
1. KILLUNMAP  — InternalMunmap 裸 syscall（绕过一切簿记），过滤命中 p_blocks 数组页
2. KILLREMAP  — musl realloc 的 mremap（MAYMOVE 移动/缩小自行 unmap 旧页，box64 从未
                hook；patch_p12dbg v8 只打了 guest my_mremap，musl 内部 mremap 不经过它）
3. RESERVEFAIL — DelMmaplist：InternalMunmap 后 PROT_NONE 占位 mmap 失败 → 真实洞+
                簿记残留（mincore=-1 + exist=0x11 的精确吻合形态）
4. COLLIDE    — box32_dynarec_mmap 按 MEM_RESERVED 簿记 MAP_FIXED RWX 覆盖：目标
                getProtection!=0 说明盖的是已登记真实页（musl 数组页）而非 ---p 保留区

所有探针只在地址与 p_blocks 数组页区间相交时打印（零常态噪音）；
RESERVEFAIL/COLLIDE 无条件打印（本身即异常事件，频率极低）。

用法: patch_killarray_probe.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: killarray-probe"


def fail(msg):
    print(f"patch_killarray_probe: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def sub1(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        fail(f"{tag}: 锚点命中 {n} 次（期望 1）: {old[:90]!r}")
    return src.replace(old, new, 1)


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = sys.argv[1]

    # ---- 1/2: os_linux.c — InternalMunmap 相交打点 ----
    ol = os.path.join(root, "src", "os", "os_linux.c")
    if not os.path.isfile(ol):
        fail(f"文件不存在: {ol}")
    s = open(ol, encoding="utf-8").read()
    if SENTINEL not in s:
        if "#include <stdio.h>" not in s:
            s = sub1(
                s,
                "#include <sys/syscall.h>\n",
                "#include <sys/syscall.h>\n#include <stdio.h> // BOX64-BUILD: killarray-probe\n",
                "os_linux.c include",
            )
        anchor = (
            "int InternalMunmap(void* addr, unsigned long length)\n"
            "{\n"
        )
        new = (
            anchor +
            "    { // " + SENTINEL + " KILLUNMAP 裸 syscall 删 p_blocks 数组页=直接凶手\n"
            "        extern void box64_memdbg_state(void**, int*, int*);\n"
            "        void* k_pb = NULL; int k_nb = 0, k_cb = 0;\n"
            "        box64_memdbg_state(&k_pb, &k_nb, &k_cb);\n"
            "        if(k_pb) {\n"
            "            uintptr_t k_a0 = (uintptr_t)addr, k_a1 = k_a0 + length;\n"
            "            uintptr_t k_p0 = ((uintptr_t)k_pb) & ~0xfffUL;\n"
            "            uintptr_t k_p1 = (((uintptr_t)k_pb) + (unsigned long)k_nb * 40UL + 0xfffUL) & ~0xfffUL;\n"
            "            if(k_a0 < k_p1 && k_a1 > k_p0)\n"
            '                fprintf(stderr, "KILLUNMAP addr=%p len=%lu p_blocks=%p n=%d caller=%p\\n",\n'
            "                    addr, length, k_pb, k_nb, __builtin_return_address(0));\n"
            "        }\n"
            "    }\n"
        )
        s = sub1(s, anchor, new, "os_linux.c KILLUNMAP")
        open(ol, "w", encoding="utf-8").write(s)
        print("[patch_killarray_probe] os_linux.c: KILLUNMAP 注入完成")
    else:
        print("[patch_killarray_probe] os_linux.c 已应用过，跳过")

    # ---- 1/2b: custommmap.c — mremap 强定义（musl realloc 通道）----
    cm = os.path.join(root, "src", "custommmap.c")
    if not os.path.isfile(cm):
        fail(f"文件不存在: {cm}")
    s = open(cm, encoding="utf-8").read()
    if SENTINEL not in s:
        if "#include <stdio.h>" not in s:
            s = sub1(
                s,
                "#include <stdint.h>\n",
                "#include <stdint.h>\n// BOX64-BUILD: killarray-probe\n#include <stdio.h>\n#include <stdarg.h>\n#include <sys/syscall.h>\n#ifndef MREMAP_MAYMOVE\n#define MREMAP_MAYMOVE 1\n#endif\n#ifndef MREMAP_FIXED\n#define MREMAP_FIXED 2\n#endif\n",
                "custommmap.c include",
            )
        anchor = "EXPORT int munmap(void* addr, unsigned long length)\n"
        mremap_fn = (
            "// " + SENTINEL + " KILLREMAP musl realloc 的 mremap 通道（MAYMOVE 移动自行 unmap 旧页，\n"
            "// 历史所有 munmap 探针都抓不到；静态 musl 下同名 EXPORT 劫持与 munmap 同理）\n"
            "EXPORT void* mremap(void* old_addr, size_t old_size, size_t new_size, int flags, ...)\n"
            "{\n"
            "    void* new_addr = NULL;\n"
            "    if(flags & MREMAP_FIXED) {\n"
            "        va_list ap;\n"
            "        va_start(ap, flags);\n"
            "        new_addr = va_arg(ap, void*);\n"
            "        va_end(ap);\n"
            "    }\n"
            "    void* ret = (void*)syscall(SYS_mremap, old_addr, old_size, new_size, flags, new_addr);\n"
            "    if(box64_is32bits) {\n"
            "        extern void box64_memdbg_state(void**, int*, int*);\n"
            "        void* k_pb = NULL; int k_nb = 0, k_cb = 0;\n"
            "        box64_memdbg_state(&k_pb, &k_nb, &k_cb);\n"
            "        if(k_pb) {\n"
            "            uintptr_t k_p0 = ((uintptr_t)k_pb) & ~0xfffUL;\n"
            "            uintptr_t k_p1 = (((uintptr_t)k_pb) + (unsigned long)k_nb * 40UL + 0xfffUL) & ~0xfffUL;\n"
            "            uintptr_t k_o0 = (uintptr_t)old_addr, k_o1 = k_o0 + old_size;\n"
            "            int k_oldhit = (k_o0 < k_p1 && k_o1 > k_p0);\n"
            "            int k_newhit = 0;\n"
            "            if(ret != (void*)-1 && (flags & MREMAP_MAYMOVE)) {\n"
            "                uintptr_t k_n0 = (uintptr_t)ret, k_n1 = k_n0 + new_size;\n"
            "                k_newhit = (k_n0 < k_p1 && k_n1 > k_p0);\n"
            "            }\n"
            "            if(k_oldhit || k_newhit)\n"
            '                fprintf(stderr, "KILLREMAP old=%p osz=%lu nsz=%lu fl=%d ret=%p p_blocks=%p n=%d oldhit=%d newhit=%d caller=%p\\n",\n'
            "                    old_addr, (unsigned long)old_size, (unsigned long)new_size, flags, ret, k_pb, k_nb, k_oldhit, k_newhit,\n"
            "                    __builtin_return_address(0));\n"
            "        }\n"
            "    }\n"
            "    return ret;\n"
            "}\n"
        )
        s = sub1(s, anchor, mremap_fn + anchor, "custommmap.c mremap")
        open(cm, "w", encoding="utf-8").write(s)
        print("[patch_killarray_probe] custommmap.c: KILLREMAP(mremap 强定义) 注入完成")
    else:
        print("[patch_killarray_probe] custommmap.c 已应用过，跳过")

    # ---- 3/4: custommem.c — DelMmaplist RESERVEFAIL + box32_dynarec_mmap COLLIDE ----
    cme = os.path.join(root, "src", "custommem.c")
    if not os.path.isfile(cme):
        fail(f"文件不存在: {cme}")
    s = open(cme, encoding="utf-8").read()
    if SENTINEL not in s:
        # 3: DelMmaplist 占位失败 → 真实洞+簿记残留
        anchor = (
            "                if(InternalMmap(addr, size, 0, MAP_NORESERVE|MAP_ANONYMOUS|MAP_FIXED, -1, 0)!=MAP_FAILED)\n"
            "                    rb_set(mapallmem, (uintptr_t)addr, (uintptr_t)addr+size, MEM_RESERVED);\n"
        )
        new = (
            "                // " + SENTINEL + " RESERVEFAIL 占位失败=真实洞+簿记残留（mincore=-1+exist=0x11 形态）\n"
            "                if(InternalMmap(addr, size, 0, MAP_NORESERVE|MAP_ANONYMOUS|MAP_FIXED, -1, 0)!=MAP_FAILED)\n"
            "                    rb_set(mapallmem, (uintptr_t)addr, (uintptr_t)addr+size, MEM_RESERVED);\n"
            "                else\n"
            '                    fprintf(stderr, "RESERVEFAIL addr=%p size=%zu\\n", addr, size);\n'
        )
        s = sub1(s, anchor, new, "custommem.c RESERVEFAIL")

        # 4: box32_dynarec_mmap MAP_FIXED 前：目标 getProtection!=0 = 盖真实页
        anchor = (
            "                void* ret = InternalMmap((void*)cur, size, PROT_READ | PROT_WRITE | PROT_EXEC, map_flags, fd, offset);\n"
        )
        new = (
            "                { // " + SENTINEL + " COLLIDE 目标已登记可读写页（musl 数组页）而非 ---p 保留区\n"
            "                    extern unsigned int getProtection(uintptr_t);\n"
            "                    unsigned int k_prot = getProtection((uintptr_t)cur);\n"
            "                    if(k_prot)\n"
            '                        fprintf(stderr, "COLLIDE cur=%p size=%zu flag=0x%x prot=0x%x\\n", (void*)cur, size, flag, k_prot);\n'
            "                }\n"
            + anchor
        )
        s = sub1(s, anchor, new, "custommem.c COLLIDE")
        open(cme, "w", encoding="utf-8").write(s)
        print("[patch_killarray_probe] custommem.c: RESERVEFAIL+COLLIDE 注入完成")
    else:
        print("[patch_killarray_probe] custommem.c 已应用过，跳过")

    print("patch_killarray_probe: 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
