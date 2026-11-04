# 参与开发

这个工具的改动大多落在「对一个非公开数据格式的理解」上，所以下面几条比代码风格更重要。

## 跑起来

```bash
pip install -r requirements-dev.txt
python wb_migrate.py list        # 核心逻辑，零依赖，先确认能读到本机会话
python wb_app.py                 # 桌面窗口
build.cmd                        # 出 exe（Windows，双击或在 cmd 里运行）
```

不想动真实数据时，用 `WORKBUDDY_HOME` 指向一个沙盒：

```bash
cp -r ~/.workbuddy /tmp/fake-home
WORKBUDDY_HOME=/tmp/fake-home python wb_migrate.py list
```

## 改之前先看清数据

`docs/data-format.md` 记录了目录布局、数据库字段、路径编码规则。这些都是在真实机器上核对过的事实，不是推测。如果某条和你的环境不符，那很可能就是需要适配的新版本差异，欢迎提 issue 并附上 `python wb_migrate.py doctor` 的输出。

## 提交前的自检

```bash
python wb_migrate.py doctor
```

重点看「路径编码自检」那一行：命中数应该等于本地会话总数，未命中为 0。这条自检如果挂了，说明 WorkBuddy 改了目录规则，或者路径编码算法需要更新。

改动涉及导入时，请务必在沙盒里跑一遍完整往返：

```bash
WORKBUDDY_HOME=/tmp/a python wb_migrate.py export --session latest -o /tmp/p.zip
WORKBUDDY_HOME=/tmp/b python wb_migrate.py import /tmp/p.zip --yes
```

然后确认目标库里 `sessions` 有那条记录、`projects/` 下的 jsonl 逐行能解析、工作空间文件哈希与源一致。

## 代码约定

- `wb_migrate.py` 只用标准库，不引入第三方依赖——它要能在只有 Python 的机器上直接跑。
- 界面逻辑（`wb_gui.py`）复用核心逻辑，不重复实现打包/导入。两者走同一套 `argparse.Namespace`，避免行为分叉。
- 写入用户数据前先备份、先预演。任何失败都不能留下半个会话。
- 中文注释和中文输出是刻意的，这个工具的使用者都在中文环境里。
