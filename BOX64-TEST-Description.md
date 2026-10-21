# box64-test：Termux 上 box64-bin 跑 x86_64 steamcmd

## 目标
`box64-bin ./steamcmd +login addallno +quit` 联网登录跑通（musl box64，非 glibc）。

## 现状（bin cdf72c02，2026-10-21；上一里程碑 d52e3698，基线 034a064b）
- **主线达成**：72+ 轮测试 37-24/40 全 OK（缓存凭证登录→user info→Unloading→退出），0 崩溃
- **崩溃 A/B 双修复后**：32 位 +login addallno 8/8 轮 SEGV=0/ABRT=0（90s timeout rc=124 属正常）；64 位无回归
- 残余：网络慢致 Steam Public 登录偶超 45-60s（非 box64 bug）；/data 99% 满致 breakpad Fatal（清 `$TMPDIR/dumps*` 恢复）
- 备份 bin：`bin/box64-bin.d52e3698-backup3`（B-14 里程碑）、`bin/box64-bin.8f441255-backup2`、`bin/box64-bin.034a064b-backup2`（基线）

## 运行命令
```bash
export BOX64_PATHMAP=/tmp:$TMPDIR,/etc:$TMPDIR/fakecert/etc,/usr/lib/ssl:$TMPDIR/fakecert/usr/lib/ssl
export BOX64_LD_LIBRARY_PATH=$HOME/x86libs/lib/x86_64-linux-gnu:$HOME/x86libs/usr/lib/x86_64-linux-gnu
export BOX64_LOG=1 BOX64_SHOWSEGV=1
# 可选：BOX64_MUTEX_DEBUG=1 开 md-enter 诊断（默认静默）
cd $HOME/Ubuntu/root/steam/linux64
timeout -k 5 60 $HOME/openwork/box64-test/bin/box64-bin ./steamcmd +login addallno +quit
```
每轮后：`rm -rf $TMPDIR/dumps*; pkill -f '[b]ox64[-]bin'; pkill -f '[s]teamcmd'`

## 关键坑（血泪）
1. **GOM 首参必须 x64emu_t* emu**：`GOM(name, vFEp)` 的 wrapper 以 `(emu,res)` 调实现；实现漏 emu → emu 被当 res → 必崩。对照 my_getaddrinfo/my___sched_cpualloc 均带 emu。
2. **musl aibuf 池 freeaddrinfo**：musl freeaddrinfo 假定链来自其池（+0x50 short 反查块头加锁 refcount）；box64_dns_fallback 的 calloc 裸链被 guest free 时野指针崩 → 修复=魔数 0x80000000 标记 + my_freeaddrinfo 自释放。
3. **patch 链序依赖**：gai_free_compat 依赖 patch_gai 先打（fallback/my_getaddrinfo/gai_native_broken 全是 patch_gai 注入非上游源码）；宏 BOX64_GAI_FB_FLAG 必须在 fallback 使用点之前。
4. **Termux 环境**：默认 shell 是 Termux（uid 10207），bash -s heredoc 必须；无 /tmp 用 $TMPDIR；无 strace/gdb/core；dmesg 不可读。
5. **artifact 下载慢**：gh api zip 常截断，用 curl -C - 断点续传循环直到 unzip -t 通过。

## patch 链（scripts/build-box64-musl.sh）
...→ dlclose_log → malloc_lock_fork → futex_eownerdied → mutex_deadowner_wrap → atfork_on_clone → getdb_probe → dlclose_guard → nativelr → sched_cpualloc → **gai_free_compat** → gen-libc-stubs

## 已关闭的崩溃族（gai_free_compat + emu 修复后 72+ 轮 0 复现）
- 型A dlclose DelMmaplist 野 db（dlclose-guard）
- 型A cmpxchg16b Treiber 栈 steamclient+0x270dcb7
- 型B freeaddrinfo musl 池反查（本轮根因+修复）
- malloc 家族（get_meta/__libc_free/malloc_usable_size）
- pthread_cond_timedwait 空指针
- __sched_cpualloc PLT NULL（patch_sched_cpualloc）
- __malloc_lock fork 死锁（atfork hook）

## box32 32 位路径攻坚记录（2026-10-17，probe bin）

### 已修复（三连击，全在 scripts/ patch 链）
1. **启动 SIGSEGV**（mmap_guard，67b29e6）：32 位进程 box64_is32bits=1 → custommem 新建块三目走 box32_dynarec_mmap → rbtree_init 首次分配早于 mapallmem 创建 → rb_get_end(NULL) 解引用。修复=box32_dynarec_mmap 头部 `if(!mapallmem)` fallback InternalMmap。
2. **pathmask32 缺 i386 stat64 系**（857dc56）：表只有 106/107/299，补 {195 stat64},{196 lstat64},{300 fstatat64}（syscall 层，对 breakpad case 非主路径但正确性修复）。
3. **wrapped32 无 pathmap**（pathmap2 _patch32 段，8f4e864+a438077）：32 位 guest 用独立 wrapped32/my32_* 集，原 pathmap2 只打 64 位 wrapped/。补：my32___xstat/lxstat/lxstat64/fxstatat64/stat 体插 box64_pathmap（宽容锚=签名+`{`）；新增 my32_mkdir/access/mkdirat；private.h GOW→GOM(iEE* 形态)；iEEpu trampoline 注入预生成 wrapper32.c/h（CI 跳过 rebuild_wrappers_32）；wrapped32/wrappedlibc.c 注入 `#include "pathmap.h"`（a438077，否则隐式 int 声明把64 位返回指针截断→mkdir EFAULT=14）。

