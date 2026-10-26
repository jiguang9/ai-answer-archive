# -*- coding: utf-8 -*-
# 不联网的单元测试：python3 -m unittest
import csv, hashlib, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'ai-answer-archive', 'tools'))
import archive as A  # noqa: E402
import build_index as B  # noqa: E402


class TurboDecode(unittest.TestCase):
    def test_objects_point_at_keys_and_values_by_index(self):
        payload = json.dumps([{'_1': 2}, 'post', {'_3': 4, '_5': -5}, 'text', '你好', 'gone']) + '\nP9:[{}]'
        self.assertEqual(A.turbo_decode(payload), {'post': {'text': '你好', 'gone': None}})


class GptRender(unittest.TestCase):
    refs = {'search6': {'attribution': 'example.com', 'url': 'https://example.com/a'}}

    def test_entities_links_and_cites(self):
        text = ('推荐 <Entity category="software" value="飞书" disambig="x"/>。'
                '<Link url="https://a.com">官网 <icon name="arrow"/></Link>'
                '<Link url="https://b.com" title="B 官网"/>'
                '参考<Cite refs={["turn123search6"]}/>')
        out, links = A.gpt_render(text, self.refs)
        self.assertEqual(out, '推荐 飞书。[官网](https://a.com)[B 官网](https://b.com)参考（来源：example.com）')
        self.assertEqual(links, [('https://a.com', '官网'), ('https://b.com', 'B 官网')])

    def test_self_closing_link_does_not_swallow_the_rest(self):
        out, _ = A.gpt_render('前<Link url="https://a.com" title="官网"/>后面还有很多字', {})
        self.assertTrue(out.endswith('后面还有很多字'))

    def test_form_with_arrow_function_and_each_loop(self):
        text = ('{@body const opts=[{id:"a",name:"甲"},{id:"b",name:"乙"}]}\n'
                '<form onSubmit={()=>GenUI.issueNewTurn("x")}>\n  <label>1. 选哪个？</label>\n'
                '  {#each opts as o}\n    <checkbox key={o.id}>{o.name}</checkbox>\n  {/each}\n</form>')
        out, _ = A.gpt_render(text, {})
        self.assertNotIn('GenUI', out)
        self.assertNotIn('{', out)
        self.assertEqual([l for l in out.split('\n') if l], ['1. 选哪个？', '甲', '乙'])

    def test_chart_becomes_one_line(self):
        raw = '<Chart content={{"xKey":"p","series":[{"dataKey":"c","label":"年费","valuePrefix":"¥"}],"data":[{"p":"A","c":1},{"p":"B","c":2}],"meta":{"footer":"注"}}}/>'
        out, _ = A.gpt_render(raw, {})
        self.assertEqual(out, '[图表] 年费：A ¥1；B ¥2（注）')


class DoubaoRender(unittest.TestCase):
    def test_cites_go_back_at_utf8_byte_positions(self):
        text = '飞书适合文档。钉钉适合审批。'
        pos = len('飞书适合文档'.encode('utf-8'))
        tag = {'type': 2, 'tag_info': json.dumps({'insert_text': '(36氪)', 'website_name': '36氪', 'pos': [pos],
                                                  'url': 'https://36kr.com/p/1?f_link_type=x&flow_extra=y', 'title': '文章'})}
        video = {'type': 102, 'tag_info': json.dumps({'card_type': 'video-group', 'pos': len(text.encode('utf-8')),
                                                     'value': json.dumps({'video_title': '标题\n第二行'})})}
        src, notes = A.Sources(), A.Notes()
        out = A.db_render({'text': text, 'text_tags': [tag, video], 'search_references': [
            {'text_card': {'url': 'https://36kr.com/p/1', 'title': '文章'}},
            {'video_card': {'main_site_url': 'https://v.example.com/1', 'video_captions': '视频说明\n更多'}}]}, src, notes)
        self.assertEqual(out, '飞书适合文档（来源：36氪）。钉钉适合审批。\n[视频卡片：标题 第二行]\n')
        self.assertEqual(src.text().split('\n'), ['文章 | 36kr.com | https://36kr.com/p/1 | 正文引用',
                                                  '[视频] 视频说明 | v.example.com | https://v.example.com/1'])


