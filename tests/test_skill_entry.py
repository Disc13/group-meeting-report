"""Check link identity and cache selection without network or live outputs."""
import importlib.util,json,tempfile,unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/meeting.py'
spec=importlib.util.spec_from_file_location('meeting_skill',SCRIPT)
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)


class LinkEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.skill-entry-test-',dir=str(Path(__file__).resolve().parent))
        self.root=Path(self.temp.name).resolve()

    def tearDown(self):
        assert self.root.parent==Path(__file__).resolve().parent
        self.temp.cleanup()

    def test_cw_and_crm_reuse_the_same_existing_meeting(self):
        old=self.root/'old';old.mkdir()
        (old/'manifest.json').write_text(json.dumps({'source':'https://meeting.tencent.com/cw/abc'}))
        self.assertEqual(entry.choose_root('https://meeting.tencent.com/crm/abc',self.root),old)

    def test_output_of_another_meeting_is_rejected(self):
        old=self.root/'old';old.mkdir()
        (old/'workflow.json').write_text(json.dumps({'source':'https://meeting.tencent.com/cw/other'}))
        with self.assertRaisesRegex(ValueError,'different meeting'):
            entry.choose_root('https://meeting.tencent.com/cw/new',self.root,old)

    def test_new_link_does_not_use_today_as_the_meeting_date(self):
        self.assertEqual(entry.choose_root('https://meeting.tencent.com/cw/new',self.root),self.root/'meetings/new')


if __name__=='__main__':unittest.main()
