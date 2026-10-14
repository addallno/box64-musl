#!/usr/bin/env python3
# 目的：修型B崩溃（getaddrinfo+0x13 fork child SIGSEGV @__lock）。
# 根因：musl 的 freeaddrinfo 是 aibuf 池版（addrinfo+0x50 short 反查
# 块头、对块头+0x4c 加锁 refcount），而 box64_dns_fallback 用 calloc
# 组装裸 addrinfo 链返回 guest；guest 之后 freeaddrinfo 该链时 musl
# 按池协议反查 → 野指针 __lock → SIGSEGV（si_addr 为无映射堆地址）。
# 历史：nativelr 取证 pc=__lock lr=freeaddrinfo+0x34（轮93）。
# 修复：GOM hook freeaddrinfo，fallback 链节点打 ai_flags 高位魔数
# 0x80000000；hook 命中魔数则逐节点 free，否则转发 native freeaddrinfo。
# 用法：python3 patch_gai_free_compat.py <box64源码根目录>

import sys
import os

SENTINEL = "BOX64-BUILD: gai-free-compat"
TARGET_C = "src/wrapped/wrappedlibc.c"
TARGET_H = "src/wrapped/wrappedlibc_private.h"

H_OLD = "GO(freeaddrinfo, vFp)\n"
H_NEW = "GOM(freeaddrinfo, vFEp) // " + SENTINEL + "\n"

# 宏定义须在 box64_dns_fallback 之前（fallback 内 ai_flags 使用点在前）
C_ANCHOR_FB = "// native 不可用时的解析组链（calloc 组装，与 native freeaddrinfo 的 musl free 配对）\n"
C_INSERT_MACRO = (
    "// " + SENTINEL + "：fallback 链魔数（高位标记，freeaddrinfo hook 识别）\n"
    "#define BOX64_GAI_FB_FLAG 0x80000000\n"
)

C_ANCHOR_DECL = "static int box64_gai_native_broken = 0;\n"
C_INSERT_DECL = (
    "// " + SENTINEL + "：freeaddrinfo hook（musl 池版反查兼容）\n"
    "EXPORT void my_freeaddrinfo(struct addrinfo* res)\n"
    "{\n"
    "    // musl freeaddrinfo 假定链来自其 aibuf 池（+0x50 short 反查块头\n"
    "    // 加锁 refcount）；fallback 的 calloc 裸链命中高位魔数时自释放\n"
    "    if (res && (res->ai_flags & BOX64_GAI_FB_FLAG)) {\n"
    "        struct addrinfo* p = res;\n"
    "        while (p) {\n"
    "            struct addrinfo* n = p->ai_next;\n"
    "            free(p->ai_addr);\n"
    "            free(p);\n"
    "            p = n;\n"
    "        }\n"
    "        return;\n"
    "    }\n"
    "    freeaddrinfo(res);\n"
    "}\n"
)

C_ANCHOR_FLAGS = "        ai->ai_flags = oflags;\n"
C_REPLACE_FLAGS = (
    "        ai->ai_flags = oflags | BOX64_GAI_FB_FLAG; // " + SENTINEL + "\n"
)

# oom 路径的 freeaddrinfo(head) 是 native 内部直调 musl 池版（不经 GOM
# hook），对 fallback 裸链同样野指针——改手动释放
C_ANCHOR_OOM = (
    "oom:\n"
    "    if (head)\n"
    "        freeaddrinfo(head);\n"
    "    return EAI_MEMORY;\n"
)
C_REPLACE_OOM = (
    "oom:\n"
    "    // " + SENTINEL + "：裸链不能走 musl 池版 freeaddrinfo，手动释放\n"
    "    if (head) {\n"
    "        struct addrinfo* p = head;\n"
    "        while (p) {\n"
    "            struct addrinfo* n = p->ai_next;\n"
    "            free(p->ai_addr);\n"
    "            free(p);\n"
    "            p = n;\n"
    "        }\n"
    "    }\n"
    "    return EAI_MEMORY;\n"
)


def replace_once(src, anchor, new, name):
    n = src.count(anchor)
    if n != 1:
        print(f"patch_gai_free_compat: {name} 锚点命中 {n} 次（期望 1）: {anchor!r}",
              file=sys.stderr)
        sys.exit(2)
    return src.replace(anchor, new, 1)


def main():
    if len(sys.argv) < 2:
        print("用法: patch_gai_free_compat.py <box64源码根目录>", file=sys.stderr)
        sys.exit(1)
    root = os.path.abspath(sys.argv[1])

    hpath = os.path.join(root, TARGET_H)
    with open(hpath, "r", encoding="utf-8") as f:
        hsrc = f.read()
    if SENTINEL in hsrc:
        print(f"patch_gai_free_compat: priv.h 已应用过，跳过 ({hpath})")
    else:
        hsrc = replace_once(hsrc, H_OLD, H_NEW, "priv.h GO(freeaddrinfo)")
        with open(hpath, "w", encoding="utf-8") as f:
            f.write(hsrc)
        print(f"patch_gai_free_compat: priv.h 已应用 -> {hpath}")

    cpath = os.path.join(root, TARGET_C)
    with open(cpath, "r", encoding="utf-8") as f:
        csrc = f.read()
    if SENTINEL in csrc and "my_freeaddrinfo" in csrc:
        print(f"patch_gai_free_compat: wrappedlibc.c 已应用过，跳过 ({cpath})")
        return
    csrc = replace_once(csrc, C_ANCHOR_FB, C_INSERT_MACRO + C_ANCHOR_FB,
                        "wrappedlibc.c fallback宏")
    csrc = replace_once(csrc, C_ANCHOR_DECL, C_INSERT_DECL + C_ANCHOR_DECL,
                        "wrappedlibc.c decl")
    csrc = replace_once(csrc, C_ANCHOR_FLAGS, C_REPLACE_FLAGS, "wrappedlibc.c flags")
    csrc = replace_once(csrc, C_ANCHOR_OOM, C_REPLACE_OOM, "wrappedlibc.c oom")
    with open(cpath, "w", encoding="utf-8") as f:
        f.write(csrc)
    print(f"patch_gai_free_compat: wrappedlibc.c 已应用 -> {cpath}")


if __name__ == "__main__":
    main()
