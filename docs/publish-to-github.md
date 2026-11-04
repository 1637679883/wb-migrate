# 从零发布到 GitHub

这份指南假设你从来没用过 GitHub。跟着做，大约 20 分钟能把代码放上去、把 exe 挂出来给人下载。

不需要理解 git 的原理，照着输命令就行。整篇只有六步，每步结束都有「怎么确认这步成功了」。

> **当前状态：这六步已经全部做完了。** 仓库在 `https://github.com/1637679883/wb-migrate`，默认分支 `main`，Release `v1.0.0` 已挂着 `wb-migrate.exe`。
> 所以这份文档现在有两种用法：一是当「我当时是怎么做的」的说明书看；二是将来在另一台机器上、或者另起一个项目时照着再走一遍。
> 已经完成的部分我在下面各步开头标了 ✅，照着做时跳过即可。

---

## 第 0 步：先确认两件事

打开 PowerShell（或者在这台机器上直接用工具执行），输入：

```
git --version
```

看到 `git version 2.55.0.windows.3` 之类的输出就说明装好了。这台机器上已经有了，不用再装。

第二件事：确认你的 `~/.gitconfig` 里配了名字和邮箱。这个信息会写进每一条提交记录，GitHub 靠它把你的提交和你的头像关联起来。

```
git config --global user.name
git config --global user.email
```

如果输出是空的，或者邮箱不是你的，下面第三步会教你怎么改。

---

## 第 1 步：注册 GitHub 账号

已经有账号的话跳到第 2 步。

打开 https://github.com/signup

要填三样东西：邮箱、密码、用户名。用户名会出现在所有链接里，比如用户名是 `xiaoming`，仓库地址就是 `https://github.com/xiaoming/wb-migrate`。一旦定下来就不好改，想清楚再填——用英文、数字、连字符，别用中文。

然后是邮箱验证码，去邮箱里把那串数字填进去。

注册完会让你选免费套餐还是付费，选左边的 **Free**，够用了。

**怎么确认成功**：浏览器打开 `https://github.com/你的用户名`，能看到你的主页，右上角有你的头像。

---

## 第 2 步：在网页上新建一个空仓库

点右上角头像左边的 `+` 号，选 **New repository**。

三处要填：

**Repository name** 填 `wb-migrate`。

**Description** 可选，写一句 `WorkBuddy 会话与工作空间跨机器迁移工具`。

**Public / Private** 选 **Public**。开源就是公开，选 Private 别人看不见。

往下拉，有三行勾选框：**Add a README file**、**Add .gitignore**、**Choose a license**。

**这三行一个都不要勾。** 这是新手最容易踩的坑：本地代码已经准备好了，GitHub 上如果先建了个 README，两边历史就分叉了，第一次推送会被拒绝。

点最下面的绿色按钮 **Create repository**。

**怎么确认成功**：跳转到一个页面，标题是你的仓库名，中间黑框里写着「Quick setup — if you've done this before」，底下有一串 `https://github.com/你的用户名/wb-migrate.git`。

**把这行地址复制下来，后面要用。**

---

## 第 3 步：确认提交作者是谁（✅ 已完成）

现在仓库里那几条提交的作者是 `1637679883 <1637679883@qq.com>`，就是仓库所有者的账号，GitHub 上会正常显示成你。

如果你想把显示名换成别的（比如 `DrivingGod1980`），**只改配置不影响已有的提交**，历史里还是旧名字。要让历史跟着改，得重写：

```
cd "C:/Users/Lenovo/WorkBuddy/2026-11-01-16-04-06/wb-migrate"
git config user.name "新名字"
git config user.email "你注册GitHub用的邮箱"
git rebase --root --exec "git commit --amend --reset-author --no-edit"
git push --force-with-lease
```

邮箱**必须**是你 GitHub 账号绑定的那个，否则提交不会记到你名下。

最后那条 `git rebase --root` 会把所有提交的作者重写一遍，输出里会滚过几行 `Executing: git commit --amend`，最后一行是 `Successfully rebased and updated`。因为提交哈希全变了，远程和本地必须重新对齐，所以才需要 `--force-with-lease`——用 `--force-with-lease` 而不是 `--force`，万一远程刚被别人推过，它会把你的操作拦下来。

**怎么确认成功**：

```
git log --format="%an <%ae> | %s"
```

每行的作者都应该是你想显示的名字和邮箱。

---

## 第 4 步：克隆地址与分支名（✅ 已完成）

README 里的 clone 地址已经是真实地址，没有占位符了：

```
git clone https://github.com/1637679883/wb-migrate.git
```

**如果你把这个项目 fork 或搬到自己的账号下**，记得把这行换成你自己的用户名，别人照抄才不会报错。全文只有这一处地址需要改。

分支名已经是 `main`，和 GitHub 一致。新建仓库时如果你是从 `master` 起步的，用这条改过来：

```
git branch -M main
```

**怎么确认成功**：

```
git branch --show-current
```

应该输出 `main`。

---

## 第 5 步：连上远程仓库并推送

```
git remote add origin https://github.com/你的用户名/wb-migrate.git
git push -u origin main
```

第一条把本地仓库和网页上那个仓库绑定，第二条把代码传上去。

**第一次推送会弹出一个窗口**，标题是「Git Credential Manager」，让你选择登录方式。

