# -*- coding: utf-8 -*-
"""把「回答存档表」和判定助手导出的「判定结果」合成一份可交付的原始记录索引。

用法：
  python3 build_index.py 存档目录 判定结果.csv 索引配置.json
存档目录是 archive.py 的输出目录（里面有 回答存档表.csv 和 原始数据/）。
输出写回存档目录：原始记录索引.csv（一条回答一行）和 原始记录索引说明.md。

索引按 047 第 13.1 条交付物里的「原始记录索引」整理，每行字段参考 046 第 7.4.2 条：
平台和模型版本、查询日期时间、搜索请求、账号状态、地区、联网和上下文状态、重复次数、原始回答、引用链接。
取数失败的回答也列进来，照实标原因，不删。
"""
import collections, csv, datetime, hashlib, json, os, sys

TZ = datetime.timezone(datetime.timedelta(hours=8))
COLUMNS = ['记录编号', '问题编号', '问题原文', '平台', '模型 / 档位', '第几次', '回答时间', '取数时间',
           '账号状态', '会话', '联网', '记忆', '地区',
           '取数状态', '判定状态', '品牌提及', '仅被引', '检索层命中', '出现位置', '竞品出现顺序', '被引官网链接',
           '来源条数', '正文引用条数', '搜索词条数', '负面提及',
           '分享链接', '原始数据文件', '原始数据 SHA-256']
STATUS_ORDER = ['正文提及', '仅被引', '仅检索到', '无引用数据', '未出现']


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def lines(cell):
    return [x for x in (cell or '').split('\n') if x.strip()]


