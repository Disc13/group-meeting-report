"""Regression: representative timestamps must not erase later occurrences."""
import tempfile,unittest
from pathlib import Path
from PIL import Image
from split_meeting import export_images,group_pages


class PresenterBoundaryTests(unittest.TestCase):
    def test_reused_page_and_different_family_id_survive_later_section(self):
        sections={'presentations':[
            {'id':'first','file_stem':'first','material_start_s':0,'material_end_s':10},
            {'id':'second','file_stem':'second','material_start_s':10,'material_end_s':20}]}
        reading=[{'page_id':'representative','first_shown_s':2,'start_s':2,'end_s':5,
                  'occurrence_ids':['early','later-family-member']}]
        intervals=[{'id':'early','page_id':'representative','start_s':2,'end_s':5},
                   {'id':'later-family-member','page_id':'other-capture','start_s':12,'end_s':15}]
        groups=group_pages(sections,reading,intervals,{'representative'})
        self.assertEqual([g['pages'][0]['first_shown_in_section_s'] for g in groups],[2,12])
        self.assertEqual(groups[1]['pages'][0]['display_intervals'][0]['source_page_id'],'other-capture')

    def test_overlap_cannot_silently_double_assign(self):
        sections={'presentations':[
            {'id':'first','file_stem':'first','material_start_s':0,'material_end_s':12},
            {'id':'second','file_stem':'second','material_start_s':10,'material_end_s':20}]}
        reading=[{'page_id':'a','occurrence_ids':['i']}]
        intervals=[{'id':'i','page_id':'a','start_s':2,'end_s':15}]
        with self.assertRaisesRegex(ValueError,'Overlapping'):
            group_pages(sections,reading,intervals,{'a'})

    def test_original_render_is_copied_without_pdf_or_jpeg_reencoding(self):
        with tempfile.TemporaryDirectory(prefix='.image-export-',dir=str(Path(__file__).resolve().parent)) as folder:
            root=Path(folder);(root/'reference').mkdir();(root/'captures').mkdir()
            native=root/'reference/page-001.png';Image.new('RGB',(80,45),'blue').save(str(native))
            Image.new('RGB',(40,23),'red').save(str(root/'captures/frame.jpg'))
            group={'presenter':'test','title':'native','file_stem':'test','reference_dir':'reference',
                   'pages':[{'page_id':'capture-001','image':'frame.jpg','reference':{'page':1,'status':'matched'},'first_shown_in_section_s':10}]}
            export_images(root,group)
            destination=root/group['pages'][0]['export_image']
            self.assertEqual(destination.suffix,'.png')
            self.assertEqual(destination.read_bytes(),native.read_bytes())
            with Image.open(destination) as image:self.assertEqual(image.size,(80,45))


if __name__=='__main__':unittest.main()
