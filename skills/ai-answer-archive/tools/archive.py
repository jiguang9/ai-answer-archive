# -*- coding: utf-8 -*-
"""AI 回答存档工具（最小版）：一批 AI 对话分享链接 → 一张「回答存档表」CSV + 每条的原始分享数据。

只请求分享链接本身，不调用任何 AI 接口，只用 Python 标准库。目前支持豆包、ChatGPT、DeepSeek。

用法：
  python3 archive.py 链接表 输出目录
链接表可以是：
  - .md：Markdown 表格，列为「题 | AI | 第 1 次 | 第 2 次 | …」，题前面的题号（如 A1）会自动去掉
  - .csv / .tsv：表头含「问题」「第几次」「分享链接」，可选「平台」
  - .txt：一行一个链接，或「问题<Tab>第几次<Tab>链接」
"""
import csv, datetime, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'
TZ = datetime.timezone(datetime.timedelta(hours=8))  # 日期按北京时间
COLUMNS = ['问题原文', '平台', '档位', '第几次', '日期', '回答正文', '来源列表', '分享链接', '取数状态', '原始数据文件', '搜索词', '回答时间', '取数时间']
EXCEL_CELL_MAX = 32767
REDACTED = '[已隐去]'


# ---------- 通用 ----------

def http(url, data=None, headers=None, tries=3):
    h = {'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'}
    h.update(headers or {})
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.geturl(), r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):  # 链接失效或被拒，重试没用
                raise RuntimeError(f'打不开分享链接（HTTP {e.code}）')
            last = f'HTTP {e.code}'
        except Exception as e:
            last = type(e).__name__
        time.sleep(2 * (n + 1))
    raise RuntimeError(f'打不开分享链接（{last}）')


def domain(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith('www.') else host


def day(ts):
    return datetime.datetime.fromtimestamp(float(ts), TZ).strftime('%Y-%m-%d') if ts else ''


def stamp(ts):
    return datetime.datetime.fromtimestamp(float(ts), TZ).strftime('%Y-%m-%d %H:%M') if ts else ''


class Sources:
    """来源列表：按出现顺序去重，一行「标题 | 域名 | 链接」，正文里引用到的在行尾加「| 正文引用」。"""

    def __init__(self):
        self.items, self.keys = {}, {}

    def add(self, url, title='', cited=False):
        if not url or not url.startswith('http'):
            return
        p = urllib.parse.urlsplit(url)  # 同一网址参数顺序不同、末尾多个 / 算一条，存第一次出现的写法
        key = (p.netloc.lower(), p.path.rstrip('/'), tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True))))
        url = self.keys.setdefault(key, url)
        it = self.items.setdefault(url, {'title': '', 'cited': False})
        # 标题里的 | 换成全角 ｜，免得和分隔符混；原样标题看原始数据
        it['title'] = it['title'] or ' '.join((title or '').split()).replace('|', '｜')
        it['cited'] = it['cited'] or cited

    def text(self):
        return '\n'.join(f"{v['title'] or '（无标题）'} | {domain(u)} | {u}" + (' | 正文引用' if v['cited'] else '')
                         for u, v in self.items.items())


def platform_of(url):
    host = domain(url)
    if host.endswith('chatgpt.com') or host.endswith('chat.openai.com'):
        return 'ChatGPT'
    if host.endswith('doubao.com'):
        return '豆包'
    if 'deepseek' in host:
        return 'DeepSeek'
    return host


# ---------- 豆包：分享页会把域名显示成空白，改用分享接口 ----------

DB_API = ('https://www.doubao.com/im/message/share/get?version_code=20800&language=zh&device_platform=web'
          '&aid=497858&real_aid=497858&pkg_type=release_version&samantha_web=1&web_platform=browser')


def db_clean_url(u):
    """正文引用的链接带 f_link_type、flow_extra 跟踪参数，去掉后才能和参考资料对上。"""
    p = urllib.parse.urlsplit(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in ('f_link_type', 'flow_extra')]
    return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(q)))


