# ai-answer-archive

> 把 AI 对话分享链接里的回答完整存下来，批量判品牌可见度，出一份能交付、能复核的原始记录索引

做 GEO（生成式引擎优化）监测，要在豆包、ChatGPT、DeepSeek 里反复问同一批问题，每条回答都得留底。分享链接最省事，可是直接打开分享页存不全：

- ChatGPT 分享页不登录打开是空白；
- 豆包分享页会把 `chatgpt.com` 这类域名显示成空白，来源列表也只露出十来条；
- DeepSeek 分享网页在有些网络下打不开。

这个工具不开网页，直接取分享链接背后的数据：一批链接进去，出来一张「回答存档表」（一条回答一行，完整正文、AI 自己返回的来源、搜索词），每条的原始数据另存一份。再接上 [GEO 可见度判定助手](https://github.com/jiguang9/geo-judge) 批量判，最后合成一份「原始记录索引」，对应中国广告协会 T/CAAAD 047—2026《生成式引擎优化效果监测与验证要求》第 13.1 条列的交付物。

- 支持豆包、ChatGPT（`chatgpt.com/s/…` 单条回答分享）、DeepSeek。
- 只请求分享链接本身，**不调用任何 AI 接口**，不登录任何账号。
- 只用 Python 3 标准库，不用装依赖。本地运行，数据不上传。
- 取数失败的照实标「要人打开看」和原因，不悄悄漏掉。

## 三步

```
链接表 ──archive.py──▶ 回答存档表.csv + 原始数据/
                         │
                         ▼  geo-judge（网页「导入存档表」或命令行 judge-archive.js）
                       判定结果.csv
                         │
                         ▼  build_index.py
                       原始记录索引.csv + 原始记录索引说明.md
```

```bash
cd skills/ai-answer-archive

# 1 存档
python3 tools/archive.py examples/链接表-示例.csv 输出目录

# 2 判定：用 geo-judge 仓库的命令行脚本（Node 18+），或在线判定助手第 3 步「导入存档表（CSV）」
node /path/to/geo-judge/tools/judge-archive.js 输出目录/回答存档表.csv \
  --brand "你的品牌, 别名" --domain 你的官网.com --competitors "竞品A|别名, 竞品B" \
  -o 输出目录/判定结果.csv

# 3 出索引
python3 tools/build_index.py 输出目录 输出目录/判定结果.csv examples/索引配置-示例.json
```

`examples/链接表-示例.csv` 里的分享 ID 是占位的，换成你自己的链接再跑。

## 当 Agent Skill 用

装好后，在 Claude Code、Codex 等 Agent 里说「把这批分享链接存档并判一下」，它会按 [`skills/ai-answer-archive/SKILL.md`](skills/ai-answer-archive/SKILL.md) 走完三步：先问清问题原文、品牌竞品和测试环境，跑完逐步核对，最后交付整个输出目录。

| 平台 | 安装命令 |
|---|---|
| Claude Code | `npx skills add https://github.com/jiguang9/ai-answer-archive --skill ai-answer-archive -g -a claude-code` |
| Codex | `npx skills add https://github.com/jiguang9/ai-answer-archive --skill ai-answer-archive -g -a codex` |
| OpenClaw | `npx skills add https://github.com/jiguang9/ai-answer-archive --skill ai-answer-archive -g -a openclaw` |
| Hermes Agent | `hermes skills install jiguang9/ai-answer-archive/skills/ai-answer-archive` |

安装用的是第三方的 [Vercel skills CLI](https://github.com/vercel-labs/skills)；不想用的话，`git clone` 本仓库后把 `skills/ai-answer-archive/` 复制进 `~/.claude/skills/` 或 `~/.codex/skills/` 也一样。更新：`bash ~/.claude/skills/ai-answer-archive/update.sh`（只需要 `curl` 或 `wget`）。

判定那一步要用到 [geo-judge](https://github.com/jiguang9/geo-judge)：本机有它的副本就用命令行脚本，没有就用[在线判定助手](https://aimarketinglift.com/tools/geo-judge)导入存档表，两种方式结果逐字节一致。

## 链接表

| 格式 | 写法 |
|---|---|
| `.csv` / `.tsv` | 表头含「问题」「第几次」「分享链接」，「平台」可写可不写（不写按链接认） |
| `.md` | Markdown 表格，列为「题 \| AI \| 第 1 次 \| 第 2 次 \| …」，题前面的题号（如 `A1 `）会自动去掉 |
| `.txt` | 一行一个链接，或「问题 Tab 第几次 Tab 链接」 |

问题原文要一字不差。ChatGPT 单条分享里不带提问，只能靠链接表写。豆包、DeepSeek 的分享里有提问，和链接表对不上会标出来，多半是链接顺序贴错了。

## 回答存档表的列

UTF-8 带 BOM，Excel、Numbers、WPS 直接打开不乱码。

| 列 | 说明 |
|---|---|
| 问题原文 | 取链接表里写的。链接表没写，就用分享数据里的提问 |
| 平台 | 豆包 / ChatGPT / DeepSeek / … |
| 档位 | ChatGPT 填分享数据里的模型名；DeepSeek 填 `model_type`，开了深度思考再加「· 深度思考」；豆包的分享数据里没有能对上「快速 / 思考 / 专家」的字段，留空 |
| 第几次 | 取链接表 |
| 日期 | 回答生成的日期，北京时间 |
| 回答正文 | 完整原文，Markdown。引用角标三家统一写成 `（来源：站点名）`，按原位置插回；正文里的视频、图片写成 `[视频卡片：…]` `[图片：…]`；ChatGPT 的界面标记还原成文字：官网按钮写成 `[官网](链接)`，图表写成一行 `[图表] …`，表单选项逐行列出；思考过程和「AI 生成仅供参考」提示不算正文 |
| 来源列表 | AI 自己返回的来源，一行一条：`标题 \| 域名 \| 链接`；正文里引用到的、或正文直接给的链接，行尾多一个 `\| 正文引用`。同一网址只留一条（参数顺序、`#` 后面不同也算同一条）。标题里本来的 `\|` 换成全角 `｜` |
| 分享链接 | 原样 |
| 取数状态 | `成功`，或 `要人打开看：原因`（几条原因用「；」隔开） |
| 原始数据文件 | 这条的原始数据存在哪 |
| 搜索词 | AI 联网时搜了哪些词，一行一个。豆包、DeepSeek 有；ChatGPT 分享数据里没有，留空 |
| 回答时间 | 回答生成的时间，北京时间，精确到分钟 |
| 取数时间 | 工具请求这条分享链接的时间，北京时间，精确到分钟 |

「要人打开看」会在这些情况出现：链接打不开或已失效；分享数据里没找到回答；豆包任务模式（生成了文件或表格，分享数据里只有说明文字）；分享里的提问和链接表对不上；多轮对话（只存最后一轮）；ChatGPT 回答没有正常结束或带图片文件；正文超过 Excel 单格上限 32767 字；平台暂不支持。

## 原始数据怎么存的

- **豆包**：分享接口返回的 JSON 原样存，只把分享者昵称、头像和账号 ID 换成 `[已隐去]`。
- **ChatGPT**：分享页源码里的 turbo-stream 数据原文（`payload`），加上按下标还原后的结构（`decoded`）。分享页是匿名的，不含分享者信息。数据里带每次请求的编号和耗时，同一条链接重新取一次，文件哈希会变，回答内容不变。
- **DeepSeek**：分享数据接口返回的 JSON 原样存，里面没有分享者信息。

## 原始记录索引

`build_index.py` 把存档表和判定结果按「分享链接」对上，写回存档目录：

- `原始记录索引.csv`：一条回答一行。编号、问题、平台、模型 / 档位、第几次、回答时间、取数时间、账号状态、会话、联网、记忆、地区、取数状态、判定结果、来源 / 正文引用 / 搜索词条数、分享链接、原始数据文件、原始数据 SHA-256。字段参考 T/CAAAD 046—2026《生成式引擎优化服务要求》第 7.4.2 条。
- `原始记录索引说明.md`：采样范围（按 047 第 9.2 条要披露的项目）、数量核对、判定汇总、判定口径、怎么复核一条、限制、问题清单。

环境和限制写在索引配置 JSON 里（例子见 `examples/索引配置-示例.json`）。测试时没记下来的写「未记录」，不要补猜。取数失败的回答也列进索引，照实标原因。交付时把整个目录给对方，对方可以顺着分享链接、原始数据和哈希逐条复核。

## 校准

开发时用 45 条真实分享链接（豆包、ChatGPT、DeepSeek 各 15 条，5 个问题各问 3 次）校准过：

- 45 条全部取到。和当时手工存的页面原文比，正文覆盖率豆包、ChatGPT 在 89%–100%，剩下的差异是引用角标和图表的写法不同，不是漏字；DeepSeek 和当时下载的分享数据逐字一致。
- 豆包来源条数和页面上「参考 N 篇资料」对得上（同一篇文章带不同 `#` 片段列两次的，合成一条）；ChatGPT 来源和账号里「查看来源」面板逐条一致。
- 接上判定助手后，45 条的判定状态和逐条粘贴进网页判的结果完全一致；网页导入和命令行判出来的 CSV 逐字节一致。

## 注意

- 各家分享页和分享接口都不是公开 API，随时可能改。取不到时会标「要人打开看」，请到 issue 里反馈。
- 只处理你自己的、或你有权处理的分享链接。
- 请求之间默认隔 1 秒，别拿它高频批量抓取。

## 项目结构

```
skills/ai-answer-archive/
  SKILL.md                 Agent Skill 说明
  tools/archive.py         存档：链接表 → 回答存档表 + 原始数据
  tools/build_index.py     出索引：存档表 + 判定结果 → 原始记录索引
  examples/                链接表、索引配置的例子
  update.sh                更新已安装的 Skill
tests/                     python3 -m unittest
```

## License

MIT
