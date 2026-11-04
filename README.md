# wb-migrate · 工作台迁移

> 同一个账号，换台电脑，把整个会话连工作区一起搬过去。不联网、不登录、一个 zip 搞定。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-3776ab.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## 这是什么

WorkBuddy 的对话在云端跟着账号走，但真正让对话「能用」的东西留在本地：工作区的文档和代码、会话的文件修改历史、任务清单、`.workbuddy/memory` 里沉淀的记忆。换台电脑登录同一个账号，对话列表能看见，点进去却发现工作区是空的——因为那些文件从来没有离开过旧机器。

这个工具把一整个会话打成一个包，带到新机器上还原：对话正文、附属数据、工作空间目录文件，一个都不少。路径会自动改写成新机器上的位置，数据库记录、侧边栏缓存一并登记，重启应用就能在左侧列表里看到。

全程离线。不经过任何服务器，不需要登录，不上传任何东西。

**它不是官方工具**，是照着 WorkBuddy 本地数据结构写的第三方程序。用之前请先备份（导入时工具自己也会备份数据库）。

## 它能搬什么

一个会话在磁盘上其实分散在六七个地方，工具会一并处理：

- **对话正文** —— 每一句话、每次工具调用、每个思考过程，都在 `projects` 目录下的 `jsonl` 里，逐行完整带走。
- **会话附属数据** —— 文件修改历史（撤销用）、变更明细、产物索引、文件树清单、任务清单、会话内自动备份。少了这些，对话能看但没法撤销和回滚。
- **工作空间目录** —— 这个会话的整个工作目录，包括生成的文档、表格、图片、代码，以及 `.workbuddy/memory/` 里的记忆文件。默认全带，单个文件超过 300 MB 的会跳过并在清单里记录。
- **数据库记录与侧边栏缓存** —— 新机器上要能看到、能点开，靠的是这一层。

不处理的：云端会话。那类会话正文本来就存在服务器上，本地没有文件，同账号登录后本来就能看到。

## 快速开始

### 方式一：用现成的 exe（Windows）

从仓库的 Releases 页面下载 `工作台迁移.exe`，双击。它会开一个自己的窗口——不是浏览器、没有地址栏、不用装 Python、不用敲命令。不想下载二进制也可以照「方式二」自己跑源码。

第一次运行 Windows 可能提示「已保护你的电脑」，因为程序没买代码签名证书。点「更多信息」→「仍要运行」即可，之后不再提示。

### 方式二：从源码跑

需要 Python 3.8 以上。

```bash
git clone https://github.com/<你的账号>/wb-migrate.git
cd wb-migrate
pip install -r requirements.txt   # 只有桌面窗口需要，命令行版零依赖
python wb_app.py                  # 原生窗口
```

想用浏览器打开界面，跑 `python wb_gui.py` 也行，它会打印带令牌的本地地址。

### 方式三：命令行

零依赖，只要有 Python。

Windows 直接双击 `wb-migrate.cmd`，或者在终端里：

```bash
python wb_migrate.py list
python wb_migrate.py export --session 3
python wb_migrate.py import wb-20261103-1050-标题.zip --yes
```

## 界面

窗口里就两件事。

**打包带走** —— 列出本机所有会话，勾上要带走的（可以全选），决定要不要连工作目录里的文件一起带，选个保存位置，点「开始打包」。完成后自动在资源管理器里定位到那个 zip。

**接收进来** —— 选那个包，界面先读出内容给你看：几个对话、多少条消息、来源哪台机器、带不带工作目录、和本机有没有重名。确认没问题再点「开始导入」，跑完提示你重启 WorkBuddy。

顶部有一排状态：本机路径、WorkBuddy 是否在运行、以及「环境自检」按钮——它就是命令行的 `doctor`，会检查目录、数据库、路径编码规则、应用状态，有问题当场指出来。

包就是一个普通 zip。接收端直接读它，不需要先解压。同一时间只允许开一个窗口，避免两个进程同时写数据库。

## 命令行参考

### list

```bash
wb-migrate list
```

列出本机会话：标题、时间、消息条数、工作空间路径和体积。默认不显示回收站里的，加 `--include-deleted` 查看。

### export

