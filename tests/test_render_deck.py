"""Only synthetic decks; external Office processes are mocked."""
import hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from PIL import Image
from render_deck import render


def pdf(path):
    with fitz.open() as doc:
        for number in (1,2):doc.new_page(width=320,height=180).insert_text((20,40),'Synthetic slide '+str(number))
        doc.save(str(path))


class DeckRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.render-test-',dir=str(Path(__file__).resolve().parent))
        self.root=Path(self.temp.name).resolve()

    def tearDown(self):self.temp.cleanup()

    def test_pdf_renders_each_page_without_rewriting_source(self):
        source=self.root/'synthetic.pdf';pdf(source);before=source.read_bytes()
        render(source,self.root/'pages')
        self.assertEqual(len(list((self.root/'pages').glob('page-*.png'))),2)
        with Image.open(self.root/'pages/page-001.png') as image:self.assertEqual(image.size,(480,270))
        self.assertEqual(source.read_bytes(),before)

    def test_libreoffice_conversion_uses_isolated_profile_and_source_is_preserved(self):
        source=self.root/'synthetic.pptx';source.write_bytes(b'inert-pptx-fixture');before=hashlib.sha256(source.read_bytes()).digest()
        calls=[]
        def convert(args,**kwargs):
            calls.append(args);out=Path(args[args.index('--outdir')+1]);pdf(out/(source.stem+'.pdf'))
        with patch('render_deck.subprocess.run',side_effect=convert):render(source,self.root/'pages',soffice='synthetic-soffice')
        self.assertTrue(any(a.startswith('-env:UserInstallation=file:') for a in calls[0]))
        self.assertEqual(calls[0][-1],str(source))
        self.assertEqual(len(list((self.root/'pages').glob('page-*.png'))),2)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)


if __name__=='__main__':unittest.main()