def build(archive_dir, judge_csv, cfg_path):
    cfg = json.load(open(cfg_path, encoding='utf-8'))
    env = cfg.get('环境') or {}
    archive = read_csv(os.path.join(archive_dir, '回答存档表.csv'))
    judged = {r['分享链接']: r for r in read_csv(judge_csv) if r.get('分享链接')}
    if not judged:
        sys.exit('判定结果里没有「分享链接」列或全是空的。要用判定助手「导入存档表」后导出的 CSV。')

    qids = {}
    rows = []
    for i, a in enumerate(archive, 1):
        q = a['问题原文']
        qids.setdefault(q, f'Q{len(qids) + 1:02d}')
        e = {**(env.get('通用') or {}), **(env.get(a['平台']) or {})}
        j = judged.get(a['分享链接'])
        raw = a.get('原始数据文件') or ''
        raw_path = os.path.join(archive_dir, raw) if raw else ''
        src = lines(a.get('来源列表'))
        if a['取数状态'] != '成功':
            verdict = '未判定（取数失败）'
        elif not j:
            verdict = '未判定（判定结果里没有这条）'
        else:
            verdict = j['状态']
        rows.append({
            '记录编号': f'R{i:03d}', '问题编号': qids[q], '问题原文': q, '平台': a['平台'],
            '模型 / 档位': a.get('档位') or '分享数据里没有', '第几次': a.get('第几次', ''),
            '回答时间': a.get('回答时间') or a.get('日期', ''), '取数时间': a.get('取数时间', ''),
            '账号状态': e.get('账号状态', '未记录'), '会话': e.get('会话', '未记录'),
            '联网': e.get('联网', '未记录'), '记忆': e.get('记忆', '未记录'), '地区': e.get('地区', '未记录'),
            '取数状态': a['取数状态'], '判定状态': verdict,
            '品牌提及': j['品牌提及'] if j else '', '仅被引': j['仅被引'] if j else '',
            '检索层命中': j['检索层命中'] if j else '', '出现位置': j['出现位置'] if j else '',
            '竞品出现顺序': j['竞品出现顺序'] if j else '', '被引官网链接': j['被引官网链接'] if j else '',
            '来源条数': len(src), '正文引用条数': sum(1 for x in src if x.rstrip().endswith('| 正文引用')),
            '搜索词条数': len(lines(a.get('搜索词'))), '负面提及': (j.get('负面提及') or '') if j else '',
            '分享链接': a['分享链接'], '原始数据文件': raw,
            '原始数据 SHA-256': sha256(raw_path) if raw_path and os.path.exists(raw_path) else '没有原始数据',
        })

    out_csv = os.path.join(archive_dir, '原始记录索引.csv')
    with open(out_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    open(os.path.join(archive_dir, '原始记录索引说明.md'), 'w', encoding='utf-8').write(explain(cfg, rows, qids))
    print(f'索引 {len(rows)} 条 → {out_csv}')


def explain(cfg, rows, qids):
    plats = list(dict.fromkeys(r['平台'] for r in rows))
    times = sorted(r['回答时间'] for r in rows if r['回答时间'])
    fetched = sorted(r['取数时间'] for r in rows if r['取数时间'])
    ok = [r for r in rows if r['取数状态'] == '成功']
    bad = [r for r in rows if r['取数状态'] != '成功']
    with_raw = [r for r in rows if r['原始数据 SHA-256'] != '没有原始数据']
    rounds = collections.Counter((r['问题编号'], r['平台']) for r in rows)
    per = sorted(set(rounds.values()))
    env = cfg.get('环境') or {}
    common = env.get('通用') or {}
    J = cfg.get('判定设置') or {}

    L = [f"# 原始记录索引说明 · {cfg.get('项目', '')}", '',
         f"生成时间：{datetime.datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}（北京时间）", '',
         '这份索引一条 AI 回答一行。每一行都能顺着「分享链接」打开当时的对话，顺着「原始数据文件」看到取回来的原始数据，'
         '用「原始数据 SHA-256」核对文件有没有被改过。判定结果是工具按下面的口径自动判的，任何一条都可以按原始数据复核。'
         '取数失败的回答也列在里面，标明原因，没有删。', '',
         '本索引对应中国广告协会 T/CAAAD 047—2026《生成式引擎优化效果监测与验证要求》第 13.1 条列出的验收交付物之一「原始记录索引」，'
         '每行记录的字段参考 T/CAAAD 046—2026《生成式引擎优化服务要求》第 7.4.2 条（平台和模型版本、查询日期时间、搜索请求、账号状态、地区、联网和上下文状态、重复次数、原始回答、引用链接）。', '',
         '## 文件清单', '',
         '| 文件 | 内容 |', '|---|---|',
         '| `原始记录索引.csv` | 本索引，UTF-8，Excel / WPS / Numbers 直接打开 |',
         '| `原始记录索引说明.md` | 本说明 |',
         '| `回答存档表.csv` | 每条回答的完整正文、来源列表、搜索词 |',
         '| `原始数据/` | 每条回答从分享链接取回的原始数据，一条一个文件 |', '',
         '## 采样范围（按 047 第 9.2 条要披露的项目）', '',
         '| 项目 | 本次 |', '|---|---|',
         f"| 平台范围 | {'、'.join(plats)} |",
         f"| 问题集规模 | {len(qids)} 个问题（问题清单见文末） |",
         f"| 问题集来源 | {cfg.get('问题集来源', '未记录')} |",
         f"| 采样日期 | {times[0][:10] if times else '—'}{(' 至 ' + times[-1][:10]) if times and times[-1][:10] != times[0][:10] else ''}，回答时间 {times[0][11:] if times else ''}–{times[-1][11:] if times else ''} |",
         f"| 采样次数 | 每个问题在每个平台问 {'、'.join(map(str, per))} 次，共 {len(rows)} 条 |",
         f"| 账号状态 | {common.get('账号状态', '未记录')} |",
         f"| 会话 | {common.get('会话', '未记录')} |",
         f"| 联网 | " + '；'.join(f"{p}：{(env.get(p) or {}).get('联网', common.get('联网', '未记录'))}" for p in plats) + ' |',
         f"| 记忆 | " + '；'.join(f"{p}：{(env.get(p) or {}).get('记忆', common.get('记忆', '未记录'))}" for p in plats) + ' |',
         f"| 地域 | {common.get('地区', '未记录')} |",
         f"| 设备 / 客户端 | {common.get('设备', '未记录')} |",
         f"| 模型 / 档位 | " + '；'.join(f"{p}：{'、'.join(sorted(set(r['模型 / 档位'] for r in rows if r['平台'] == p)))}" for p in plats) + ' |',
         f"| 取数 | {fetched[0] if fetched else '—'}{(' 至 ' + fetched[-1]) if fetched and fetched[-1] != fetched[0] else ''} 从分享链接取回，只请求分享链接本身，没有调用任何 AI 接口 |", '',
         '## 数量核对', '',
         f"- 计划 {len(rows)} 条，取数成功 {len(ok)} 条，要人打开看 {len(bad)} 条。",
         f"- 有原始数据文件的 {len(with_raw)} / {len(rows)} 条。",
         f"- 判定了 {sum(1 for r in rows if not r['判定状态'].startswith('未判定'))} 条。"]
    if bad:
        L += ['', '取数没成功的：', '', '| 记录编号 | 平台 | 问题 | 第几次 | 原因 |', '|---|---|---|---|---|']
        L += [f"| {r['记录编号']} | {r['平台']} | {r['问题原文']} | {r['第几次']} | {r['取数状态']} |" for r in bad]

    L += ['', '## 判定结果汇总', '', '| 平台 | 条数 | ' + ' | '.join(STATUS_ORDER) + ' | 未判定 |',
          '|---|---|' + '---|' * (len(STATUS_ORDER) + 1)]
    for p in plats + ['合计']:
        rs = rows if p == '合计' else [r for r in rows if r['平台'] == p]
        c = collections.Counter(r['判定状态'] for r in rs)
        L.append(f"| {p} | {len(rs)} | " + ' | '.join(str(c.get(s, 0)) for s in STATUS_ORDER)
                 + f" | {sum(v for k, v in c.items() if k.startswith('未判定'))} |")

    L += ['', '## 判定口径', '',
          f"- 判定工具：{cfg.get('判定工具', '未记录')}",
          f"- 品牌：`{J.get('品牌', '')}`；官网域名：`{J.get('官网域名', '')}`；竞品：`{J.get('竞品', '')}`",
          '- **正文提及**：回答正文里写了品牌名。',
          '- **仅被引**：正文没写品牌名，但官网链接出现在正文里，或被 AI 用角标当作来源引用。',
          '- **仅检索到**：正文和引用都没有，品牌名或官网只出现在 AI 读过的来源、搜索词里。',
          '- **无引用数据**：正文没写品牌名，这条回答也没有任何来源数据，判断不了有没有被引用。',
          '- **未出现**：正文、引用、来源、搜索词里都没有。',
          '- 正文里的引用角标（写成「（来源：站点名）」）、搜索带出来的视频和配图说明不算正文点名。',
          '- 出现位置按回答的 Markdown 结构算：在编号或项目符号列表里，报「这一组第几项 / 共几项」；不在列表里，报出现在正文的前、中、后 1/3。',
          '- 「负面提及」要人工判断，工具不会自动标，空着表示没有人工标注。', '',
          '## 怎么复核一条', '',
          '1. 在索引里找到这条，打开「分享链接」，看当时的对话（链接可能被分享者删除或平台下线，所以另存了原始数据）。',
          '2. 打开「原始数据文件」，回答正文、来源、搜索词都在里面。',
          '3. 核对文件没被改过：macOS 终端运行 `shasum -a 256 文件路径`，Windows 运行 `certutil -hashfile 文件路径 SHA256`，结果应和「原始数据 SHA-256」一列一致。哈希核对的是这份存档文件本身有没有被改；ChatGPT 分享页数据里带每次请求的编号和耗时，重新取一次文件哈希会不同，回答内容不变。',
          '4. 回答正文和来源的整理版在 `回答存档表.csv`，可以对着原始数据看整理有没有走样。', '',
          '## 限制', '']
    L += [f'- {x}' for x in cfg.get('限制') or []]
    L += ['- 原始数据只隐去了分享者昵称、头像和账号 ID，其余原样。',
          '- 豆包分享数据里没有能对上「快速 / 思考 / 专家」的字段，豆包的模型 / 档位写「分享数据里没有」。',
          '- ChatGPT 分享数据里没有搜索词，ChatGPT 的「搜索词条数」都是 0。', '',
          '## 问题清单', '', '| 问题编号 | 问题原文 |', '|---|---|']
    L += [f'| {v} | {k} |' for k, v in qids.items()]
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    build(*sys.argv[1:])
