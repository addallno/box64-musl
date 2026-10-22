# box64-build

通过 GitHub Actions 交叉编译 **box64** 的 aarch64 静态单文件（支持 box32，即同时模拟 x86_64 与 x86_32 程序）。

## 用途

- 目标平台：**aarch64**（Proot Linux 与 Android 通用），产物为 musl 静态链接单文件
- 启用特性：
  - `ARM_DYNAREC=ON` —— ARM64 动态重编译（必须）
  - `BOX32=ON` —— 同时模拟 32 位 x86 程序（替代 box86，无需单独编 armhf box86）
  - `STATICBUILD=ON` —— 静态链接，单文件分发
  - `BAD_SIGNAL=ON` —— Android 混合内核信号兼容
  - `NOGIT=1` —— 从源码 zip 构建时省略 git SHA
- 硬件：骁龙 652 (MSM8976SG) 8 核 A53，Android 7.1.1，6GB RAM / 512MB swap，磁盘仅剩 ~7.6GB

## 架构

- **CI**：GitHub Actions（`ubuntu-latest`），本地不做任何编译（设备性能红线）
- **工具链**：`cross-tools/musl-cross` release `20260515` 的 `aarch64-unknown-linux-musl.tar.xz`（与 termoneplus-tools 的 dropbear 编译同一工具链源）
- **box64 源码**：`ptitSeb/box64` 官方仓库 git clone（`-DNOGIT=1` 用不上，直接 clone 取最新 main）

## 文件

| 文件 | 说明 |
|------|------|
| `.github/workflows/build.yml` | CI 工作流：拉工具链 → clone box64 → cmake 交叉编译 → strip → 上传 artifact |
| `scripts/build-box64-musl.sh` | 实际编译脚本（workflow 调用） |
| `scripts/patch-musl-isnanf.py` | 源码补丁脚本：修 musl isnanf + 伪造 robust futex 系统调用（编译前注入） |
| `Description.md` | 本文档 |

## 命令

```bash
# 手动触发编译（必须显式触发，默认不自动跑）
gh workflow run build.yml

# ci-param 分支：版本参数化构建（box64_ref + static 输入）
gh workflow run build-box64 --ref ci-param -f box64_ref=v0.2.4 -f static=true
gh run watch <run-id> --exit-status
```

## ci-param 分支：v0.2.4 静态构建（A组，2026-09-25 成功）

- workflow 输入：`box64_ref`（box64 源码 ref）、`static`（STATICBUILD 开关）
- **v0.2.4 + STATICBUILD=ON 构建成功**（run `36134227359`）：产物
  `box64-aarch64-musl-v0.2.4-static/box64-aarch64-musl`，8.3MB，
  `ELF 64-bit LSB executable, ARM aarch64, statically linked, stripped`
- `scripts/patch-musl-isnanf.py` 承载老版本 STATICBUILD 适配补丁集
  （每项带判据，main 版已有等价机制时自动跳过）：
  - `threads.c` musl cancel 最小实现（`__pthread_unwind_buf_t` + 三个强定义 + `__sigsetjmp`→`setjmp`）
  - `debug.h` 注入 STATICBUILD 分支（`box_*`→libc 直用宏，避免 `__libc_*` 声明与 glibc_missing stub 头 `void(void)` 冲突；`box_strdup/box_realpath` 宏化）
  - `CMakeLists.txt`：注入 `add_definitions(-DSTATICBUILD)`（老版本只有 option 无宏定义）；去除 `--whole-archive --allow-multiple-definition`（musl 归档全铺导致 CONDBR19 ±1MB 跨距超限）；STATICBUILD 下 `list(REMOVE_ITEM)` 移除 `globalsymbols.c`（其 `optarg/optind/opterr/optopt` 与 musl getopt.o 重复定义）
  - `elfloader.c`：`startMallocHook` 三段式（#else 空函数）、`checkHookedSymbols` 调用包 `#ifndef`；`main.c`：`endMallocHook`/`init_malloc_hook` 调用包 `#ifndef`（mallochook 裁剪区函数的调用侧兜底）
  - `custommem.c`/`wrappedlibc.c`：`mmap64(`→`mmap(`（v0.2.4 无 custommmap.c，musl off_t 恒 64 位）
  - globalsymbols.c 不编后的引用侧适配：`librarian.c` gdk_display/g_threads 特殊段与声明包 `#ifndef`；9 个 wrapped 文件的 `my_checkGlobalGdkDisplay/my_checkGlobalTInfo/my_setGlobalGThreadsInit` 单行调用包 `#else ((void)0);`（兼容无花括号 if 单行体）；`wrappedlibc.c` 注入 `my_updateGlobalOpt/my_checkGlobalOpt` 空实现
  - `gen-libc-stubs.py`：跳过与源码 `EXPORT` 数据定义同名的 stub（如 `pcre_free` 函数指针 vs 数据声明冲突）
  - `wrappedlib_init.h` symbol2map STATICBUILD resolved=0、`wrappedlibc_private.h` __xmknod GOM 恢复等（详见脚本内 docstring）
