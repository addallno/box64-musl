#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atfork-on-clone：guest 内联 raw clone(fork语义) 的 child 端手动执行 box64 atfork handler。

rootcause：steam crashhandler.so 内联 `mov $0x38,%rax; syscall`（nr=56 clone，
glibc fork 式 clone(SIGCHLD)），绕过 fork@plt/my_fork 与 musl fork()。box64
x64syscall case 56 直通宿主 raw clone（clone()/__clone 包装与 syscall(__NR_clone)
两条分支均不跑 atfork 链）—— child 继承父进程全部锁状态却无人执行
atfork_child_box64context（init_mutexes 清 mutex_dyndump、__malloc_lock 清零）/
atfork_child_custommem/atfork_child_dynarec_prot。
后果：child dynarec internalDBGetBlock -> mutex_lock(&my_context->mutex_dyndump)
（offset 392）futex 永等（owner tid 为已消失的父线程），父进程 wait4 永等，
steamcmd 永不登录。实测死锁锁字 [0x2, 0x8000502e, 0x1, 0] 即继承脏态。

改动（4 文件）：
  - box64context.c：新增聚合函数 box64_atfork_child_all()（日志 + 依次调
    本文件 static atfork_child_box64context 与两个 extern wrapper）
  - custommem.c：新增 wrapper box64_atfork_child_custommem
  - libtools/signals.c：新增 wrapper box64_atfork_child_dynarec_prot
  - emu/x64syscall.c：case 56 两处（x64Syscall_linux / my_syscall）所有
    fork 语义（!(flags & CLONE_VM)）child 返回点调用聚合函数；vfork(0x4100)
    含 CLONE_VM 天然排除；32-bit path 不在本补丁范围。

用法: patch_atfork_on_clone.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: atfork-on-clone"


