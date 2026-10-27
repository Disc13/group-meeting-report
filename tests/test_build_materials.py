"""Keep distinct slides available before the agent reviews animations."""
import json,tempfile,unittest
from pathlib import Path
from build_materials import group_occurrences,run


def interval(number,page_id,reference=None):
    return {'id':'interval-%04d'%number,'page_id':page_id,'start_s':number*10,
            'end_s':number*10+8,'stable':True,'image':'pages/'+page_id+'.jpg',
            'crop_method':'full_frame_fallback','reference':reference}


class MaterialRetentionTests(unittest.TestCase):
    def test_repeated_capture_keeps_all_occurrences(self):
        rows=[interval(1,'capture-001'),interval(2,'capture-002'),interval(3,'capture-001')]
        groups=group_occurrences(rows)
        self.assertEqual([r['id'] for r in groups[0]['rows']],['interval-0001','interval-0003'])
        self.assertEqual(len(groups),2)

    def test_only_verified_original_page_allows_cross_capture_merge(self):
        rows=[interval(1,'a',{'page':1,'status':'matched'}),interval(2,'b',{'page':1,'status':'matched'}),
              interval(3,'c',{'page':1,'status':'ambiguous'}),interval(4,'d',{'page':1,'status':'ambiguous'})]
        groups=group_occurrences(rows)
        self.assertEqual([len(g['rows']) for g in groups],[2,1,1])

    def test_similar_ocr_results_and_textless_diagram_survive_material_pack(self):
        with tempfile.TemporaryDirectory(prefix='.materials-test-',dir=str(Path(__file__).resolve().parent)) as folder:
            root=Path(folder);captures=root/'captures';(captures/'ocr').mkdir(parents=True)
            rows=[interval(i,'capture-%03d'%i) for i in range(1,8)]
            def dump(path,data):path.write_text(json.dumps(data),encoding='utf8')
            dump(captures/'slides.json',{'intervals':rows,'unique_pages':7,'timing_uncertainty_s':2})
            for i,row in enumerate(rows):
                # Six result pages have one common title and nearly identical OCR;
                # the last page is a diagram with no readable OCR.
                text=('Experiment results: shared title, shared metric names and long repeated table header; result='+str(i)) if i<6 else ''
                dump(captures/'ocr'/(row['page_id']+'.json'),{'page_id':row['page_id'],'text':text})
            transcript=[{'start_s':r['start_s'],'end_s':r['end_s'],'speaker':'Presenter A','text':'Synthetic explanation '+r['page_id']} for r in rows]
            (root/'transcript.jsonl').write_text('\n'.join(json.dumps(r) for r in transcript),encoding='utf8')
            output=root/'pack';run(captures,None,output)
            result=json.loads((output/'reading-order.json').read_text('utf8'))
            self.assertEqual([p['page_id'] for p in result['pages']],[r['page_id'] for r in rows])
            self.assertEqual(result['excluded'],[])
            self.assertEqual(result['needs_review'][0]['page_id'],'capture-007')
            packet=(output/'material-packet.md').read_text('utf8')
            for row in rows:
                self.assertIn('Synthetic explanation '+row['page_id'],packet)
                self.assertIn('pages/'+row['page_id']+'.jpg',packet)


if __name__=='__main__':unittest.main()