- 本地复现 CI patch 环节：
  ```bash
  SKIP_FTS=1 MUSL_SYMS_FILE=<musl-nm输出> python3 scripts/patch-musl-isnanf.py <box64源码> <注入include目录>
  ```
  RET=0 且打印「共替换 N 处」即通过；CI 侧用真实 musl-syms，本地不设该变量时 decls=0 属正常差异。
- 注意：qemu-user 本地跑该产物会因 box64 固定加载地址（0x34800000 段）与 guest_base 冲突而失败，需真机（aarch64）验证运行。

## 产物

`box64-aarch64-musl`（静态单文件，strip 后预计 ~几 MB）。运行示例：

```sh
BOX64_LD_LIBRARY_PATH=./x64lib ./box64-aarch64-musl ./some_x86_64_program
```

## steamcmd get_robust_list 断言修复（patch-musl-isnanf.py）

STATICBUILD box64 下运行 steamcmd 时，steamclient.so 的 robust futex 初始化代码会因宿主
`get_robust_list` 返回 EPERM 而走入断言失败路径（`mov dword[0],0` 主动 SIGSEGV）。
补丁在 `x64syscall.c` 中伪造 273/274 号系统调用：

- **伪造结构**：file-scope `static` 5 字段结构 `rh`，自满足 steamclient 的链表回指校验
  `[*head & ~1 - 8] == head`（`next = &rh+0x20`、`head_ref = &rh`、`futex_offset = -0x20`）；
  `len` 写死 `0x18`（断言硬要求，与 `sizeof(rh)` 无关）。
- **双槽写入**：断言检查帧比 syscall 入口帧高 `0x190`（实测），除 `head_p/len_p` 直接槽外
  还需补写 `+0x190` 槽位。
- **方案A 恢复**：`+0x190` 槽位原值是上层帧 flag/canary，`my274` 写假值前存入 file-scope
  `rb_rest`，同线程下一次进入 `my_syscall` / `x64Syscall_linux` 时恢复，避免 stack_chk 报错。
- 幂等：`patch_x64syscall_c` 共 6 处替换，重复执行返回 0；仅修改 `x64syscall.c`，
  干净源用 `git -C box64 show HEAD:src/emu/x64syscall.c` 导出。

验证（远端 proot 容器内 `~/run-in-proot.sh`）：`Loading Steam API... OK` → 正常登录流程，
无 SIGSEGV / stack_chk。

## 说明

- box64 静态版仅内置少量 wrapped 库（libc/libm/libpthread），图形/音频等 x86 库需通过 `BOX64_LD_LIBRARY_PATH` 指向 x64lib。
- BOX32 模式运行时需把 32 位 x86 库路径放入 `BOX32_LD_LIBRARY_PATH`（从 box64 仓库 `x86lib/` 获取）。

## BOX64_PATHMAP 路径前缀映射

通用 GNU 程序适配：把 guest 程序发出的路径前缀重写为宿主真实前缀（例：guest 写 `/tmp/x` 实际落到别处）。

```sh
# 语法：BOX64_PATHMAP="/from:/to,/from2:/to2"（逗号分隔，最多 8 条）
BOX64_PATHMAP=/tmp:/data/local/tmp ./x86_program
```

- **匹配**：前缀 + 边界（`/tmp` 命中 `/tmp/a` 不命中 `/tmpx`）；多条取最长前缀；每条路径只映射一次。
- **覆盖**：syscall 直调（static 程序、box32 int 0x80）与 libc wrapper（open/stat/execve/fopen/renameat 等 15 个）双层。
- **约束**：勿映射 `/proc`；规则勿链式；env 由子进程继承，无需额外配置。
- 实现见 `scripts/patch_pathmap.py`（13/13 patch 链），BUGS.md「BOX64_PATHMAP 路径前缀映射」章。

## gai 兼容修复（patch_gai.py，B 功能）