def db_ext(m, key):
    e = m.get('ext')
    if isinstance(e, dict):
        return str(e.get(key, ''))
    r = re.search(r"'%s': '([^']*)'" % re.escape(key), e or '')  # ext 是 Python 字典的字符串形式
    return r.group(1) if r else ''


def db_cards(tags, src):
    """text_tags 里 type 102 的 search_reference 卡片（任务模式把参考资料放在思考过程里）。"""
    for t in tags or []:
        if t.get('type') != 102:
            continue
        info = json.loads(t['tag_info']) if isinstance(t.get('tag_info'), str) else (t.get('tag_info') or {})
        if info.get('card_type') not in (None, 'search_reference'):
            continue
        try:
            v = json.loads(info.get('value') or '{}')
        except ValueError:
            continue
        for c in v.get('data') or []:
            tc = c.get('text_card') or {}
            src.add(tc.get('url'), tc.get('title'))


def db_render(c, src, notes):
    """把一条回答的 content 还原成页面上看到的正文：引用角标按 UTF-8 字节位置插回去。"""
    if isinstance(c, list):  # 任务模式的分块格式
        out = []
        for b in c:
            if isinstance(b, str):
                out.append(b); continue
            cc = b.get('content') or {}
            if cc.get('text_block', {}).get('text'):
                out.append(cc['text_block']['text'])
            elif 'artifact_block' in cc or 'file_operation_block' in cc:
                notes.add('回答里有生成的文件，分享数据不含完整文件')
        return '\n\n'.join(out)
    for r in c.get('search_references') or []:
        for kind, tc in r.items():  # text_card 是网页，video_card 是抖音视频
            if not isinstance(tc, dict):
                continue
            u = tc.get('url') or tc.get('main_site_url') or tc.get('host_page_url')
            title = tc.get('title') or ('[视频] ' + (tc.get('video_captions') or '').split('\n')[0][:80] if kind == 'video_card' else '')
            src.add(u, title)
    text = c.get('text') or ''
    ins = []
    for t in c.get('text_tags') or []:
        info = json.loads(t['tag_info']) if isinstance(t.get('tag_info'), str) else (t.get('tag_info') or {})
        if (info.get('insert_text') or '').startswith('<data-inline'):  # 正文里插的图片组
            m = re.search(r'value="([^"]*)"', info['insert_text'])
            try:
                v = json.loads(html.unescape(m.group(1)))
                caps = [f"{im.get('img_caption') or '无说明'}（{im.get('source_app_name') or '来源不明'}）" for im in v.get('images') or []]
                ins.append((info.get('pos', 0), f"\n[图片：{'、'.join(caps)}]\n" if caps else ''))
            except (AttributeError, ValueError):
                ins.append((info.get('pos', 0), '\n[图片，见原始数据]\n'))
        elif t.get('type') == 2 and info.get('insert_text'):
            u = db_clean_url(info.get('url', ''))
            src.add(u, info.get('title'), cited=True)
            for p in info.get('pos') or []:
                # 角标统一写成「（来源：站点名）」，三家一样，判定时好剔掉，免得「(企业微信)」这种角标被当成正文点名
                name = info.get('website_name') or info['insert_text'].strip('()（）')
                ins.append((p, f'（来源：{name}）'))
        elif t.get('type') == 102 and info.get('card_type') == 'video-group':
            try:
                v = json.loads(info.get('value') or '{}')
                ins.append((info.get('pos', 0), f"\n[视频卡片：{' '.join(v.get('video_title', '').split())}]\n"))  # 标题里的换行压成空格
            except ValueError:
                pass
    b = text.encode('utf-8')
    for p, s in sorted(ins, key=lambda x: -x[0]):
        if 0 <= p <= len(b):
            b = b[:p] + s.encode('utf-8') + b[p:]
    return b.decode('utf-8', 'replace')


