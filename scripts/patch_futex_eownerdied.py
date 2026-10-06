#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futex-eownerdied：guest FUTEX_WAIT 对已死 robust owner 注入 EOWNER_DIED。

rootcause：宿主 musl 无 robust futex（box64-build 已把 case 273 set_robust_list
伪造成功），guest glibc 的 robust mutex owner 线程消失后，内核永远不会置
FUTEX_OWNER_DIED。多线程 fork 出的子进程（steamcmd update-check fork）等
继承的 robust 锁（锁字 = WAITERS|owner-tid，owner 是父进程已消失线程）会
永久 FUTEX_WAIT 挂起，父进程 wait4 永等 → 永不登录（实测 val=0x80007B1A
tid=31514、0x80002CDE tid=11486、0x80005F29 tid=24361 三轮同款死锁）。

改动（仅 src/emu/x64syscall.c，双路径各插一段，模式一致）：
  - 前 30 次 s==202 打 LOG_NONE 无条件 trace（op/val/uaddr/gettid，定位用）
  - 拦截条件：FUTEX_WAIT(cmd=0) + WAITERS 位 + owner tid>0x1000 且非本线程
    + /proc/self/task/<owner> 不存在（owner 已死，活 owner 不受影响）
  - 动作：按内核 handle_futex_death 语义改写锁字（保留 WAITERS、tid 清零、
    置 FUTEX_OWNER_DIED），返回 -EOWNERDEAD(=内核 EOWNER_DIED 130)，
    令 guest glibc 走 robust 恢复接管路径而非永久等待。
  - x64Syscall_linux（R_EAX=s，args RDI/RSI/RDX）：S_RAX=-EOWNERDEAD，return
  - my_syscall（R_EDI=s，args 起 RSI，即 uaddr=RSI op=RDX val=RCX）：
    errno=EOWNERDEAD，return -1（libc 约定）

用法: patch_futex_eownerdied.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: futex-eownerdied"


def fail(msg):
    print(f"patch_futex_eownerdied: 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def patch(srcdir: str) -> int:
    p = os.path.join(srcdir, "src", "emu", "x64syscall.c")
    if not os.path.isfile(p):
        fail(f"文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        src = f.read()

    if SENTINEL in src:
        print("patch_futex_eownerdied: 已应用过，跳过")
        return 0

    common = (
        "    // BOX64-BUILD: futex-eownerdied — 宿主 musl 无 robust futex（set_robust_list 已伪造成功），\n"
        "    // owner 线程消失后内核不会置 OWNER_DIED，fork 后子进程等继承的 robust 锁会永久挂起\n"
        "    //（实测 steamcmd update-fork 子进程卡 FUTEX_WAIT val=WAITERS|owner-tid 三轮同款）。仅当：\n"
        "    // FUTEX_WAIT(cmd=0) + WAITERS 位 + owner tid 合法且非本线程 + /proc/self/task 无该 tid\n"
        "    //（owner 已死）→ 按内核 handle_futex_death 语义改写锁字并返回 EOWNER_DIED，\n"
        "    // 令 guest glibc 走 robust 恢复接管路径；活 owner / 其他命令一律透传。\n"
        "    if(s == 202) {\n"
        "        static int ftr202 = 0;\n"
        "        if(ftr202 < 30) {\n"
        "            ++ftr202;\n"
        '            printf_log(LOG_NONE, "futex-trace #%d op=0x%lx val=0x%x uaddr=%p gettid=%d // ' + SENTINEL + '\\n",\n'
        "                       ftr202, (unsigned long)R_{OP}, (unsigned)R_{VAL}, (void*)R_{UADDR}, GetTID());\n"
        "        }\n"
        "        if((R_{OP} & 0x7F) == 0 /* FUTEX_WAIT */) {\n"
        "            uint32_t fval = R_{VAL};\n"
        "            if(fval & 0x80000000) {\n"
        "                uint32_t fowner = fval & 0x3FFFFFFF;\n"
        "                if(fowner > 0x1000 && fowner != (uint32_t)GetTID()) {\n"
        "                    char ftask[64];\n"
        "                    snprintf(ftask, sizeof(ftask), \"/proc/self/task/%u\", fowner);\n"
        "                    if(access(ftask, F_OK)) {\n"
        "                        uint32_t* fw = (uint32_t*)R_{UADDR};\n"
        "                        if(fw && *fw == fval)\n"
        "                            *fw = 0x40000000 | (fval & 0x80000000);  // 内核 handle_futex_death：保留 WAITERS、tid 清零、置 OWNER_DIED\n"
        '                        printf_log(LOG_NONE, "futex: owner tid %u 已死, 注入 EOWNER_DIED (val=0x%x uaddr=%p) // ' + SENTINEL + '\\n",\n'
        "                                   fowner, fval, (void*)R_{UADDR});\n"
        + "{RET}"
        + "                    }\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
    )

    def blk(op, val, uaddr, ret):
        return (common.replace("{OP}", op).replace("{VAL}", val)
                      .replace("{UADDR}", uaddr).replace("{RET}", ret))

    # 1) x64Syscall_linux：R_EAX=s, futex 参数 RDI/RSI/RDX → op=RSI, val=RDX, uaddr=RDI
    old1 = (
        "    if (s == 157 && R_EDI == PR_SET_SYSCALL_USER_DISPATCH) {\n"
        "        S_RAX = my_syscall_user_dispatch_prctl(emu, R_RSI, R_RDX, R_R10, (void*)R_R8);\n"
        "        return;\n"
        "    }\n"
        "    // check wrapper first\n"
    )
    new1 = old1 + blk("RSI", "RDX", "RDI", "                            S_RAX = -EOWNERDEAD;  // 即内核 EOWNER_DIED(130)，POSIX 同值\n                            return;\n")
    src = apply(src, old1, new1, 1, "x64Syscall_linux 拦截点", "x64syscall.c")

    # 2) my_syscall：R_EDI=s, futex 参数 RSI/RDX/RCX → uaddr=RSI, op=RDX, val=RCX
    old2 = (
        "    if (s == 157 && S_ESI == PR_SET_SYSCALL_USER_DISPATCH) {\n"
        "        long ret = my_syscall_user_dispatch_prctl(emu, R_RDX, R_RCX, R_R8, (void*)R_R9);\n"
        "        if(ret < 0) {\n"
        "            errno = -ret;\n"
        "            return -1;\n"
        "        }\n"
        "        return 0;\n"
        "    }\n"
        "    // check wrapper first\n"
    )
    new2 = old2 + blk("RDX", "RCX", "RSI", "                            errno = EOWNERDEAD;  // 即内核 EOWNER_DIED(130)，POSIX 同值\n                            return -1;\n")
    src = apply(src, old2, new2, 1, "my_syscall 拦截点", "x64syscall.c")

    with open(p, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"patch_futex_eownerdied: 已应用 -> x64syscall.c")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: patch_futex_eownerdied.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