无 proot 在 Android（bionic）跑通用 GNU 程序时，guest glibc 语义与宿主
实现的三处差异导致 iputils ping 等程序直接退出。patch_gai.py（第 14 号
patch，哨兵 `BOX64-BUILD: gai-fix`，全部 job 可幂等重放）：

1. **getaddrinfo AI flag 剥离**：GOM(getaddrinfo, iFEpppp) → my_getaddrinfo
   拷贝 hints 剥 `0x40|0x80|0x10000000|0x00200000`（AI_IDN/AI_CANONIDN 及
   IDN 扩展位）。**位值铁证**：ping 反汇编 `movl $0xc2, -0x70(%rbp)` =
   hints.ai_flags=0xC2（CANONNAME 0x2 | IDN 0x40 | CANONIDN 0x80），与
   glibc/bionic netdb.h 一致，但 bionic 运行时不认 0x40/0x80 → EAI_BADFLAGS
   → gai_strerror "Invalid flags" → ping.c:656 返回码 2。
2. **getnameinfo NI_IDN 剥离**：GOM(getnameinfo, **iFpupupui**) →
   my_getnameinfo（无 emu）`flags & 0x1F`（基础位 NUMHOST..DGRAM 保留）。
   ⚠️ CI 模式 `if(NOT CI)` 跳过 rebuild_wrappers.py → **GOM 类型串必须是
   wrapper.h 预生成表已有类型**：iFpupupui ✓（3224 行）、iFEpppp ✓（1984 行）、
   **iFEpupupui ✗**（首版据此 CI 失败 `'iFEpupupui' undeclared`）。
3. **dn_comp**：glibc 2.34 并入 libc 导出，GO(dn_comp, iFppipp) 自动生成转发
   （musl 1.2.5 自带同签名实现），修复 R_X86_64_JUMP_SLOT 符号缺失。

**验证**（2026-10-02，远端 Android + CI run 36970237989 / commit 35c3ea6，
产物 md5 705fdfe191f382cc236456edfd1970ab）：
- `./box64-bin ./ping -c1 127.0.0.1` → rc=0，0% loss（修复前 Invalid flags rc=2）
- `ping -V` → libcap yes, IDN yes
- gai-test-dyn 动态自测：0x2/0x400/0x200000/0x10000000/0x200402/NI_IDN 全 rc=0
- 注意：**静态链接 guest 会绕过 box64 符号包装**，诊断程序必须动态编译

**q57 回归（2026-10-02，通过）**：q57 的 proot 环境完好——tmoe ubuntu-jammy
（rootfs `~/.local/share/tmoe-linux/containers/proot/ubuntu-jammy_arm64`，
steamcmd 在其 `/root/steam/linux32/`，脚本内 `/media/termux/home` 为 proot 内
视角的 Termux home）。**跑 q57 必须启动 proot 实例**（Termux 侧直接跑只有
bionic 环境，不代表 q57 场景）：
- `git diff 984cd05..HEAD` 确认 **wrapped32/ 0 处改动**；CI run 36970237989
  含 BOX32 编译通过。
- 新版 box64-bin（705fdfe）proot 内实测（`~/openwork/box64-work/q57.sh`，timeout 240）：
  **5 轮中 4 轮完整成功**（rc=0，`Waiting for user info...OK` +
  `Unloading Steam API...OK`，与 2026-09-30 基准 q57_tee2.log 一致），1 轮
  间歇 SIGSEGV；另有 1 轮 rc=0 但未到 user info（登录早退模式）。
  注：timeout 65 会卡在 `Logging in user...`（Steam 网络重试），需 ≥240s。
- **对照旧版 box64-old（43edcab，gai 修复前）同法 5 轮**：同样 1 轮 SIGSEGV
  （run1）+ 1 轮早退（run5）+ 3 轮完整成功——**崩溃/早退复现率与新版相当
  → 既有不稳定状态，非 gai 修复引入**。
- 对照补充：gai 修复前产物复现 ping `dn_comp not found` + `Invalid flags`，
  新版 1 received / 0% loss ✓；Termux 侧（bionic 直跑）32 位 busybox/dash
  新旧均 SIGSEGV，属该环境既有状态，与 q57（proot 内）无关。
- 结论：gai 修复对 64 位 ping 有效、对 box32/q57 **无回归**。

## box32 +login 崩溃族修复（2026-10-21，正式 bin cdf72c02）