def fetch_doubao(url):
    m = re.search(r'/thread/([A-Za-z0-9]+)', url)
    if not m:  # 短链之类，先跟一次跳转
        url2, _ = http(url)
        m = re.search(r'/thread/([A-Za-z0-9]+)', url2)
        if not m:
            raise RuntimeError('认不出豆包分享 ID')
    sid = m.group(1)
    _, body = http(DB_API, json.dumps({'share_id': sid}).encode(),
                   {'Content-Type': 'application/json', 'Referer': f'https://www.doubao.com/thread/{sid}'})
    j = json.loads(body)
    raw = redact_doubao(body, j)
    if j.get('code') != 0:
        raise Fetched(raw, f"分享接口返回错误（{j.get('code')} {j.get('msg', '')}）".strip())
    ms = (j.get('data') or {}).get('message_snapshot', {}).get('message_list') or []
    users = [x for x in ms if x.get('user_type') == 1]
    bots = [x for x in ms if x.get('user_type') == 2]
    if not bots:
        raise Fetched(raw, '分享数据里没找到回答')
    notes, src = Notes(), Sources()
    if len(users) > 1:
        notes.add(f'多轮对话（{len(users)} 轮），只存了最后一轮')
    bot = bots[-1]
    c = json.loads(bot['content'])
    if isinstance(c, dict) and set(c) == {'text'} and not c['text']:
        notes.add('回答正文为空')
    answer = db_render(c, src, notes)
    if bot.get('thinking_content'):
        try:
            for th in json.loads(bot['thinking_content']).get('thinks') or []:
                db_cards(th.get('text_tags'), src)
        except ValueError:
            pass
    if db_ext(bot, 'is_general_task') == '1':
        notes.add('豆包任务模式，生成的文件或表格不在分享数据里，正文只有说明')
    q = ''
    if users:
        uc = json.loads(users[-1]['content'])
        q = uc.get('text', '') if isinstance(uc, dict) else ''
    queries = ((c.get('multi_reference') or {}).get('search_queries') or []) if isinstance(c, dict) else []
    return dict(question=q, tier='', date=day(bot.get('create_time')), answered=stamp(bot.get('create_time')), answer=answer,
                sources=src.text(), queries=queries, notes=notes, raw=raw, raw_ext='json')


def redact_doubao(body, j):
    """原始数据照存，只把分享者昵称、头像和账号 ID 换成 [已隐去]。"""
    vals = set()
    info = (j.get('data') or {}).get('share_info') or {}
    u = info.get('user') or {}
    if u.get('nick_name'):
        vals.add(u['nick_name'])
    for k in ('uri', 'tiny_url', 'origin_url'):
        if (u.get('image') or {}).get(k):
            vals.add(u['image'][k])
    for m in (j.get('data') or {}).get('message_snapshot', {}).get('message_list') or []:
        if m.get('user_type') == 1:
            for k in ('sender_id', 'sec_sender'):
                if m.get(k):
                    vals.add(str(m[k]))
    vals.update(re.findall(r'creatorID\\*":\\*"(\d+)', body))
    raw = json.dumps(j, ensure_ascii=False, indent=1)
    for v in sorted(vals, key=len, reverse=True):
        if len(v) >= 2:
            raw = raw.replace(v, REDACTED)
    return raw


# ---------- ChatGPT：分享页不登录是空白，回答在页面里的 turbo-stream 数据 ----------

def turbo_decode(payload):
    """react-router 的 turbo-stream：扁平数组，对象写成 {"_键下标": 值下标}，负数是 null/undefined。"""
    arr = json.loads(payload.split('\n', 1)[0])
    def dec(i, seen=()):
        if not isinstance(i, int) or i < 0:
            return None
        if i in seen:
            return None
        v = arr[i]
        if isinstance(v, dict):
            return {arr[int(k[1:])]: dec(x, seen + (i,)) for k, x in v.items()}
        if isinstance(v, list):
            if v and isinstance(v[0], str) and len(v[0]) == 1:  # 日期、Promise 等特殊值，用不上
                return None
            return [dec(x, seen + (i,)) for x in v]
        return v
    return dec(0)


def find_messages(o, out):
    if isinstance(o, dict):
        if isinstance(o.get('author'), dict) and isinstance(o.get('content'), dict) and o.get('id'):
            out.setdefault(o['id'], o)
        for v in o.values():
            find_messages(v, out)
    elif isinstance(o, list):
        for v in o:
            find_messages(v, out)