选 **Sign in with your browser**（用浏览器登录）。浏览器会打开 GitHub 的授权页，点绿色的 **Authorize** 就行。整个过程不用输密码。

授权完窗口自动关掉，命令行开始滚动上传进度，最后出现：

```
branch 'main' set up to track 'origin/main'.
```

**怎么确认成功**：刷新你的仓库页面，能看到 README 里的中文说明、绿色按钮上写着「Code」，还有 22 个文件。

### 如果浏览器登录窗口没弹出来

有些网络环境会让这个窗口弹不出来。那时候命令行会直接问你要用户名和密码。**密码那里不能填你自己的 GitHub 密码**，GitHub 早在 2021 年就不接受密码了，必须用 Token。

申请 Token 的步骤：

1. GitHub 页面右上角头像 → **Settings**
2. 左侧菜单拉到最底部 → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)**
4. 右上角 **Generate new token** → **Generate new token (classic)**
5. Note 随便写，比如 `wb-migrate`；Expiration 选 90 days
6. 下面的权限列表里，勾上第一项 **repo**（勾它会连带勾上它下面的四个子项）
7. 拉到最底部，**Generate token**
8. 页面上会出现一串 `ghp_` 开头的字符。**这是唯一一次显示，立刻复制**

回到命令行，用户名那里填你的 GitHub 用户名，密码那里把那串 `ghp_...` 粘贴进去（粘贴时不显示字符，看不见是正常的，粘完直接回车）。

### 如果推送被拒绝，提示 `rejected ... fetch first`

说明第 2 步不小心勾了 README。补救：

```
git pull origin main --allow-unrelated-histories --no-rebase
git push -u origin main
```

如果 `git pull` 之后报冲突，输入 `git checkout --ours README.md` 保留本地的，然后 `git add -A`、`git commit -m "合并远程初始文件"`，再 push。

---

## 第 6 步：把 exe 挂到 Releases（✅ 已完成，见 v1.0.0）

仓库里不放 exe（17 MB 的二进制文件塞进 git 会让仓库越来越臃肿），走 Releases 发布，那里专门放编译好的成品，别人点一下就能下载。

1. 打开仓库页面，右侧栏中点 **Releases**，再点 **Create a new release**
2. **Choose a tag** 里输入 `v1.0.0`，右边会出现 **Create new tag: v1.0.0 on publish**，点它
3. **Target** 保持 `main`
4. **Release title** 填 `v1.0.0`
5. 大文本框里写说明，可以直接粘这段：

```
首个版本。

- 图形界面：两个页签，勾选会话即可打包，接收端先预览再写入
- 迁移内容：对话正文、元数据、工作空间文件、文件历史、任务记录
- 安全措施：导入前自动备份数据库，路径重写单遍扫描，逐行校验 JSON
- 命令行：list / export / import / inspect / doctor
- 单文件程序，不依赖 Python，不需要安装
```

6. 最下面 **Attach binaries** 区域，把 `wb-migrate/dist/wb-migrate.exe` 拖进去。上传要一两分钟
7. 点绿色按钮 **Publish release**

附件名就是别人下载到的文件名，要和 README 里写的一致。`build.cmd` 现在直接产出 `dist/wb-migrate.exe`，不用手动改名。

**怎么确认成功**：仓库首页右侧出现一个 `Releases` 区块，写着 `v1.0.0`，点进去能看到下载链接和 17 MB 的 exe 附件。

顺便把 README 里「从 Releases 下载」那句话后面的相对链接检查一下，确保指向 `../../releases`——它已经写好了，不用改。

---

## 以后改了代码怎么更新

改完文件，回到项目目录，三条命令：

```
cd "C:/Users/Lenovo/WorkBuddy/2026-11-01-16-04-06/wb-migrate"
git add -A
git commit -m "一句话说你改了什么"
git push
```

`git status` 可以随时看有哪些文件被改了还没提交。

想发新版本时，重复第 6 步，tag 换成 `v1.0.1`，附件换成新的 exe。

---

## 常见问题

**推送特别慢，或者卡在 `Writing objects` 不动**

网络问题。等一两分钟看看，实在不行按 `Ctrl+C` 中断再重试一次。多半是运营商到 GitHub 的线路不稳。

**问我要的邮箱会不会暴露**

提交记录里会公开显示。介意的话用 GitHub 提供的匿名邮箱：打开 Settings → Emails，勾上 **Keep my email addresses private**，页面会给你一个 `12345678+用户名@users.noreply.github.com` 形式的地址，用那个代替真实邮箱，效果一样。

**传上去发现有问题，想把提交撤掉**

```
git reset --soft HEAD~1
```

会撤掉最近一次提交，但保留文件改动，改完再重新 `git add -A` 提交。**已经 push 到 GitHub 的不要用这个**，会两边不一致，那种情况改用 `git revert`。

**仓库里误传了大文件**

`.gitignore` 已经挡住了 `dist/`、`build/`、`__pycache__`，正常不会发生。万一发生了，先 `git rm --cached 那个文件`，提交一次，文件就从仓库里移除了（本地文件还在）。

**想加个截图让 README 好看点**

在仓库页面点 **Issues** → **New issue**，把图片拖进输入框，GitHub 会自动生成一个 `https://github.com/user-attachments/...` 的地址，复制出来粘到 README 里就行。不用真的提这个 issue。
