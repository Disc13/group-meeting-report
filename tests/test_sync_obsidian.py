"""Exercise cross-meeting accumulation and preservation in an isolated vault."""
import contextlib,io,json,re,shutil,tempfile,unittest
from pathlib import Path
from sync_obsidian import plan_export,publish


def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')


class VaultExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.obsidian-test-',dir=str(Path(__file__).resolve().parent))
        self.base=Path(self.temp.name).resolve();self.vault=self.base/'vault'
        (self.vault/'Agent').mkdir(parents=True);(self.vault/'AGENTS.md').write_text('rules')
        (self.vault/'Agent/Agent 维护.md').write_text('rules',encoding='utf8')
        (self.vault/'知识/组会').mkdir(parents=True)
        (self.vault/'知识/组会/组会.md').write_text('# 组会\n\n既有内容\n',encoding='utf8')
        self.config={'vault':str(self.vault),'meeting_dir':'知识/组会','people_dir':'知识/人物','assets_dir':'附件/组会'}

    def tearDown(self):
        assert self.base.parent==Path(__file__).resolve().parent
        self.temp.cleanup()

    def meeting(self,date):
        root=self.base/date;(root/'captures').mkdir(parents=True);(root/'sections').mkdir()
        (root/'组会报告.md').write_text('# 原标题\n\n## 一、测试同学\n\n\\[x=1\\]\n',encoding='utf8')
        (root/'transcript.md').write_text('# 字稿\n',encoding='utf8')
        for name in ('comparison.json','cursor-status.json'):dump(root/'captures'/name,{})
        dump(root/'meeting-structure.json',{'presentations':[{'id':'test','presenter':'测试同学'}]})
        dump(root/'workflow.json',{})
        # These are inert attachment stand-ins, not authored documents.
        (root/'source.pdf').write_bytes(b'fixture-pdf');(root/'source.pptx').write_bytes(b'fixture-pptx')
        dump(root/'sections/manifest.json',{'presentations':[{'id':'test','presenter':'测试同学','title':'研究',
             'page_count':1,'pdf':'source.pdf','pptx':'source.pptx'}]})
        dump(root/'人物汇报提要.json',{'meeting_id':date,'meeting_date':date,'updated':'2000-01-04',
             'title':date+' 小组会报告','presenters':[{'name':'测试同学','presentation_id':'test',
             'report_heading':'一、测试同学','title':'研究','kind':'研究进展','directions':['控制'],
             'summary':'概要','progress':'进行中','discussions':['问题与回应'],'follow_up':['计划'],
             'evidence_window':'00:01:00–00:02:00'}]})
        return root

    def sync(self,root):
        vault,files,event=plan_export(root,self.config)
        with contextlib.redirect_stdout(io.StringIO()):publish(root,vault,files,event,True)
        return json.loads((root/'obsidian-export.json').read_text('utf8'))

    def test_repeat_is_idempotent_and_math_uses_obsidian_delimiters(self):
        root=self.meeting('2000-01-01');self.sync(root)
        report=(self.vault/'知识/组会/2000-01-01 小组会报告.md').read_text('utf8')
        self.assertIn('$$x=1$$',report);self.assertNotIn('\\[',report)
        transcript=self.vault/'附件/组会/2000-01-01/逐字稿.md'
        self.assertNotIn(b'\r',transcript.read_bytes())
        self.assertEqual(self.sync(root)['changed_files'],0)

    def test_new_vault_does_not_need_private_maintenance_notes_or_existing_index(self):
        root=self.meeting('2000-01-01');self.config['vault']=str(self.base/'clean-vault')
        self.vault=Path(self.config['vault']);self.vault.mkdir()
        self.sync(root)
        self.assertTrue((self.vault/'知识/组会/组会.md').is_file())
        self.assertTrue((self.vault/'知识/人物/测试同学.md').is_file())
        self.assertFalse((self.vault/'Agent').exists())

    def test_two_meetings_accumulate_and_reexport_does_not_duplicate(self):
        first=self.meeting('2000-01-01');self.sync(first)
        second=self.meeting('2000-01-02');self.sync(second);self.sync(first)
        profile=(self.vault/'知识/人物/测试同学.md').read_text('utf8')
        self.assertEqual(profile.count('### 2000-01-01 研究'),1)
        self.assertEqual(profile.count('### 2000-01-02 研究'),1)
        self.assertIn('覆盖2次汇报',profile)

    def test_manual_notes_survive_and_edited_generated_text_blocks_update(self):
        first=self.meeting('2000-01-01');self.sync(first)
        path=self.vault/'知识/人物/测试同学.md'
        path.write_text(path.read_text('utf8')+'\n## 本人补充\n保留此段\n',encoding='utf8')
        second=self.meeting('2000-01-02');self.sync(second)
        self.assertIn('保留此段',path.read_text('utf8'))
        path.write_text(path.read_text('utf8').replace('概要','手工修改过的概要',1),encoding='utf8')
        third=self.meeting('2000-01-03')
        with self.assertRaisesRegex(ValueError,'Managed content edited'):self.sync(third)
        self.assertFalse((self.vault/'知识/组会/2000-01-03 小组会报告.md').exists())

    def test_another_recording_cannot_replace_the_same_date(self):
        first=self.meeting('2000-01-01')
        dump(first/'workflow.json',{'source':'https://meeting.tencent.com/cw/first'})
        self.sync(first)
        another=self.base/'another-recording';shutil.copytree(first,another)
        dump(another/'workflow.json',{'source':'https://meeting.tencent.com/cw/second'})
        report=self.vault/'知识/组会/2000-01-01 小组会报告.md';before=report.read_bytes()
        with self.assertRaisesRegex(ValueError,'another recording'):self.sync(another)
        self.assertEqual(report.read_bytes(),before)

    def test_images_only_publish_without_pdf_pptx_or_reference_comparison(self):
        from PIL import Image
        root=self.meeting('2000-01-01')
        (root/'captures/comparison.json').unlink()
        image=root/'output/images/test/001-capture-001.png';image.parent.mkdir(parents=True)
        Image.new('RGB',(20,12),'green').save(str(image))
        dump(root/'sections/test.json',{'pages':[{'page_id':'capture-001','export_image':'output/images/test/'+image.name}]})
        dump(root/'sections/manifest.json',{'presentations':[{'id':'test','presenter':'测试同学','title':'研究',
             'page_count':1,'image_index':'output/images/test/index.md'}]})
        report=root/'组会报告.md'
        report.write_text(report.read_text('utf8')+'\n![幻灯片](output/images/test/'+image.name+')\n',encoding='utf8')
        self.sync(root)
        self.assertEqual((self.vault/'附件/组会/2000-01-01/图'/image.name).read_bytes(),image.read_bytes())
        gallery=(self.vault/'附件/组会/2000-01-01/图片/test.md').read_text('utf8')
        report=(self.vault/'知识/组会/2000-01-01 小组会报告.md').read_text('utf8')
        for text in (report,gallery):
            embeds=re.findall(r'!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]',text)
            self.assertEqual(len(embeds),1)
            # Check the path as the vault consumer sees it, without prepending
            # the current note's directory or normalizing ../../ segments.
            self.assertEqual((self.vault/embeds[0]).read_bytes(),image.read_bytes())
        self.assertNotIn('source.pdf',(self.vault/'附件/组会/2000-01-01/汇报材料.md').read_text('utf8'))
        self.assertEqual(self.sync(root)['changed_files'],0)

    def test_encoded_image_paths_and_plain_image_links_use_vault_targets(self):
        from PIL import Image
        from urllib.parse import quote
        root=self.meeting('2000-01-01')
        source=root/'captures/图 8.png';Image.new('RGB',(32,18),'blue').save(str(source))
        report=root/'组会报告.md'
        report.write_text(report.read_text('utf8')+'\n![仲裁图](%s)\n[查看原图](%s)\n'%(quote('captures/图 8.png'),quote('captures/图 8.png')),encoding='utf8')
        self.sync(root)
        text=(self.vault/'知识/组会/2000-01-01 小组会报告.md').read_text('utf8')
        embeds=re.findall(r'!\[\[([^\]]+)\]\]',text)
        self.assertEqual(len(embeds),1)
        self.assertEqual((self.vault/embeds[0]).read_bytes(),source.read_bytes())
        self.assertIn('[[%s|查看原图]]'%embeds[0],text)
        self.assertEqual(self.sync(root)['changed_files'],0)


if __name__=='__main__':unittest.main()
