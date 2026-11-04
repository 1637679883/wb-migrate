# WorkBuddy 本地数据格式

这份文档记录 `wb-migrate` 所依赖的 WorkBuddy 本地数据结构。内容都是在真实机器上核对过的（会话逐行可解析、路径编码 26/26 命中），不是猜测。

WorkBuddy 更新后如果改了布局，`wb-migrate doctor` 的「路径编码自检」会第一个报警：命中数掉下来就说明规则变了。

## 目录布局

数据根目录在 `~/.workbuddy/`，Windows 上是 `C:\Users\<用户名>\.workbuddy\`。可用环境变量 `WORKBUDDY_HOME` 覆盖（工具测试时用它指向沙盒）。

```
~/.workbuddy/
  workbuddy.db                        SQLite，会话元数据
  projects/<编码路径>/<会话ID>.jsonl     对话正文，一行一条记录
  projects/<编码路径>/<会话ID>.meta.json
  projects/<编码路径>/<会话ID>.file-rollback.ndjson
  file-history/<会话ID>/               文件快照（撤销用）
  changes-index/<会话ID>.json          变更索引
  changes-detail/<会话ID>/             变更明细
  artifact-index/<会话ID>.json         产物索引
  file-tree-manifests/<会话ID>.json    文件树清单
  tasks/<会话ID>/                      任务清单
  workspace/sessions/<会话ID>/         会话内文件修改备份
  sessions/<pid>.json                  运行中的会话心跳
  <用户ID>/sidebar-list-snapshot.json  侧边栏列表缓存
```

工作空间目录不在数据根目录下，而是用户的普通工作目录，默认 `~/WorkBuddy/<会话目录>/`，里面包含生成的文件和 `.workbuddy/memory/`。

## 数据库

`sessions` 表存会话元数据，工具实际用到这些列：

- `id` —— 会话 UUID，也是文件名
- `cwd` —— 工作空间绝对路径
- `title` —— 标题（另有 `custom_title` 是用户改过的）
- `status`、`created_at`、`updated_at`、`last_activity_at`
- `deleted_at` —— 非空表示在回收站
- `transport` —— `local` 或 `cloud`，云端会话本地没有正文
- `model`、`user_id`、`project_id` 等其余字段

`workspaces` 表只有两列：`path` 和 `last_opened_at`。它记录「哪些工作空间目录被试过」，用来让侧边栏把目录列出来，所以导入新会话时除了插 `sessions`，还要往这张表补一条，否则侧边栏可能看不到那个工作空间。

写入策略：只填「两边都存在的列」。旧版本数据库缺列时不会报错，新版本多出来的列也不会被写成空值——它们走自己的默认值。

## 路径编码

`projects/` 下的目录名由工作空间绝对路径编码而来：

盘符小写、去掉冒号、分隔符统一换成 `-`。

```
C:\Users\Akina23\WorkBuddy\2026-09-08-21-49-06
    -> c-Users-Akina23-WorkBuddy-2026-09-08-21-49-06

D:/projects/my-app
    -> d-projects-my-app
```

反斜杠和正斜杠都要先归一，因为数据里两种写法都存在。工具在真实机器上用这条规则逐个核对过所有本地会话，全部命中；`doctor` 里那行「路径编码自检：N 命中 / 0 未命中」就是这个检查。

## 心跳文件

`sessions/<pid>.json` 是运行中会话的心跳：

```json
{
  "pid": 18776,
  "sessionId": "987d2664-...",
  "cwd": "C:\\Users\\Lenovo\\WorkBuddy\\2026-11-01-16-04-06",
  "startedAt": 1793676209691,
  "lastHeartbeat": 1793676209725,
  "kind": "interactive"
}
```

**注意**：`lastHeartbeat` 只在会话启动时写一次，整个运行期间不会刷新。实测应用已经开着九分钟时，心跳还是九分钟前的值。所以不能拿心跳时间判断「应用是否在运行」——`wb-migrate` 改成查系统进程里有没有 `WorkBuddy.exe`（见 `is_app_running()`），心跳只在拿不到进程列表时兜底。

## 迁移包格式

导出产物是普通 zip，内部结构：

```
manifest.json                    清单
README.txt                       给人看的说明
transcript/<会话ID>.jsonl         对话正文
transcript/<会话ID>.meta.json     会话元数据
transcript/<会话ID>.file-rollback.ndjson  回滚记录（若有）
aux/file-history/<会话ID>/...
aux/changes-detail/<会话ID>/...
aux/changes-index/<会话ID>.json
aux/artifact-index/<会话ID>.json
aux/file-tree-manifests/<会话ID>.json
aux/tasks/<会话ID>/...
aux/workspace-sessions/<会话ID>/...
workspace/<会话ID>/...            工作空间文件
```

`manifest.json` 的关键字段：

```json
{
  "format": "workbuddy-migration",
  "format_version": 1,
  "tool_version": "1.0.0",
  "created_at": "2026-11-03T02:53:17+00:00",
  "source": {
    "host": "LAPTOP-XXXX",
    "os": "win32",
    "python": "3.12.0",
    "workbuddy_home": "C:\\Users\\x\\.workbuddy",
    "home": "C:\\Users\\x",
    "user_id": "..."
  },
  "options": { "include_workspace": true, "max_file_mb": 300 },
  "sessions": [
    {
      "id": "...",
      "title": "...",
      "cwd": "C:\\Users\\x\\WorkBuddy\\2026-10-28-02-15-54",
      "message_count": 196,
      "transcript_sha256": "...",
      "transcript_bytes": 651537,
      "db_row": { "...sessions 表原始行..." },
      "aux": [ { "kind": "file-history", "type": "dir", "files": 2, "bytes": 998 } ],
      "workspace": { "included": true, "files": 53, "bytes": 11234, "skipped": [] }
    }
  ]
}
```

`db_row` 保存导出时 `sessions` 表的整行，导入时就地取字段；`transcript_sha256` 用于导入前的完整性校验；`workspace.skipped` 记下因为超过体积上限而没打进去的文件，不会悄悄丢。

## 导入时的落盘映射

包内路径与落地位置的对应关系：

- `transcript/<id>.jsonl` → `~/.workbuddy/projects/<按新 cwd 编码>/<id>.jsonl`
- `transcript/<id>.meta.json` → 同目录，同名
- `aux/<kind>/<id>/...` → `~/.workbuddy/<实际目录>/<id>/...`
- `workspace/<id>/...` → `<新 cwd>/...`，其中 cwd 由 `--workspace-root` 加上原目录名推出

`aux` 里的名字和实际目录名不完全一致（比如 `workspace-sessions` 实际是 `workspace/sessions`），映射表在 `wb_migrate.py` 的 `AUX_DIRS` / `AUX_FILES` 里。

## 路径重写

两台机器用户名不同时，对话里的绝对路径得改。工具为每一对「旧路径 → 新路径」生成三种写法（JSON 转义反斜杠 `C:\\Users\\x`、原样 `C:\Users\x`、正斜杠 `C:/Users/x`），按长度倒序排，然后**单遍**扫描文本做替换。

单遍很关键：链式替换会出现「改完的结果又被下一条规则命中」，比如把 `C:\Users\a` 换成 `C:\Users\b` 之后，规则 `C:\Users\b → ...` 又把它改一次。用一次正则 `re.sub` 加回调就能避开。

盘符开头的路径匹配忽略大小写，因为 WorkBuddy 自己的记录里同一个路径有时写 `C:` 有时写 `c:`。