```bash
wb-migrate export --session 3
wb-migrate export --session all --no-workspace
```

`--session` 支持四种写法：序号（`3`）、完整 ID、ID 前缀、标题关键字，另外还有 `all` 和 `latest`（默认）。多个用逗号分隔取并集。

其余参数：

- `--out 路径` —— 输出文件位置，默认按时间和标题命名
- `--no-workspace` —— 只带对话，不带工作空间文件
- `--max-file-mb 100` —— 单文件体积上限，默认 300 MB
- `--include-cloud` —— 允许选择云端会话（默认排除）
- `--include-deleted` —— 允许选择回收站里的会话

### import

```bash
wb-migrate import 包.zip          # 预演，只打印计划
wb-migrate import 包.zip --yes    # 真正写入
```

默认只预演：列出每个会话的标题、原工作空间路径、将放到哪里、要写多少文件、有没有重名。看完没问题再加 `--yes`。

其余参数：

- `--workspace-root D:\WB` —— 工作空间放哪个根目录，默认 `~/WorkBuddy`
- `--keep-cwd` —— 保留原绝对路径不做重映射（两台机器路径完全一致时才用）
- `--force` —— 覆盖目标机已存在的同 ID 会话；WorkBuddy 运行中也强行写入
- `--new-id` —— 已存在时复制成一个新 ID 的会话，不动原来的
- `--no-workspace` —— 只导入对话
- `--overwrite-files` —— 目标目录已有同名文件时覆盖（默认跳过）
- `--map "旧路径=新路径"` —— 额外的路径替换规则，可重复指定

### inspect

```bash
wb-migrate inspect 包.zip
```

只看包里有什么，不写任何东西。收到别人发来的包，先跑这个。

### doctor

```bash
wb-migrate doctor
```

环境自检：目录在不在、数据库能不能读、路径编码规则和磁盘对不对得上、WorkBuddy 是不是在运行。

## 导入时到底发生了什么

