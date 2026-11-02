#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memdbg-state：导出 custommem 全局状态 + VMA 探测（哨兵 BOX64-BUILD: memdbg-state）。

背景：32 位 steamcmd 卡死的起点是 box64 自身 custommem 分配器在
`internal_customMalloc` / `map128_customMalloc` 里读 `p_blocks[i]` 时
SIGSEGV(code=2 SEGV_ACCERR)，si_addr 落在 musl 堆旁的“空洞”。
需要在崩溃瞬间拿到 p_blocks/n_blocks/c_blocks 与 si_addr 的映射状态，
才能判定是“容量簿记错乱”还是“p_blocks 已失效”。

改动（src/custommem.c，p_blocks 声明之后）：
1. box64_memdbg_state()：导出 p_blocks/n_blocks/c_blocks
2. box64_memdbg_mapped()：用 mincore 判断某地址页是否真实映射

用法: patch_memdbg_state.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: memdbg-state"


def fail(msg):
    print(f"patch_memdbg_state: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "custommem.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_memdbg_state: 已应用过，跳过")
        return 0

    old = "static blocklist_t*        p_blocks = NULL;    // actual blocks for custom malloc\n"
    new = old + (
        "\n// " + SENTINEL + "\n"
        "void box64_memdbg_state(void** pb, int* nb, int* cb)\n"
        "{\n"
        "    *pb = (void*)p_blocks;\n"
        "    *nb = n_blocks;\n"
        "    *cb = c_blocks;\n"
        "}\n"
        "int box64_memdbg_mapped(unsigned long addr)\n"
        "{\n"
        "    unsigned char v = 0;\n"
        "    extern int mincore(void*, unsigned long, unsigned char*);\n"
        "    if(mincore((void*)(addr & ~0xfffUL), 4096, &v) != 0)\n"
        "        return -1;\n"
        "    return (v & 1) ? 1 : 0;\n"
        "}\n"
    )

    n = src.count(old)
    if n != 1:
        fail(f"锚点命中 {n} 次（期望 1）")
    src = src.replace(old, new)

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print("patch_memdbg_state: 已应用 -> custommem.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_memdbg_state.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