def gpt_tokens(text):
    """切成文字和标签。标签属性里可能有 {()=>…} 和引号，不能简单找下一个 >。"""
    i, n, buf = 0, len(text), []
    while i < n:
        if text[i] == '<' and i + 1 < n and (text[i + 1].isalpha() or text[i + 1] == '/'):
            j, depth, quote = i + 1, 0, None
            while j < n:
                ch = text[j]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in '"\'`':
                    quote = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                elif ch == '>' and depth <= 0:
                    break
                j += 1
            raw = text[i:j + 1]
            m = re.match(r'<(/?)([A-Za-z][\w-]*)', raw)
            if m and j < n:
                if buf:
                    yield ('text', ''.join(buf)); buf = []
                yield ('tag', m.group(2), bool(m.group(1)), raw.endswith('/>'), raw)
                i = j + 1
                continue
        buf.append(text[i])
        i += 1
    if buf:
        yield ('text', ''.join(buf))


def gpt_render(text, refs):
    """把回答里的界面标记（<Entity> <Link> <Cite> <box> <form> …）还原成可读的 Markdown。"""
    def cite(ids):
        names = []
        for i in ids:
            e = refs.get(re.sub(r'^turn\d+', '', i))
            if e:
                n = e.get('attribution') or domain(e.get('url', ''))
                if n not in names:
                    names.append(n)
        return f"（来源：{'、'.join(names)}）" if names else ''
    def attr(raw, name):
        m = re.search(r'\b%s="([^"]*)"' % name, raw)
        return m.group(1) if m else ''
    def each(m):  # 表单里的多选项写成 {#each opts as o}…{o.name}…{/each}，选项定义在 {@body const opts=[…]}
        src_, var, body = m.group(1), m.group(2), m.group(3)
        arr = src_ if src_.startswith('[') else defs.get(src_)  # 也可能直接写数组：{#each [{…}] as f}
        if arr is None:
            return '\n[选项列表，见原始数据]\n'
        k = re.search(r'>\s*\{%s(?:\.(\w+))?\}\s*<' % var, body) or re.search(r'\{%s(?:\.(\w+))?\}' % var, body)
        items = re.findall(r'\b%s\s*:\s*"([^"]*)"' % k.group(1), arr) if k and k.group(1) else re.findall(r'"([^"]*)"', arr)
        return '\n' + '\n'.join(items) + '\n'
    defs = dict(re.findall(r'(?m)^\s*\{@body const (\w+)\s*=\s*(\[.*\])\}\s*$', text))
    t = re.sub(r'\{#each\s+(\[.*?\]|\w+)\s+as\s+(\w+)\}(.*?)\{/each\}', each, text, flags=re.S)
    t = re.sub(r'\{@body[^\n]*\}\n?', '', t)
    t = re.sub(r'cite(.*?)', lambda m: cite(m.group(1).split('')), t)  # 老格式角标
    t = re.sub(r'[^]*', '', t)
    out, depth, link, links = [], 0, None, []
    for tok in gpt_tokens(t):
        if tok[0] == 'text':
            s = tok[1]
            if depth > 0:  # 卡片、表单里的缩进是排版用的，去掉
                s = re.sub(r'\n[ \t]+', '\n', s)
            (link if link is not None else out).append(s)
            continue
        _, name, closing, selfclose, raw = tok
        if name == 'Entity':
            s = attr(raw, 'value')
        elif name == 'Cite':
            s = cite(re.findall(r'"([^"]+)"', raw))
        elif name == 'Link' and selfclose:  # <Link url="…" title="官网"/>
            u = attr(raw, 'url'); label = attr(raw, 'title') or u
            links.append((u, label)); s = f'[{label}]({u})'
        elif name == 'Link' and not closing:
            link = []; link_url = attr(raw, 'url'); continue
        elif name == 'Link' and closing and link is not None:
            label = ''.join(link).strip()
            links.append((link_url, label))
            s, link = f'[{label}]({link_url})', None
        elif name == 'Chart':
            s = gpt_chart(raw)
        else:
            if not selfclose:
                depth = max(0, depth + (-1 if closing else 1))
            s = ''
        (link if link is not None else out).append(s)
    if link:  # 没闭合的链接，文字别丢
        out.extend(link)
    t = ''.join(out)
    t = re.sub(r'(?m)^[ \t]+$', '', t)
    return re.sub(r'\n{3,}', '\n\n', t).strip(), links


