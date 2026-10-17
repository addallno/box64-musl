#!/usr/bin/env python3
"""box32 早期崩溃打点：NewBox64Context/core.c 各步 write(2) 探针，定位 32-bit ELF 启动段错误位置。

仅用于二分定位，定位后可随链保留（无条件输出几行无害）或移除。
"""
import os
import sys

SENTINEL = "BOX64-BUILD: box32-probe"


def fail(msg: str) -> int:
    print(f"patch_box32_probe: 错误: {msg}", file=sys.stderr)
    return 1


def apply_once(src: str, anchor: str, insert: str, name: str) -> str:
    if SENTINEL + ": " + name in src:
        print(f"patch_box32_probe: {name} 已应用过，跳过")
        return src
    if anchor not in src:
        raise SystemExit(f"patch_box32_probe: 锚未命中: {name}")
    # 探针紧跟锚行之后
    idx = src.index(anchor) + len(anchor)
    return src[:idx] + insert + src[idx:]


def probe(tag: str) -> str:
    return (
        f'\n    write(2, "[{SENTINEL}] {tag}\\n", {len(f"[{SENTINEL}] {tag}\\n")});'
    )


def patch(root: str) -> int:
    core = os.path.join(root, "src", "core.c")
    ctx = os.path.join(root, "src", "box64context.c")
    with open(core, "r", encoding="utf-8") as f:
        c = f.read()
    c = apply_once(
        c,
        "box64_is32bits = FileIsX86ELF(prog);\n    #endif",
        probe("T1 after FileIsX86ELF"),
        "T1",
    )
    # T2 锚=整行调用，插在其后 → 表示 NewBox64Context 已返回（没打 T2=崩在其内部）
    c = apply_once(
        c,
        "my_context = NewBox64Context(argc - nextarg);",
        probe("T2 after NewBox64Context"),
        "T2",
    )
    with open(core, "w", encoding="utf-8") as f:
        f.write(c)

    with open(ctx, "r", encoding="utf-8") as f:
        x = f.read()
    if "#include <unistd.h>" not in x:
        x = apply_once(x, "#include <stdlib.h>\n", '#include <unistd.h>\n', "unistd")
    x = apply_once(x, "context->deferredInit = 1;", probe("T3 deferredInit"), "T3")
    x = apply_once(x, "init_custommem_helper(context);", probe("T4 custommem"), "T4")
    x = apply_once(x, "context->maplib = NewLibrarian(context);", probe("T5 maplib"), "T5")
    x = apply_once(x, "context->system = NewBridge();", probe("T6 bridge"), "T6")
    x = apply_once(x, "context->exit_bridge = AddBridge(context->system, NULL, NULL, 0, NULL);", probe("T7 exit_bridge"), "T7")
    x = apply_once(x, "context->dlprivate = NewDLPrivate();", probe("T8 dlprivate"), "T8")
    with open(ctx, "w", encoding="utf-8") as f:
        f.write(x)

    print("patch_box32_probe: 完成（T1,T3-T8）")
    return 0


if __name__ == "__main__":
    sys.exit(patch(sys.argv[1] if len(sys.argv) > 1 else "."))