class SourcesList(unittest.TestCase):
    def test_same_page_counts_once_and_pipes_in_titles_are_escaped(self):
        s = A.Sources()
        s.add('https://a.com/x?b=2&a=1', '标题 | 站点')
        s.add('https://a.com/x/?a=1&b=2', '另一个标题', cited=True)
        s.add('https://a.com/x?a=1&b=2#:~:text=片段', '')
        self.assertEqual(s.text(), '标题 ｜ 站点 | a.com | https://a.com/x?b=2&a=1 | 正文引用')


class ReadLinks(unittest.TestCase):
    def test_markdown_table_strips_question_numbers(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write('| 题 | AI | 第 1 次 | 第 2 次 |\n|---|---|---|---|\n| A1 协作软件哪个好 | 豆包 | https://www.doubao.com/thread/x1 | https://www.doubao.com/thread/x2 |\n')
        rows = A.read_links(f.name)
        os.unlink(f.name)
        self.assertEqual([(r['question'], r['n']) for r in rows], [('协作软件哪个好', '1'), ('协作软件哪个好', '2')])
        self.assertEqual(A.platform_of(rows[0]['url']), '豆包')


class BuildIndex(unittest.TestCase):
    def test_joins_by_share_link_and_hashes_raw_files(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, '原始数据'))
        raw = os.path.join(d, '原始数据', '001-DeepSeek-abc.json')
        open(raw, 'w', encoding='utf-8').write('{"x": 1}')
        with open(os.path.join(d, '回答存档表.csv'), 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, A.COLUMNS)
            w.writeheader()
            w.writerow({'问题原文': '问题一', '平台': 'DeepSeek', '档位': 'default', '第几次': '1', '日期': '2026-10-24',
                        '回答正文': '正文', '来源列表': 'T | a.com | https://a.com | 正文引用\nU | b.com | https://b.com',
                        '分享链接': 'https://chat.deepseek.com/share/abc', '取数状态': '成功',
                        '原始数据文件': '原始数据/001-DeepSeek-abc.json', '搜索词': 'q1\nq2', '回答时间': '2026-10-24 10:00',
                        '取数时间': '2026-10-26 09:00'})
            w.writerow({'问题原文': '问题一', '平台': 'ChatGPT', '第几次': '1', '分享链接': 'https://chatgpt.com/s/t_1',
                        '取数状态': '要人打开看：打不开分享链接（HTTP 403）'})
        judge = os.path.join(d, '判定结果.csv')
        with open(judge, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['状态', '品牌提及', '仅被引', '检索层命中', '出现位置', '竞品出现顺序', '被引官网链接', '负面提及', '分享链接'])
            w.writerow(['正文提及', '是', '否', '是', '正文前1/3（约 5%）', '1. 竞品', '', '', 'https://chat.deepseek.com/share/abc'])
        cfg = os.path.join(d, '索引配置.json')
        json.dump({'项目': '测试', '环境': {'通用': {'会话': '新对话'}, 'DeepSeek': {'联网': '开'}}}, open(cfg, 'w', encoding='utf-8'), ensure_ascii=False)
        B.build(d, judge, cfg)
        rows = B.read_csv(os.path.join(d, '原始记录索引.csv'))
        self.assertEqual([r['判定状态'] for r in rows], ['正文提及', '未判定（取数失败）'])
        self.assertEqual(rows[0]['原始数据 SHA-256'], hashlib.sha256(b'{"x": 1}').hexdigest())
        self.assertEqual((rows[0]['来源条数'], rows[0]['正文引用条数'], rows[0]['搜索词条数']), ('2', '1', '2'))
        self.assertEqual((rows[0]['联网'], rows[0]['会话'], rows[0]['记忆']), ('开', '新对话', '未记录'))
        self.assertEqual(rows[1]['原始数据 SHA-256'], '没有原始数据')
        note = open(os.path.join(d, '原始记录索引说明.md'), encoding='utf-8').read()
        self.assertIn('取数成功 1 条，要人打开看 1 条', note)


if __name__ == '__main__':
    unittest.main()