def gpt_chart(raw):
    """<Chart content={{…}}/> 页面上是一张图，存档表写成一行文字：[图表] 系列：项 值；…（脚注）。"""
    m = re.search(r'content=\{(\{.*\})\}\s*/?>$', raw, re.S)
    try:
        c = json.loads(m.group(1))
    except (AttributeError, ValueError):
        return '\n[图表，见原始数据]\n'
    x = c.get('xKey')
    parts = []
    for s in c.get('series') or []:
        pre, suf = s.get('valuePrefix', ''), s.get('valueSuffix', '')
        vals = '；'.join(f"{d.get(x)} {pre}{d.get(s.get('dataKey'))}{suf}" for d in c.get('data') or [])
        parts.append(f"{s.get('label') or s.get('dataKey')}：{vals}")
    foot = ((c.get('meta') or {}).get('footer') or '')
    return '\n[图表] ' + ' / '.join(parts) + (f'（{foot}）' if foot else '') + '\n'


def fetch_chatgpt(url):
    _, html = http(url)
    chunks = re.findall(r'enqueue\(("(?:[^"\\]|\\.)*")\)', html)
    if not chunks:
        raise RuntimeError('分享页里没找到对话数据（可能链接失效或页面改版）')
    payload = ''.join(json.loads(c) for c in chunks)
    decoded = turbo_decode(payload)
    raw = json.dumps({'说明': 'ChatGPT 分享页源码里的 turbo-stream 原文（payload）和按下标还原后的结构（decoded）',
                      'payload': payload, 'decoded': decoded}, ensure_ascii=False, indent=1)
    msgs = {}
    find_messages(decoded, msgs)
    msgs = sorted(msgs.values(), key=lambda m: m.get('create_time') or 0)
    users = [m for m in msgs if m['author'].get('role') == 'user']
    bots = [m for m in msgs if m['author'].get('role') == 'assistant' and m['content'].get('content_type') == 'text'
            and ''.join(p for p in m['content'].get('parts') or [] if isinstance(p, str)).strip()]
    if not bots:
        raise Fetched(raw, '分享数据里没找到回答')
    notes, src = Notes(), Sources()
    if len(users) > 1:
        notes.add(f'多轮对话（{len(users)} 轮），只存了最后一轮')
    bot = bots[-1]
    md = bot.get('metadata') or {}
    if bot.get('status') not in (None, 'finished_successfully') or md.get('is_complete') is False:
        notes.add(f"回答没有正常结束（{bot.get('status')}）")
    if any(not isinstance(p, str) for p in bot['content'].get('parts') or []):
        notes.add('回答里有图片或文件，存档表只有文字')
    refs = {}
    for g in md.get('search_result_groups') or []:
        for e in g.get('entries') or []:
            rid = e.get('ref_id') or {}
            refs[f"{rid.get('ref_type')}{rid.get('ref_index')}"] = e
            src.add(e.get('url'), e.get('title'))
    text = ''.join(p for p in bot['content']['parts'] if isinstance(p, str))
    for ids in re.findall(r'<Cite\s+refs=\{\[(.*?)\]\}', text) + re.findall(r'cite(.*?)', text):
        for i in re.findall(r'turn\d+(\w+?\d+)', ids):
            e = refs.get(i)
            if e:
                src.add(e.get('url'), e.get('title'), cited=True)
    for cr in md.get('content_references') or []:  # 老格式：正文角标对应的网页
        for it in cr.get('items') or []:
            src.add(it.get('url'), it.get('title'), cited=True)
    answer, links = gpt_render(text, refs)
    for u, label in links:  # 正文里直接给的链接（官网之类）
        src.add(u, label, cited=True)
    q = ''
    if users:
        q = ''.join(p for p in users[-1]['content'].get('parts') or [] if isinstance(p, str))
    tier = md.get('resolved_model_slug') or md.get('model_slug') or ''
    return dict(question=q, tier=tier, date=day(bot.get('create_time')), answered=stamp(bot.get('create_time')), answer=answer,
                sources=src.text(), notes=notes, raw=raw, raw_ext='json')


