#!/usr/bin/env python3
"""补 box64 上游 raw syscall 缺口（sysgap）：

1. x64Syscall_linux 与 my_syscall 两个 switch 各补 case 76/86/92/94
   （truncate/link/chown/lchown —— aarch64 host 无对应 __NR，上游无 case，
   静态/动态 x86_64 程序打到 default 直接 ENOSYS）。
2. syscallwrap[] 转换表补 xattr 族 9 项（上游只映了 listxattr 194-196，
   setxattr/getxattr/removexattr 三支缺失）。

幂等：文件内含哨兵 BOX64-BUILD: sysgap 则整文件跳过。
"""
import os
import sys

SENTINEL = "BOX64-BUILD: sysgap"


def fail(msg):
    print(f"[patch_sysgap] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply_file(path, jobs):
    if not os.path.isfile(path):
        fail(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print(f"[patch_sysgap] {os.path.basename(path)} 已应用过，跳过")
        return
    for anchor, repl in jobs:
        n = src.count(anchor)
        if n != 1:
            fail(f"{os.path.basename(path)} 锚点命中 {n} 次（期望 1）:\n{anchor[:150]}")
        src = src.replace(anchor, repl, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[patch_sysgap] {os.path.basename(path)} 应用 {len(jobs)} 处")


# x64Syscall_linux 风格：S_RAX 赋值 + break，参数自 R_RDI 起
CASE_LINUX = (
    "        case 76: // " + SENTINEL + " sys_truncate(path, len)\n"
    "            S_RAX = truncate((void*)R_RDI, (off_t)R_RSI);\n"
    "            if(S_RAX==-1) S_RAX = -errno;\n"
    "            break;\n"
    "        case 86: // " + SENTINEL + " sys_link(old, new)\n"
    "            S_RAX = link((void*)R_RDI, (void*)R_RSI);\n"
    "            if(S_RAX==-1) S_RAX = -errno;\n"
    "            break;\n"
    "        case 92: // " + SENTINEL + " sys_chown(path, uid, gid)\n"
    "            S_RAX = chown((void*)R_RDI, (uid_t)S_ESI, (gid_t)S_EDX);\n"
    "            if(S_RAX==-1) S_RAX = -errno;\n"
    "            break;\n"
    "        case 94: // " + SENTINEL + " sys_lchown(path, uid, gid)\n"
    "            S_RAX = lchown((void*)R_RDI, (uid_t)S_ESI, (gid_t)S_EDX);\n"
    "            if(S_RAX==-1) S_RAX = -errno;\n"
    "            break;\n"
)
ANCHOR_LINUX = (
    '        default:\n'
    '            printf_log(LOG_INFO, "Warning: Unsupported Syscall 0x%02Xh (%d)\\n", s, s);'
)

# my_syscall 风格：直接 return，参数自 R_RSI 起（arg0=R_RSI, arg1=R_RDX, arg2=R_RCX）
CASE_MYSYS = (
    "        case 76: // " + SENTINEL + " sys_truncate\n"
    "            return truncate((void*)R_RSI, (off_t)R_RDX);\n"
    "        case 86: // " + SENTINEL + " sys_link\n"
    "            return link((void*)R_RSI, (void*)R_RDX);\n"
    "        case 92: // " + SENTINEL + " sys_chown\n"
    "            return chown((void*)R_RSI, (uid_t)R_EDX, (gid_t)R_ECX);\n"
    "        case 94: // " + SENTINEL + " sys_lchown\n"
    "            return lchown((void*)R_RSI, (uid_t)R_EDX, (gid_t)R_ECX);\n"
)
ANCHOR_MYSYS = (
    "        default:\n"
    "            if(!(warned&(1<<s))) {"
)

# syscallwrap[] 表：xattr 族 9 项（host nr 直翻，参数计数为 x86_64 参数个数）
XATTR_ENTRIES = (
    "    #ifdef __NR_setxattr\n"
    "    [188] = {__NR_setxattr, 5},   // " + SENTINEL + " xattr\n"
    "    [189] = {__NR_lsetxattr, 5},\n"
    "    [190] = {__NR_fsetxattr, 5},\n"
    "    [191] = {__NR_getxattr, 4},\n"
    "    [192] = {__NR_lgetxattr, 4},\n"
    "    [193] = {__NR_fgetxattr, 4},\n"
    "    [197] = {__NR_removexattr, 2},\n"
    "    [198] = {__NR_lremovexattr, 2},\n"
    "    [199] = {__NR_fremovexattr, 2},\n"
    "    #endif\n"
)
ANCHOR_XATTR = "    [194] = {__NR_listxattr, 3},"


def main():
    if len(sys.argv) != 2:
        fail("用法: patch_sysgap.py <box64目录>")
    root = sys.argv[1]
    target = os.path.join(root, "src", "emu", "x64syscall.c")
    apply_file(target, [
        # x64Syscall_linux default 前插 case 组
        (ANCHOR_LINUX, CASE_LINUX + ANCHOR_LINUX),
        # my_syscall default 前插 return case 组
        (ANCHOR_MYSYS, CASE_MYSYS + ANCHOR_MYSYS),
        # 转换表 listxattr 项前补 xattr 族
        (ANCHOR_XATTR, XATTR_ENTRIES + ANCHOR_XATTR),
    ])
    print("[patch_sysgap] 完成")


if __name__ == "__main__":
    main()
