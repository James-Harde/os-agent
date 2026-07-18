# Git 使用手册

> 适用场景：Windows Git Bash / Mac Terminal / Linux Terminal
> 原则：照抄命令，括号里的内容换成你自己的，不用背

---

## 一、首次配置（全局，只需做一次）

```bash
git config --global user.name "你的GitHub用户名"
git config --global user.email "你的GitHub邮箱"
```

**作用**：告诉 Git 你是谁，每次提交都会带上这个身份。`--global` 表示所有项目共用，配一次就行。

验证配好了：
```bash
git config --global --list
```

---

## 二、第一次推送（全新本地项目 → GitHub）

### 场景 A：本地已有代码，GitHub 已建好空仓库

```bash
git init                              # 在当前目录初始化本地仓库
git add .                             # 暂存当前目录所有改动
git commit -m "first commit"          # 提交到本地仓库（写个说明）
git remote add origin 仓库地址         # 关联远程仓库，origin 是它的别名
git push -u origin master:main        # 推送到远程 main 分支
```

逐条解释：

| 命令 | 作用 |
|---|---|
| `git init` | 把当前目录变成 Git 管理目录，生成隐藏的 `.git` 文件夹 |
| `git add .` | 把所有改动放入"暂存区"（准备提交的快照） |
| `git commit -m "..."` | 把暂存区的内容正式存入本地仓库，`-m` 后面是说明 |
| `git remote add origin 地址` | 给远程仓库起个名字叫 `origin`，以后直接用这个名字 |
| `git push -u origin master:main` | 把**本地 master** 推到**远程 main**。`-u` 设好跟踪，以后直接 `git push` |

> ⚠️ 常见问题：本地分支是 `master`，GitHub 默认分支是 `main`，所以用 `master:local` 语法把本地推到远程对应分支。

### 场景 B：GitHub 已建好仓库，从远程克隆下来开发

```bash
git clone 仓库地址                    # 把远程仓库完整下载到本地
cd 项目文件夹                          # 进入项目目录
# ... 改代码 ...
git add .
git commit -m "说明改了啥"
git push                              # clone 下来已经关联好了，直接 push
```

`clone` = 下载项目 + 自动关联远程 + 自动切换到默认分支，最省事。

---

## 三、后续每次修改（日常三步）

```bash
git add .                 # 暂存所有改动（或 git add 文件名 只暂存某个文件）
git commit -m "本次改动说明"  # 提交到本地仓库
git push                  # 推送到GitHub
```

**口诀：加 → 交 → 推**

养成好习惯：
- 写完一个功能就 commit，不要攒一大堆
- commit 说明用中文/英文都行，但要能看懂"这次改了什么"

---

## 四、常用查看命令（只读，不会改东西）

```bash
git status                # 看哪些文件改了、哪些已暂存
git log                   # 看提交历史
git log --oneline         # 精简版历史，一行一条
git diff                  # 看具体改了什么内容（未暂存的）
git branch                # 看本地有哪些分支
git remote -v             # 看关联了哪些远程仓库
```

### 场景 C：参与别人项目（Fork → PR 完整链路）

这是给开源项目贡献代码的标准做法，改动不会被直接推到原仓库，而是走"合并请求"。

```
步骤 1：在 GitHub 网页上点目标仓库的 Fork 按钮
        （复制一份到你自己的账号下）

步骤 2：把你 fork 下来的仓库克隆到本地
git clone https://github.com/你的用户名/仓库名.git
cd 仓库名

步骤 3：创建一个新分支来改代码（不要直接在主分支改）
git checkout -b fix-某个问题

步骤 4：改代码 → 暂存 → 提交
git add .
git commit -m "fix: 修复了xxx问题"

步骤 5：推送到你自己的 fork
git push -u origin fix-某个问题

步骤 6：回到 GitHub 网页，你的 fork 页面会弹出
       "Compare & pull request" 按钮 → 点它填写说明 → 提交
        
步骤 7：等原作者审核通过并合并到原仓库
        （可以在 Pull Requests 页面看状态）
```

**流程图**：
```
原仓库 ──Fork──→ 你账号下的fork ──clone──→ 本地
                                                  │
                                                  ↓
                                           改代码 / commit
                                                  │
                                                  ↓
                                            push 到 fork
                                                  │
                                                  ↓
                                       GitHub 上提 Pull Request
                                                  │
                                                  ↓
                                      原仓库作者 Review → Merge
```

> 💡 提 PR 前建议先同步原仓库最新代码，避免冲突：
> ```bash
> git remote add upstream 原仓库地址       # 把原仓库叫 upstream
> git fetch upstream                      # 拉取原仓库最新内容
> git merge upstream/main                 # 合并到你当前分支
> ```

---

## 五、常见报错速查

| 报错 | 原因 | 解决 |
|---|---|---|
| `src refspec main does not exist` | 本地分支名和远程对不上 | 检查 `git branch`，用 `git push -u origin 本地分支:main` |
| `conig is not a git command` | 命令拼错了 | 是 `config` 不是 `conig` |
| `fatal: not a git repository` | 当前目录没 init 过 | 先 `git init` |
| `rejected: non-fast-forward` | 远程有本地没有的提交 | 先 `git pull` 合并，再 `push` |
| 每次 push 都要输密码 | 没配 SSH 密钥 | 用 HTTPS 就忍了，或去配 SSH（GitHub 文档搜"connecting with ssh"） |

---

## 六、分支快速上手（进阶）

```bash
git branch dev            # 叫 dev 的分支
git checkout dev          # 切换到 dev 分支
git checkout -b dev       # 创建并切换，上面两条的简写
git checkout master       # 切回主分支
git merge dev             # 把 dev 合并到当前分支
```

---

## 七、一句话总结

| 场景 | 操作 |
|---|---|
| 第一次推本地项目 | `init` → `add` → `commit` → `remote add` → `push` |
| 日常改代码 | `add` → `commit` → `push` |
| 克隆别人的项目 | `clone` → 改代码 → `add` → `commit` → `push` |

---
生成时间：2026-07-18
