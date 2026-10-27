#!/usr/bin/env python3
"""补 32 位 raw syscall 缺口（sysgap32，64 位 sysgap 的 i386 对应版）：

1. x86syscall_32.c（BOX32 主力）：
   - syscallwrap[] 表补 xattr 族 12 项（i386 226-237，host aarch64 有
     __NR_setxattr 族，可直翻；两函数共用表，一处生效）
   - x86Syscall switch 补 case 92/9/16/182/198/212/193
     （truncate/link/lchown/chown/lchown32/chown32/truncate64 —— aarch64
     host 无对应 __NR，上游 raw switch 全缺，静态 32 位程序直接 ENOSYS）
   - my32_syscall switch 补同批 case（栈参数风格）
2. x86syscall.c（非 BOX32 遗留）：同补表+x86Syscall 同款 case

幂等：文件内含哨兵 BOX64-BUILD: sysgap32 则整文件跳过。
层序：layer1（printf_log 锚）在查表/switch 前已重写路径指针，
case 读到的是映射后的路径。
"""
import os
import sys

SENTINEL = "BOX64-BUILD: sysgap32"


def fail(msg):
    print(f"[patch_sysgap32] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply_file(path, jobs):
    if not os.path.isfile(path):
        fail(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print(f"[patch_sysgap32] {os.path.basename(path)} 已应用过，跳过")
        return
    for anchor, repl in jobs:
        n = src.count(anchor)
        if n != 1:
            fail(f"{os.path.basename(path)} 锚点命中 {n} 次（期望 1）:\n{anchor[:150]}")
        src = src.replace(anchor, repl, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[patch_sysgap32] {os.path.basename(path)} 应用 {len(jobs)} 处")


# xattr 族 12 项（i386 权威号，连续 226-237；f* 变体 arg0=fd 无路径）
XATTR_TABLE = (
    "    #ifdef __NR_setxattr\n"
    "    { 226, __NR_setxattr, 5 },    // " + SENTINEL + " xattr\n"
    "    { 227, __NR_lsetxattr, 5 },\n"
    "    { 228, __NR_fsetxattr, 5 },\n"
    "    { 229, __NR_getxattr, 4 },\n"
    "    { 230, __NR_lgetxattr, 4 },\n"
    "    { 231, __NR_fgetxattr, 4 },\n"
    "    { 232, __NR_listxattr, 3 },\n"
    "    { 233, __NR_llistxattr, 3 },\n"
    "    { 234, __NR_flistxattr, 3 },\n"
    "    { 235, __NR_removexattr, 2 },\n"
    "    { 236, __NR_lremovexattr, 2 },\n"
    "    { 237, __NR_fremovexattr, 2 },\n"
    "    #endif\n"
)

# x86Syscall 风格：R_EAX 赋值 + errno 转换 + break，参数 R_EBX/R_ECX/R_EDX
CASE_X86 = (
    "        case 92: // " + SENTINEL + " sys_truncate(path, len)\n"
    "            R_EAX = truncate((void*)R_EBX, (off_t)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 9: // " + SENTINEL + " sys_link(old, new)\n"
    "            R_EAX = link((void*)R_EBX, (void*)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 16: // " + SENTINEL + " sys_lchown(path, uid, gid) 老号\n"
    "        case 198: // sys_lchown32\n"
    "            R_EAX = lchown((void*)R_EBX, (uid_t)R_ECX, (gid_t)R_EDX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 182: // " + SENTINEL + " sys_chown(path, uid, gid) 现行\n"
    "        case 212: // sys_chown32\n"
    "            R_EAX = chown((void*)R_EBX, (uid_t)R_ECX, (gid_t)R_EDX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 193: // " + SENTINEL + " sys_truncate64(path, loff) ECX=低32 EDX=高32\n"
    "            R_EAX = truncate((void*)R_EBX,\n"
    "                (off_t)(((uint64_t)R_EDX << 32) | (uint32_t)R_ECX));\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 10: // " + SENTINEL + " sys_unlink(path) aarch64 无 __NR_unlink\n"
    "            R_EAX = unlink((void*)R_EBX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 15: // " + SENTINEL + " sys_chmod(path, mode) musl 无 __NR_chmod 走 libc\n"
    "            R_EAX = chmod((void*)R_EBX, (mode_t)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 33: // sys_access(path, mode)\n"
    "            R_EAX = access((void*)R_EBX, (int)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 38: // sys_rename(old, new)\n"
    "            R_EAX = rename((void*)R_EBX, (void*)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 39: // sys_mkdir(path, mode)\n"
    "            R_EAX = mkdir((void*)R_EBX, (mode_t)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 40: // sys_rmdir(path)\n"
    "            R_EAX = rmdir((void*)R_EBX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 83: // sys_symlink(target, linkpath)\n"
    "            R_EAX = symlink((void*)R_EBX, (void*)R_ECX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
    "        case 85: // sys_readlink(path, buf, size)\n"
    "            R_EAX = readlink((void*)R_EBX, (void*)R_ECX, (size_t)R_EDX);\n"
    "            if(R_EAX==0xffffffff && errno>0)\n"
    "                R_EAX = (uint32_t)-errno;\n"
    "            break;\n"
)

# x86Syscall 的 default 锚（含 "Syscall 32"，区别 64 位 x64syscall.c）
ANCHOR_X86_DEFAULT = (
    "        default:\n"
    '            printf_log(LOG_INFO, "Warning: Unsupported Syscall 32 0x%02Xh (%d)\\n", s, s);'
)

# my32_syscall 风格：return (uint32_t)f(...)，栈参数 p(0)/u32(4)/u32(8)
CASE_MY32 = (
    "        case 92: // " + SENTINEL + " sys_truncate\n"
    "            return (uint32_t)truncate(p(0), (off_t)u32(4));\n"
    "        case 9: // " + SENTINEL + " sys_link\n"
    "            return (uint32_t)link(p(0), p(4));\n"
    "        case 16: // " + SENTINEL + " sys_lchown 老号\n"
    "        case 198: // sys_lchown32\n"
    "            return (uint32_t)lchown(p(0), (uid_t)u32(4), (gid_t)u32(8));\n"
    "        case 182: // " + SENTINEL + " sys_chown 现行\n"
    "        case 212: // sys_chown32\n"
    "            return (uint32_t)chown(p(0), (uid_t)u32(4), (gid_t)u32(8));\n"
    "        case 193: // " + SENTINEL + " sys_truncate64 栈 arg1 低=4 高=8\n"
    "            return (uint32_t)truncate(p(0),\n"
    "                (off_t)(((uint64_t)u32(8) << 32) | u32(4)));\n"
    "        case 10: // " + SENTINEL + " sys_unlink\n"
    "            return (uint32_t)unlink(p(0));\n"
    "        case 15: // sys_chmod musl 无 __NR_chmod 走 libc\n"
    "            return (uint32_t)chmod(p(0), (mode_t)u32(4));\n"
    "        case 33: // sys_access\n"
    "            return (uint32_t)access(p(0), (int)u32(4));\n"
    "        case 38: // sys_rename\n"
    "            return (uint32_t)rename(p(0), p(4));\n"
    "        case 39: // sys_mkdir\n"
    "            return (uint32_t)mkdir(p(0), (mode_t)u32(4));\n"
    "        case 40: // sys_rmdir\n"
    "            return (uint32_t)rmdir(p(0));\n"
    "        case 83: // sys_symlink\n"
    "            return (uint32_t)symlink(p(0), p(4));\n"
    "        case 85: // sys_readlink\n"
    "            return (uint32_t)readlink(p(0), p(4), (size_t)u32(8));\n"
)
ANCHOR_MY32_DEFAULT = (
    "        default:\n"
    "            if((s>>6)<sizeof(warned)/sizeof(warned[0])) {"
)

# x86syscall.c（非 BOX32）default 锚：无 "32" 字样
ANCHOR_X86C_DEFAULT = (
    "        default:\n"
    '            printf_log(LOG_INFO, "Warning: Unsupported Syscall 0x%02Xh (%d)\\n", s, s);'
)


def main():
    if len(sys.argv) != 2:
        fail("用法: patch_sysgap32.py <box64目录>")
    root = sys.argv[1]
    emu = os.path.join(root, "src", "emu")

    # BOX32 主力：表 + 两函数 default
    apply_file(os.path.join(emu, "x86syscall_32.c"), [
        ("    { 270, __NR_tgkill, 3 },",
         "    { 270, __NR_tgkill, 3 },\n" + XATTR_TABLE.rstrip("\n")),
        (ANCHOR_X86_DEFAULT, CASE_X86 + ANCHOR_X86_DEFAULT),
        (ANCHOR_MY32_DEFAULT, CASE_MY32 + ANCHOR_MY32_DEFAULT),
    ])

    # 非 BOX32 遗留：表 + default（case 同 x86Syscall 风格）
    apply_file(os.path.join(emu, "x86syscall.c"), [
        ("    { 224, __NR_gettid, 0 },",
         "    { 224, __NR_gettid, 0 },\n" + XATTR_TABLE.rstrip("\n")),
        (ANCHOR_X86C_DEFAULT, CASE_X86 + ANCHOR_X86C_DEFAULT),
    ])
    print("[patch_sysgap32] 完成")


if __name__ == "__main__":
    main()