### 验证状态（bin 56f4b587，CI 39050015590）
- 32 位 steamcmd：breakpad Fatal 消失（mkdir("/tmp/dumps")→映射 $TMPDIR/dumps r=0）、跑到 "Loading Steam API..."（历史最远）。
- 64 位 5 轮回归：0 崩溃；登录流程完整（rc=5 仅 Steam Rate Limit，环境因素）。
- **残留（下一阶段）**：32 位 steamclient.so SIGSEGV（@steamclient+0x18559f0 访 0x7f7a943xxx prot=0；后继 box64/free+0x13 同区域）——疑似 host/guest 指针混用层。
- `threadtools.cpp (2526) Permission denied` 断言 **64 位同样出现**=环境性（Termux sched/prctl 权限），非 box32 bug。

### 32 位运行命令
```
export BOX64_PATHMAP=/tmp:$TMPDIR,/etc:$TMPDIR/fakecert/etc,/usr/lib/ssl:$TMPDIR/fakecert/usr/lib/ssl
export BOX64_LOG=1 BOX64_SHOWSEGV=1
cd $HOME/Ubuntu/root/steam/linux32
timeout -k 5 90 ~/openwork/box64-test/bin/box64-bin.probe ./steamcmd +quit
# wrapped 调用 trace：BOX64_LOG=3；freopen64 后日志重定向到 $HOME/Steam/logs/stderr.txt
```

## box32 malloc/free 混用崩溃修复（2026-10-17 晚，bin d52e3698）
- **现象**：32 位 steamclient.so 两连 SIGSEGV：@steamclient+0x18559f0 访 0x7f9804a898(prot=0) → 崩后 native 路径 box64/free+0x13、box64/malloc+0x13（x0=0x7f…90c0+0x2058，musl mallocng arena meta 形态）。
- **第一现场**（BOX64_LOG=3 线程 0912）：malloc(0x21)→0xFF53AC70 → free(0xFF53AC70) 时 SIGSEGV。
- **根因**：32 位进程 `reverveHigMem32()` 把地址空间限 4G → 宿主 brk heap 落低 4G（0xFF 段）→ box32_malloc 的 `FITS_IN_32BIT(ret,s)` 短路把宿主块直接交给 guest → 崩时 free 却走到 box64/free native 路径 → 访问 musl mallocng 高区 arena meta（0x7f…，prot=0，被 reserve/dynarec 波及）→ SIGSEGV。
- **修复**（commit 4e0f55b，patch_b14_box32_alloc.py 新增 mallochook.c 段）：box32_malloc/calloc/memalign 去掉 FITS 短路，一律 customMalloc32/customCalloc32/customMemAligned32（低 4G 受控块，与 box32_free 的 isCustomAddr 路径配对）。realloc/free/usable_size 保持原逻辑。
- **验证**（bin d52e3698）：32 位 SIGSEGV 全文 0 条、Fatal=0；64 位 2 轮 rc=0 干净。已转正 box64-bin（备份 box64-bin.8f441255-backup2）。
- **残留（下一攻坚）**：32 位跑到 "Loading Steam API..." 后挂起 rc=124（150s 轮 stdout 只到 Logging directory 即停），卡点待 LOG=3 定位（疑网络 update check / mutex）。

## box32 崩溃 A/B 修复（2026-10-21，正式 bin cdf72c02，CI 39609638227）
### 崩溃 A：野 path open SIGSEGV（已防御）
- 链路：guest int80 sys_open → my_open → pathmap/isProcSelf → strncmp 野读崩（lr=crashhandler+0x80a63 形态）。
- **patch_open_defense.py**（永久补丁，不受 PROBE 门控）：open 前 mincore 检查 pathname 指针页，不存在 EFAULT 返回。每轮命中 1 次即防住。
### 崩溃 B：池块簿记 prot=0 + 0x7f 段 ERR=5（defer fix + open-defense 联合消除）
- 根因：newBlock 三处（map128 763/map64 891/LIST 1036）setting_prot>0 走 defer 单槽分支，mutex_blocks 外裸写 defered_prot_* 与 UNLOCK_PROT flush 并发竞态覆盖 → 簿记永久丢失（rb_get_end hasend=0）。**patch_newblock_defer_fix.py** 三处改无条件 setProtection。
- 对照实证：PROBE=false 无 open-defense（bin bfee8f35）3/3 轮 SEGV（A+B 连锁）；+open-defense（cdf72c02）8/8 轮 0 崩溃——**崩溃 A 疑为崩溃 B 触发器**。
- 残留：badpage 簿记 prot=0（硬件 rwxp 无害）清除通道仍未探明（memun 三通道全 0），下一攻坚。
### 构建开关
- workflow input `probe`（默认 true）→ env `PROBE`：false 跳过 patch_map_prot_probe + dynarec_mmap_lock 自检（fix/open-defense 恒执行）。正式构建用 `gh workflow run build-box64 -F probe=false -F debug=false -F nopatch=false`。
- 远端 probe bin 全链 probe3..20/fix1/fixonly/fix2 保留；正式 `bin/box64-bin`=cdf72c02。