### 崩溃 A：野 path open SIGSEGV（已防御）
- 链路：guest int80 sys_open → my_open → box64_pathmap/isProcSelf → strncmp 野读崩。
- **patch_open_defense.py**（永久，不受 PROBE 门控）：open 前 mincore 检查 pathname
  指针页，不存在则 EFAULT 返回。每轮命中 1 次，崩溃消失。

### 崩溃 B：map64/map128 池块簿记 prot=0 + 0x7f 段 ERR=5（修复+缓解）
- 根因链（probe17-20 实证）：newBlock 三处（map128/map64/LIST）setting_prot>0 时
  走 defer 单槽分支，mutex_blocks 外裸写 defered_prot_* 与 UNLOCK_PROT flush 并发
  竞态覆盖 → 被覆盖块 memprot 簿记永久丢失（rb_get_end hasend=0）。
- **patch_newblock_defer_fix.py**：三处去 defer 分支无条件 setProtection。
- 对照实证：无探针 bin 无 open-defense 时 3/3 轮 SEGV（崩溃 A+B 连锁）；
  +open-defense 后 8/8 轮 0 崩溃——崩溃 A 疑为崩溃 B 触发器。
- badpage 簿记 prot=0 残留无害（硬件 rwxp，block64maps 实证）；清除通道仍有
  未探明路径（memun 三通道全 0），后续攻坚点。

### 构建开关
- `PROBE=false`（workflow input）：跳过诊断探针（patch_map_prot_probe +
  dynarec_mmap_lock），只含 fix——对照/正式构建用。
- 正式 bin：远端 box64-bin=cdf72c02（CI 39609638227，probe=false）；
  旧 d52e3698 备份 box64-bin.d52e3698-backup3。
- 32 位 +login addallno：8 轮 SEGV=0/ABRT=0（90s timeout，rc=124）；
  64 位无回归（SEGV=0）。

## badpage「第 4 清除通道」调查结论（2026-10-21，probe21-25）

### 结论：清除通道不存在，badpage=块内空闲页无簿记条目（无害）
- probe24 决定性数据：badpage 232-240 条/轮**全部 hasend=0**（memprot rbtree 无条目），
  无任何 data=0 条目存在；四段探针（map64/map128 × hint/mprobe）均 0 条 hasend=1。
- probe25 扩展 wipeprobe 至 0x7f 段（map128 族 0x7f98d9/0x7fa4f4 badpage 所在域）：
  - munset=2/轮，均为 4K 正常 free（caller 0x34815068），**与 badpage 族无关**
  - memset data=0：**0 条**（0x7f 段从无清除类写入）
  - 0x100000000 族 memset=28/轮，全部 data=3/7（正常 setProtection 链），覆盖 0x100024000+
    已用段；badpage 段（0x100000000-0x10001f000）恰在未写区间——空闲页无条目=自洽。
- refreshProtection 对无条目页 rb_get_end 失败→**跳过 mprotect**（不会 PROT_NONE 上膛）；
  refprot-none 探针全程 0 命中；vma=1 硬件保持原 prot；SEGV 持续 0（正式 bin cdf72c02 8/8 +
  probe24/25 多轮）。

### 附带排除
- 崩溃 B 的 newBlock defer 竞态（已修）与"簿记被清"无关：那是创建时未写入的另一机制。
- 0x10718x 族当年"写入成功后消失"（setp64chk hasend=1→后读 0）在 probe24/25 未复现
  （badpage 地址族仅 0x100000000 与 0x7f98d9/0x7fa4f4）——疑为 fix 后环境变化或当年同为
  无条目误报，留档待考。

### 理论残留风险（未观测到）
- 无条目页若未来被大区间 rb_set 覆盖 data=0 且随后 refreshProtection→mprotect PROT_NONE
  →ERR=5（崩溃 B 硬件形态）。当前 wipeprobe 全量覆盖 0x100 段+0x7f 清除类，持续可观测。

### 探针基建（新增）
- patch_memprot_wipe_probe.py：rbtree 层 memprot 写追踪（rb_set_64/rb_unset caller+区间
  +data；0x100000000-0x101000000 全量 + 0x7f 段 unset 全量/data=0）+ refreshProtection
  PROT_NONE 上膛探针 [refprot-none]。
- badpage 四段探针加 hasend 字段（rb_get_end 区分无条目 vs data=0）；已修 mprobe 两段
  参数错位 bug、pr_end 类型 uintptr_t。
- probe 系 bin 链：probe21=d7600e6a / probe22=f808d8e2 / probe23=5d03c7d0 /
  probe24=3e5c483d / probe25=a6dd03a0。