# ---------- DeepSeek：分享数据接口直接给 JSON ----------

def fetch_deepseek(url):
    m = re.search(r'/share/([A-Za-z0-9]+)', url)
    if not m:
        raise RuntimeError('认不出 DeepSeek 分享 ID')
    _, body = http(f'https://chat.deepseek.com/api/v0/share/content?share_id={m.group(1)}',
                   headers={'Referer': url})
    j = json.loads(body)
    raw = json.dumps(j, ensure_ascii=False, indent=1)  # 分享数据里没有分享者信息，原样存
    data = j.get('data') or {}
    if j.get('code') != 0 or data.get('biz_code') != 0:
        raise Fetched(raw, f"分享接口返回错误（{j.get('code')}/{data.get('biz_code')} {j.get('msg') or data.get('biz_msg') or ''}）".strip())
    biz = data.get('biz_data') or {}
    msgs = biz.get('messages') or []
    users = [x for x in msgs if x.get('role') == 'USER']
    bots = [x for x in msgs if x.get('role') == 'ASSISTANT']
    if not bots:
        raise Fetched(raw, '分享数据里没找到回答')
    notes, src = Notes(), Sources()
    if len(users) > 1:
        notes.add(f'多轮对话（{len(users)} 轮），只存了最后一轮')
    bot = bots[-1]
    if bot.get('status') != 'FINISHED':
        notes.add(f"回答没有正常结束（{bot.get('status')}）")
    frags = bot.get('fragments') or []
    by_idx = {}
    for f in frags:  # SEARCH 是读过的网页；THINK 是思考过程、TIP 是"AI 生成仅供参考"提示，都不算正文
        if f.get('type') == 'SEARCH':
            for r in f.get('results') or []:
                by_idx[r.get('cite_index')] = r
                src.add(r.get('url'), r.get('title'))
    text = ''.join(f.get('content') or '' for f in frags if f.get('type') == 'RESPONSE')
    def cite(m):  # 连着的 [citation:3][citation:7] 合成一个「（来源：站点名、…）」
        names = []
        for i in re.findall(r'\d+', m.group(0)):
            r = by_idx.get(int(i))
            if r:
                src.add(r.get('url'), r.get('title'), cited=True)
                n = r.get('site_name') or domain(r.get('url', ''))
                if n not in names:
                    names.append(n)
        return f"（来源：{'、'.join(names)}）" if names else ''
    answer = re.sub(r'(?:\[citation:\d+\])+', cite, text).strip()
    if not answer:
        notes.add('回答正文为空')
    q = ''
    if users:
        q = ''.join(f.get('content') or '' for f in users[-1].get('fragments') or [] if f.get('type') == 'REQUEST')
        if users[-1].get('files'):
            notes.add('提问带了附件，附件内容不在分享数据里')
    tier = biz.get('model_type') or ''
    if bot.get('thinking_enabled'):
        tier = (tier + ' · 深度思考').strip(' ·')
    queries = [x.get('query', '') for f in frags if f.get('type') == 'SEARCH' for x in f.get('queries') or []]
    return dict(question=q, tier=tier, date=day(bot.get('inserted_at')), answered=stamp(bot.get('inserted_at')), answer=answer,
                sources=src.text(), queries=queries, notes=notes, raw=raw, raw_ext='json')


# ---------- 汇总 ----------

class Notes(list):
    def add(self, s):
        if s not in self:
            self.append(s)


class Fetched(Exception):
    """拿到了原始数据，但取不出回答：原始数据照样存。"""
    def __init__(self, raw, reason):
        super().__init__(reason)
        self.raw = raw


FETCHERS = {'豆包': fetch_doubao, 'ChatGPT': fetch_chatgpt, 'DeepSeek': fetch_deepseek}


