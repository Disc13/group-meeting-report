"""Synthetic checks for review freshness, numbered figures and chat-only delivery."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meeting
import review_references as review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.review-', dir=Path(__file__).parent)
        self.root = Path(self.temp.name).resolve()
        self.write('meeting-structure.json', {'presentations': [{'id': 'speaker-a'}, {'id': 'speaker-b'}]})
        self.write('captures/slides.json', {'sample_step_s': 2})
        self.write('captures/reading-order.json', {'pages': []})
        self.write('captures/image-selection.json', {'included': ['capture-001', 'capture-002']})
        self.write('sections/manifest.json', {'presentations': [{'id': 'speaker-a'}, {'id': 'speaker-b'}]})
        for i, speaker in enumerate(('speaker-a', 'speaker-b'), 1):
            image = 'output/%s.png' % speaker
            self.blob(image, b'synthetic exported image')
            self.blob('captures/pages/%s.png' % i, b'synthetic source image')
            self.write('sections/%s.json' % speaker, {'pages': [{
                'page_id': 'capture-%03d' % i, 'export_image': image, 'image': 'pages/%s.png' % i,
                'display_intervals': [{'start_s': (i - 1) * 10, 'end_s': i * 10}]}]})
        self.speech = {'id': 'demo-speech', 'start_s': 3, 'end_s': 6, 'text': 'This curve rises.'}
        self.blob('transcript.jsonl', (json.dumps(self.speech) + '\n').encode())
        self.report = '# Synthetic meeting\n\n![A](output/speaker-a.png)\nExplanation.\n\n![B](output/speaker-b.png)\nExplanation.\n'
        self.blob('组会报告.md', self.report.encode())
        review.initialize(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def blob(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def write(self, relative, value):
        self.blob(relative, (json.dumps(value, ensure_ascii=False) + '\n').encode())

    def decide(self):
        data = review.load(self.root / review.RECORD)
        for row in data['pages']:
            row.update(status='unnecessary', reason='Single object; speech identifies it unambiguously.')
        self.write(review.RECORD, data)
        return data

    def annotate(self):
        data = self.decide()
        data['pages'][0].update(status='annotated', reason='Disambiguate two curves.',
                                evidence=[{'time_s': 4, 'utterance_id': 'demo-speech'}])
        self.write(review.RECORD, data)
        self.blob('captures/annotated/capture-001-numbered.png', b'synthetic numbered image')
        self.blob('captures/samples/000003.jpg', b'synthetic evidence frame')
        self.write('captures/annotated/annotations.json', {'pages': [{
            'page_id': 'capture-001', 'image': 'annotated/capture-001-numbered.png',
            'image_sha256': review.digest(self.root / 'captures/annotated/capture-001-numbered.png'),
            'source_image': 'pages/1.png', 'source_sha256': review.digest(self.root / 'captures/pages/1.png'),
            'annotations': [{'number': 1, 'time_s': 4, 'utterance_id': 'demo-speech',
                             'reviewed': True, 'utterance': self.speech, 'sample': 'samples/000003.jpg',
                             'sample_sha256': review.digest(self.root / 'captures/samples/000003.jpg')}]}]})
        text = self.report.replace('output/speaker-a.png', 'captures/annotated/capture-001-numbered.png')
        self.blob('组会报告.md', text.replace('Explanation.', 'Curve ① rises.', 1).encode())

    def test_pending_review_blocks_delivery(self):
        with self.assertRaisesRegex(ValueError, 'Pending'):
            review.check(self.root)

    def test_no_annotation_quota_and_no_check_text_inserted_into_report(self):
        self.decide()
        before = (self.root / '组会报告.md').read_bytes()
        result = review.check(self.root)
        self.assertEqual(result['counts']['unnecessary'], 2)
        self.assertEqual(result['counts']['annotated'], 0)
        self.assertEqual(before, (self.root / '组会报告.md').read_bytes())

    def test_insufficient_evidence_retains_reason_and_timed_speech(self):
        data = self.decide()
        data['pages'][0].update(status='insufficient_evidence', reason='Candidate is ambiguous.',
                                evidence=[{'time_s': 4, 'utterance_id': 'demo-speech'}])
        self.write(review.RECORD, data)
        self.assertEqual(review.check(self.root)['attention'][0]['evidence'][0]['time_s'], 4)
        data['pages'][0]['evidence'][0]['time_s'] = 14
        self.write(review.RECORD, data)
        with self.assertRaisesRegex(ValueError, 'timed speech'):
            review.check(self.root)

    def test_annotation_must_be_real_referenced_and_explained(self):
        self.annotate()
        self.assertEqual(review.check(self.root)['counts']['annotated'], 1)
        self.blob('组会报告.md', self.report.encode())
        with self.assertRaises(ValueError):
            review.check(self.root)

    def test_rendered_png_and_real_evidence_pass_the_delivery_check(self):
        import cv2
        import numpy as np
        from annotate_slides import render
        frame = np.random.RandomState(17).randint(0, 256, (400, 640, 3), dtype=np.uint8)
        encoded = cv2.imencode('.jpg', frame)[1].tobytes()
        decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.blob('captures/samples/000003.jpg', encoded)
        source = cv2.imencode('.png', decoded)[1].tobytes()
        self.blob('captures/pages/1.png', source)
        self.write('captures/slides.json', {'sample_step_s': 2, 'intervals': [{
            'id': 'interval-001', 'page_id': 'capture-001', 'image': 'pages/1.png', 'crop': [0, 0, 640, 400]}]})
        candidate = {'time_s': 4, 'status': 'candidate', 'page_id': 'capture-001',
                     'interval_id': 'interval-001', 'x': 300, 'y': 200, 'meaning': 'synthetic candidate'}
        self.blob('captures/cursor-candidates.jsonl', (json.dumps(candidate) + '\n').encode())
        plan = {'pages': [{'page_id': 'capture-001', 'points': [
            {'number': 1, 'time_s': 4, 'utterance_id': 'demo-speech', 'reviewed': True}]}]}
        with redirect_stdout(io.StringIO()):
            render(self.root / 'captures', plan, self.root / 'captures/annotated')
        review.initialize(self.root, reset=True)
        data = self.decide()
        data['pages'][0].update(status='annotated', reason='Locate the discussed curve.',
                                evidence=[{'time_s': 4, 'utterance_id': 'demo-speech'}])
        self.write(review.RECORD, data)
        text = self.report.replace('output/speaker-a.png', 'captures/annotated/capture-001-numbered.png')
        self.blob('组会报告.md', text.replace('Explanation.', 'Curve ① rises.', 1).encode())
        self.assertEqual(review.check(self.root)['counts']['annotated'], 1)
        self.assertEqual((self.root / 'captures/pages/1.png').read_bytes(), source)
        output = cv2.imdecode(np.frombuffer((self.root / 'captures/annotated/capture-001-numbered.png').read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(output.shape, decoded.shape)
        self.assertFalse(np.array_equal(output, decoded))

    def test_number_on_another_page_does_not_explain_this_image(self):
        self.annotate()
        path = self.root / '组会报告.md'
        path.write_text(path.read_text('utf8').replace('Curve ① rises.', 'Curve rises.') + '\nSee ①.', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'beside its image'):
            review.check(self.root)

    def test_annotation_image_or_evidence_changes_are_rejected(self):
        for relative in ('captures/annotated/capture-001-numbered.png', 'captures/samples/000003.jpg'):
            with self.subTest(relative=relative):
                self.annotate()
                self.blob(relative, b'changed')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    review.check(self.root)

    def test_changed_inputs_require_fresh_decisions(self):
        for relative in ('transcript.jsonl', 'output/speaker-a.png', 'captures/cursor-candidates.jsonl'):
            with self.subTest(relative=relative):
                review.initialize(self.root, reset=True)
                self.decide()
                path = self.root / relative
                self.blob(relative, path.read_bytes() + b'\n' if path.exists() else b'{}\n')
                with self.assertRaisesRegex(ValueError, 'Stale'):
                    review.check(self.root)
                with self.assertRaisesRegex(ValueError, 'changed'):
                    review.initialize(self.root)
        review.initialize(self.root, reset=True)
        self.assertTrue(all(r['status'] == 'pending' for r in review.load(self.root / review.RECORD)['pages']))

    def test_shared_capture_still_requires_each_presenter_review(self):
        path = self.root / 'sections/speaker-b.json'
        section = review.load(path)
        section['pages'][0]['page_id'] = 'capture-001'
        self.write('sections/speaker-b.json', section)
        review.initialize(self.root, reset=True)
        data = self.decide()
        self.assertEqual(review.check(self.root)['total_pages'], 2)
        data['pages'].pop()
        self.write(review.RECORD, data)
        with self.assertRaisesRegex(ValueError, 'each selected'):
            review.check(self.root)

    def test_shared_capture_can_be_numbered_in_only_one_presenter_section(self):
        from render_report import render
        section = review.load(self.root / 'sections/speaker-b.json')
        section['pages'][0].update(page_id='capture-001', image='pages/1.png')
        self.write('sections/speaker-b.json', section)
        review.initialize(self.root, reset=True)
        self.annotate()
        source = '# Synthetic meeting\n\n![A](slide:capture-001)\nCurve ① rises.\n\n![B](slide:capture-001)\nAn overview.\n'
        self.blob('组会讲稿源稿.md', source.encode())
        with redirect_stdout(io.StringIO()):
            render(self.root)
        result = (self.root / '组会报告.md').read_text('utf8')
        self.assertIn('![A](captures/annotated/capture-001-numbered.png)', result)
        self.assertIn('![B](output/speaker-b.png)', result)
        self.assertEqual(review.check(self.root)['counts']['annotated'], 1)

    def test_finish_checks_review_before_generating_cards_or_writing_vault(self):
        self.write('人物汇报提要.json', {})
        args = argparse.Namespace(meeting=self.root, check_only=False, rebuild_slides=False, vault=self.root / 'vault')
        with patch.object(meeting, 'exports_ready', return_value=True), patch.object(meeting, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'Pending'):
                meeting.finish(args, {})
            command.assert_not_called()
        self.assertFalse((self.root / 'workflow.json').exists())

    def test_check_only_emits_chat_result_without_editing_report(self):
        self.decide()
        self.write('人物汇报提要.json', {})
        args = argparse.Namespace(meeting=self.root, check_only=True, rebuild_slides=False, vault=None)
        output = io.StringIO()
        with patch.object(meeting, 'exports_ready', return_value=True), redirect_stdout(output):
            meeting.finish(args, {})
        result = json.loads(output.getvalue())
        self.assertEqual(result['annotation_review_for_chat']['total_pages'], 2)
        self.assertEqual((self.root / '组会报告.md').read_text('utf8'), self.report)
        self.assertFalse((self.root / 'workflow.json').exists())


if __name__ == '__main__':
    unittest.main()