def fail(msg):
    print(f"patch_atfork_on_clone: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch_box64context(srcdir):
    p = os.path.join(srcdir, "src", "box64context.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_atfork_on_clone: box64context.c 已应用过，跳过")
        return
    old = (
        "    #ifdef DYNAREC\n"
        "    // Cancel FillBlock if needed\n"
        "    void CancelBlock64(int need_lock);\n"
        "    CancelBlock64(0);\n"
        "    #endif\n"
        "}\n"
        "\n"
        "int box64_cycle_log_initialized = 0;\n"
    )
    new = (
        "    #ifdef DYNAREC\n"
        "    // Cancel FillBlock if needed\n"
        "    void CancelBlock64(int need_lock);\n"
        "    CancelBlock64(0);\n"
        "    #endif\n"
        "}\n"
        "\n"
        "// " + SENTINEL + " 开始\n"
        "extern void box64_atfork_child_custommem(void);\n"
        "extern void box64_atfork_child_dynarec_prot(void);\n"
        "void box64_atfork_child_all(void)\n"
        "{\n"
        "    // guest（crashhandler 等）内联 raw clone(56) 直通宿主内核 clone，\n"
        "    // 绕过 musl fork() 的 atfork 链——child 继承父线程持有的\n"
        "    // my_context mutex（mutex_dyndump 等）脏锁字后 dynarec\n"
        "    // internalDBGetBlock futex 永等。此处手动执行 box64 关键 atfork\n"
        "    // child handler（内容与 musl 链注册的一致，见各调用点）。\n"
        "    fprintf(stderr, \"[box64] atfork-child: raw clone child, reinit locks\\n\");\n"
        "    atfork_child_box64context();\n"
        "    box64_atfork_child_custommem();\n"
        "    box64_atfork_child_dynarec_prot();\n"
        "}\n"
        "// " + SENTINEL + " 结束\n"
        "\n"
        "int box64_cycle_log_initialized = 0;\n"
    )
    src = apply(src, old, new, 1, "atfork_child 尾部聚合函数", "box64context.c")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    print("patch_atfork_on_clone: 已应用 -> box64context.c")


def patch_custommem(srcdir):
    p = os.path.join(srcdir, "src", "custommem.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_atfork_on_clone: custommem.c 已应用过，跳过")
        return
    old = (
        "static void atfork_child_custommem(void)\n"
        "{\n"
        "    // (re)init mutex if it was lock before the fork\n"
        "    init_mutexes();\n"
        "}\n"
    )
    new = old + (
        "// " + SENTINEL + " 开始\n"
        "void box64_atfork_child_custommem(void)\n"
        "{\n"
        "    atfork_child_custommem();\n"
        "}\n"
        "// " + SENTINEL + " 结束\n"
    )
    src = apply(src, old, new, 1, "atfork_child_custommem 尾部 wrapper", "custommem.c")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    print("patch_atfork_on_clone: 已应用 -> custommem.c")


def patch_signals(srcdir):
    p = os.path.join(srcdir, "src", "libtools", "signals.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_atfork_on_clone: signals.c 已应用过，跳过")
        return
    old = (
        "    #endif\n"
        "}\n"
        "#endif\n"
        "void init_signal_helper(box64context_t* context)\n"
    )
    new = (
        "    #endif\n"
        "}\n"
        "#endif\n"
        "// " + SENTINEL + " 开始\n"
        "void box64_atfork_child_dynarec_prot(void)\n"
        "{\n"
        "#ifdef USE_SIGNAL_MUTEX\n"
        "    atfork_child_dynarec_prot();\n"
        "#endif\n"
        "}\n"
        "// " + SENTINEL + " 结束\n"
        "void init_signal_helper(box64context_t* context)\n"
    )
    src = apply(src, old, new, 1, "atfork_child_dynarec_prot 后 wrapper", "signals.c")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    print("patch_atfork_on_clone: 已应用 -> signals.c")


def patch_x64syscall(srcdir):
    p = os.path.join(srcdir, "src", "emu", "x64syscall.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_atfork_on_clone: x64syscall.c 已应用过，跳过")
        return

    # 1) 文件头 extern 声明（sched.h 后，CLONE_VM 宏已在其中）
    old = "#include <sched.h>\n"
    new = (
        "#include <sched.h>\n"
        "// " + SENTINEL + " 开始\n"
        "void box64_atfork_child_all(void);\n"
        "// " + SENTINEL + " 结束\n"
    )
    src = apply(src, old, new, 1, "include sched.h 后 extern", "x64syscall.c")

    # 2) x64Syscall_linux 的 case 56：所有分支汇合到 break 前统一判断
    #    （flags=R_RDI；vfork 特例 0x4100 含 CLONE_VM 被条件排除）
    old = (
        "                    #ifdef NOALIGN\n"
        "                    S_RAX = syscall(__NR_clone, R_RDI, R_RSI, R_RDX, R_R10, R_R8);\n"
        "                    #else\n"
        "                    S_RAX = syscall(__NR_clone, R_RDI, R_RSI, R_RDX, R_R8, R_R10);    // invert R_R8/R_R10 on Aarch64 and most other\n"
        "                    #endif\n"
        "            }\n"
        "            break;\n"
        "        #ifndef __NR_fork\n"
        "        case 57:\n"
        "            S_RAX = fork();\n"
    )
    new = (
        "                    #ifdef NOALIGN\n"
        "                    S_RAX = syscall(__NR_clone, R_RDI, R_RSI, R_RDX, R_R10, R_R8);\n"
        "                    #else\n"
        "                    S_RAX = syscall(__NR_clone, R_RDI, R_RSI, R_RDX, R_R8, R_R10);    // invert R_R8/R_R10 on Aarch64 and most other\n"
        "                    #endif\n"
        "            }\n"
        "            // " + SENTINEL + " 开始\n"
        "            if(S_RAX==0 && !(R_RDI&CLONE_VM))\n"
        "                box64_atfork_child_all();\n"
        "            // " + SENTINEL + " 结束\n"
        "            break;\n"
        "        #ifndef __NR_fork\n"
        "        case 57:\n"
        "            S_RAX = fork();\n"
    )
    src = apply(src, old, new, 1, "case56(S_RAX) break 前", "x64syscall.c")

    # 3) my_syscall 的 case 56：线程/带栈 fork 式 clone 分支 return ret 前
    #    （flags=R_RSI）
    old = (
        "                    ret = clone(clone_fn_syscall, (void*)((uintptr_t)mystack+1024*1024), flags, args, R_RCX, NULL, R_R8);\n"
        "                return ret;\n"
    )
    new = (
        "                    ret = clone(clone_fn_syscall, (void*)((uintptr_t)mystack+1024*1024), flags, args, R_RCX, NULL, R_R8);\n"
        "                // " + SENTINEL + " 开始\n"
        "                if(ret==0 && !(R_RSI&CLONE_VM))\n"
        "                    box64_atfork_child_all();\n"
        "                // " + SENTINEL + " 结束\n"
        "                return ret;\n"
    )
    src = apply(src, old, new, 1, "case56(my_syscall) return ret 前", "x64syscall.c")

    # 4) my_syscall 的 case 56：无栈 raw syscall 分支（else return syscall…）
    old = (
        "            else\n"
        "                #ifdef NOALIGN\n"
        "                return syscall(__NR_clone, R_RSI, R_RDX, R_RCX, R_R8, R_R9);\n"
        "                #else\n"
        "                return syscall(__NR_clone, R_RSI, R_RDX, R_RCX, R_R9, R_R8);    // invert R_R8/R_R9 on Aarch64 and most other\n"
        "                #endif\n"
        "            break;\n"
    )
    new = (
        "            else {\n"
        "                long ret_ = 0;\n"
        "                #ifdef NOALIGN\n"
        "                ret_ = syscall(__NR_clone, R_RSI, R_RDX, R_RCX, R_R8, R_R9);\n"
        "                #else\n"
        "                ret_ = syscall(__NR_clone, R_RSI, R_RDX, R_RCX, R_R9, R_R8);    // invert R_R8/R_R9 on Aarch64 and most other\n"
        "                #endif\n"
        "                // " + SENTINEL + " 开始\n"
        "                if(ret_==0 && !(R_RSI&CLONE_VM))\n"
        "                    box64_atfork_child_all();\n"
        "                // " + SENTINEL + " 结束\n"
        "                return ret_;\n"
        "            }\n"
        "            break;\n"
    )
    src = apply(src, old, new, 1, "case56(my_syscall) raw syscall 分支", "x64syscall.c")

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    print("patch_atfork_on_clone: 已应用 -> x64syscall.c（4 锚点）")


def patch(srcdir: str) -> int:
    patch_box64context(srcdir)
    patch_custommem(srcdir)
    patch_signals(srcdir)
    patch_x64syscall(srcdir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_atfork_on_clone.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