def read_links(path):
    rows = []
    text = open(path, encoding='utf-8-sig').read()
    if path.endswith('.md'):
        head = None
        for line in text.split('\n'):
            if not line.startswith('|'):
                head = None; continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if re.match(r'^:?-+', cells[0] or '-'):
                continue
            if head is None:
                head = cells; continue
            urls = [(h, c) for h, c in zip(head, cells) if c.startswith('http')]
            if not urls:
                continue
            q = re.sub(r'^[A-Za-z]\d{1,3}\s+', '', cells[0])  # 去掉题号前缀
            for h, u in urls:
                n = re.search(r'\d+', h)
                rows.append(dict(question=q, n=n.group() if n else '', url=u))
    elif path.endswith(('.csv', '.tsv')):
        for r in csv.DictReader(text.splitlines(), delimiter='\t' if path.endswith('.tsv') else ','):
            g = lambda *ks: next((r[k].strip() for k in ks if r.get(k)), '')
            rows.append(dict(question=g('问题原文', '问题'), n=g('第几次', '次'), url=g('分享链接', '链接'), platform=g('平台')))
    else:
        for line in text.splitlines():
            parts = [p.strip() for p in line.split('\t')]
            if parts and parts[-1].startswith('http'):
                rows.append(dict(question=parts[0] if len(parts) > 1 else '', n=parts[1] if len(parts) > 2 else '', url=parts[-1]))
    return rows


def run(links_path, out_dir):
    raw_dir = os.path.join(out_dir, '原始数据')
    os.makedirs(raw_dir, exist_ok=True)
    rows = read_links(links_path)
    out = []
    for i, r in enumerate(rows, 1):
        url = r['url']
        plat = r.get('platform') or platform_of(url)
        row = {'问题原文': r['question'], '平台': plat, '档位': '', '第几次': r['n'], '日期': '', '回答正文': '',
               '来源列表': '', '分享链接': url, '取数状态': '', '原始数据文件': '', '搜索词': '', '回答时间': '',
               '取数时间': datetime.datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}
        sid = re.sub(r'[^A-Za-z0-9_-]', '', url.rstrip('/').rsplit('/', 1)[-1])[:40]
        raw_name = f'{i:03d}-{plat}-{sid}.json'
        fn = FETCHERS.get(plat)
        if not fn:
            row['取数状态'] = f'要人打开看：暂不支持{plat}'
        else:
            try:
                d = fn(url)
                open(os.path.join(raw_dir, raw_name), 'w', encoding='utf-8').write(d['raw'])
                row['原始数据文件'] = f'原始数据/{raw_name}'
                notes = d['notes']
                if r['question'] and d['question'] and norm(r['question']) != norm(d['question']):
                    notes.add('分享里的提问和链接表里的问题对不上')
                if len(d['answer']) > EXCEL_CELL_MAX:
                    notes.add(f'正文 {len(d["answer"])} 字，超过 Excel 单格上限，用表格软件打开会被截，完整原文看原始数据文件')
                row.update({'问题原文': r['question'] or d['question'], '档位': d['tier'], '日期': d['date'],
                            '回答正文': d['answer'], '来源列表': d['sources'], '搜索词': '\n'.join(q for q in d.get('queries') or [] if q), '回答时间': d.get('answered', ''),
                            '取数状态': ('要人打开看：' + '；'.join(notes)) if notes else '成功'})
            except Fetched as e:
                open(os.path.join(raw_dir, raw_name), 'w', encoding='utf-8').write(e.raw)
                row['原始数据文件'] = f'原始数据/{raw_name}'
                row['取数状态'] = f'要人打开看：{e}'
            except Exception as e:
                row['取数状态'] = f'要人打开看：{e}'
            time.sleep(1)
        out.append(row)
        print(f"{i:>3} {plat:<8} {row['取数状态'][:60]}  正文 {len(row['回答正文'])} 字  来源 {row['来源列表'].count(chr(10)) + 1 if row['来源列表'] else 0} 条")
    with open(os.path.join(out_dir, '回答存档表.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out)
    ok = sum(1 for r in out if r['取数状态'] == '成功')
    print(f'共 {len(out)} 条：成功 {ok}，要人打开看 {len(out) - ok}。存档表：{os.path.join(out_dir, "回答存档表.csv")}')


def norm(s):
    return re.sub(r'\s+', '', s)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    run(*sys.argv[1:])
