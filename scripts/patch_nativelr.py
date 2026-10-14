#!/usr/bin/env python3
# 目的：SIGSEGV 处理入口 dump native aarch64 regs（pc/lr/x0-x7），
# 用于定位 native 侧崩溃 caller（如 musl __lock 的传参来源）。
# 崩溃历史：x64pc 恒为 guest 库函数入口+0x13，native pc 落在 __lock
# 附近，x0=lock 指针为无效 guest/堆地址（0x7f8ebf5144 类），
# 缺 LR 无法一发锁定 __lock 的调用者。
# 锚点：signals.c my_box64signalhandler 崩溃打印 printf 后插入。
# 幂等：SENTINEL 存在即跳过。
# 用法：python3 patch_nativelr.py <box64源码根目录>

import sys
import os

SENTINEL = "BOX64-BUILD: nativelr"
TARGET_REL = "src/libtools/signals.c"
ANCHOR = '            (void*)my_context->signals[sig]);\n'

INSERT = (
    '        // ' + SENTINEL + '：native aarch64 regs（gregs: x0-x30,sp,pc,pstate）\n'
    '        printf_log(LOG_INFO, "%04d|native-crash pc=%p lr=%p x0=%p x1=%p x2=%p x3=%p x4=%p x5=%p x6=%p x7=%p // ' + SENTINEL + '\\n",\n'
    '            GetTID(),\n'
    '            (void*)p->uc_mcontext.gregs[32], (void*)p->uc_mcontext.gregs[30],\n'
    '            (void*)p->uc_mcontext.gregs[0], (void*)p->uc_mcontext.gregs[1],\n'
    '            (void*)p->uc_mcontext.gregs[2], (void*)p->uc_mcontext.gregs[3],\n'
    '            (void*)p->uc_mcontext.gregs[4], (void*)p->uc_mcontext.gregs[5],\n'
    '            (void*)p->uc_mcontext.gregs[6], (void*)p->uc_mcontext.gregs[7]);\n'
)


def main():
    if len(sys.argv) < 2:
        print("用法: patch_nativelr.py <box64源码根目录>", file=sys.stderr)
        sys.exit(1)
    root = os.path.abspath(sys.argv[1])
    path = os.path.join(root, TARGET_REL)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print(f"patch_nativelr: 已应用过，跳过 ({path})")
        return
    n = src.count(ANCHOR)
    if n != 1:
        print(f"patch_nativelr: 锚点命中 {n} 次（期望 1）: {ANCHOR!r}", file=sys.stderr)
        sys.exit(2)
    src = src.replace(ANCHOR, ANCHOR + INSERT, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"patch_nativelr: 已应用 -> {path}")


if __name__ == "__main__":
    main()