1. **备份** —— 把目标机的 `~/.workbuddy/workbuddy.db` 复制一份带时间戳的副本，路径写进日志，出问题能换回去。
2. **重写路径** —— 两台机器的用户名往往不一样，对话里到处是 `C:\Users\张三\...` 这类绝对路径。工具把「旧工作空间路径」和「旧用户目录」两种前缀替换成新机器的对应位置。替换是单遍完成的，不会出现改完一处又被下一条规则再改一次；Windows 路径大小写不敏感，匹配时忽略大小写（WorkBuddy 自己的记录里同一个路径有时写 `C:\` 有时写 `c:\`）。
3. **落盘** —— 对话正文写进 `projects/<按路径编码的目录>/<会话ID>.jsonl`，附属数据写进对应的 `file-history`、`tasks`、`workspace/sessions` 等目录，工作空间文件写进新路径。同名文件默认跳过，保护你新机器上的已有文件。
4. **写库** —— 往 `sessions` 表插入记录，工作空间路径换成本机的新路径；同时登记 `workspaces` 表、更新侧边栏缓存，保证重启后能看到。
5. **校验** —— 导入前核对对话记录的 SHA256，导入后逐行确认 JSON 能解析、记录里的工作空间路径和数据库里的一致。任何一步不对就报错停下，不会留下半个会话。

数据库字段只填两边都存在的。老版本没有的列走默认值，新版本多出来的列不会被写坏。

## 常见问题

**导入成功，但左侧列表里看不到会话。**
先确认是完全退出再重开，而不是只关了窗口。还看不到就跑 `wb-migrate doctor`，看「路径编码自检」是不是全部命中。自检通过仍看不到，多半是应用缓存：退出后删掉 `~/.workbuddy/<用户ID>/sidebar-list-snapshot.json` 再打开。

**点开会话是空的，没有内容。**
说明对话正文被放到了应用不会去读的目录。跑 `doctor` 看编码自检；若通过仍是空，用 `--workspace-root` 把工作空间放到 `~/WorkBuddy` 下面重新导入一次。残留的空会话可以用 `--force` 或 `--new-id` 处理。

**提示「目标机已存在同名会话」。**
会话 ID 一样。想覆盖用 `--force`，想并排放两份用 `--new-id`。

**导出时提示「找不到该会话的对话记录，跳过」。**
数据库里有记录，但本地正文文件已经没了（比如手动清理过 `projects` 目录）。这种会话迁不走内容，工具选择跳过而不是给你一个空壳。

**包里没有工作空间文件。**
导出时那个目录已经不存在，或者你用了 `--no-workspace`。对话照样能迁，导入后会把空目录建出来，避免会话打不开。

**体积太大。**
工作空间里可能有几百 MB 的素材。用 `--max-file-mb 50` 限制单文件，或者 `--no-workspace` 只迁对话。跳过的文件记在包内 `manifest.json` 里，不会悄悄丢掉。

**界面显示「WorkBuddy 运行中」，但我没开。**
判断方式是看系统进程里有没有 `WorkBuddy.exe`。有残留进程（比如托盘里还留着）就会显示运行中，结束它就行。

## 已知限制

- 只处理本地会话（`transport` 为 `local`）。云端会话说到底不在本地。
- 不做双向同步，只做「一次性搬过去」。搬完两边各自独立演进。
- 文件修改历史是按内容寻址存的，迁移后能看能撤销；但如果在新机器上对文件做了新修改，历史链会从迁移那个点重新开始。
- 目标机的 WorkBuddy 版本比旧机器老很多时，数据库字段可能有差异。工具会自动只填两边都有的字段，极端情况下仍可能缺少新版本的特性开关。
- 本工具依赖 WorkBuddy 的本地数据布局。官方改目录结构就得跟着改，`doctor` 的路径编码自检就是用来发现这种变化的。

## 目录结构

```
wb_migrate.py      核心逻辑：扫描会话、打包、导入、路径重写、命令行入口
wb_app.py          桌面外壳：原生窗口（pywebview/WebView2）+ 启动本地服务 + 单实例控制
wb_gui.py          界面后台：本地 HTTP 服务、任务队列、文件选择对话框
ui.html            界面本体（纯静态，零外部依赖，可离线）
make_icon.py       生成程序图标 app.ico（纯标准库画的，改配色可重出）
build.cmd          一键打包 exe（Windows）
wb-migrate.cmd/sh  命令行启动器（Windows / macOS / Linux）
wb-gui.cmd/sh      浏览器版启动器（调试用）
docs/data-format.md  WorkBuddy 数据结构、包格式、路径编码规则
docs/publish-to-github.md  第一次把项目发布到 GitHub 的完整操作步骤
```

`ui.html` 必须由后台提供数据，直接双击打开只会看到空壳。

## 开发与构建

```bash
pip install -r requirements.txt
python wb_app.py            # 开发时直接跑窗口
python wb_app.py --selftest # 无头自检，把结果写成 JSON
python wb_app.py --check out.json --wait 3 --js "document.getElementById('btnDoctor').click()"
```

最后一条会在窗口打开后注入一段 JS 模拟真实点击，再读回 DOM 状态写进 JSON——没有浏览器自动化工具时，这是验证界面的土办法。

打包 exe（Windows）：双击 `build.cmd`，或手动执行：

```bash
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name "工作台迁移" --icon app.ico --add-data "ui.html;." \
  --collect-all webview --collect-all pythonnet --collect-all clr_loader \
  --hidden-import webview.platforms.edgechromium \
  --exclude-module tkinter wb_app.py
```

产物在 `dist/`。exe 里含了 Python 运行时和 WebView2 调用层，所以有十几 MB；目标机器需要装 WebView2（Win10 1803+ 和 Win11 自带），没有的话程序会自动改用浏览器打开并打印地址。

测试里可以用 `WORKBUDDY_HOME` 环境变量把数据目录指向沙盒，避免动真实数据：

```bash
WORKBUDDY_HOME=/tmp/fake wb-migrate list
```

## 数据格式

WorkBuddy 的本地数据布局、包内结构、路径编码算法都记在 [docs/data-format.md](docs/data-format.md)。

## 许可证

[MIT](LICENSE)。随便用，出问题别找我。

---

再次提醒：这是第三方工具，与 WorkBuddy 官方无关。它直接读写本地数据库和文件，操作前请自行备份，或先拿一个不重要的会话试一遍。
