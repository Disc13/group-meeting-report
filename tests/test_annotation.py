"""Geometry and page-identity checks for numbered cursor evidence."""
import unittest
import cv2
import numpy as np
from annotate_slides import eligible,register


class RegistrationTests(unittest.TestCase):
    def test_known_projective_transform_keeps_cursor_position(self):
        rng=np.random.RandomState(217)
        source=np.zeros((420,680,3),np.uint8)+240
        for i in range(300):
            x,y=rng.randint(20,660),rng.randint(20,400)
            cv2.circle(source,(int(x),int(y)),int(rng.randint(2,9)),tuple(int(c) for c in rng.randint(0,220,3)),-1)
        h=np.array([[.92,.025,24],[-.012,.96,20],[.00003,-.00002,1.]])
        target=cv2.warpPerspective(source,h,(680,450),borderValue=(255,255,255))
        point=(257.,163.);expected=h.dot([point[0],point[1],1]);expected=expected[:2]/expected[2]
        result,stats=register(source,target,point)
        self.assertLess(np.linalg.norm(np.asarray(result)-expected),1.5)
        self.assertGreaterEqual(stats['inliers'],60)

    def test_untextured_page_is_not_silently_given_coordinates(self):
        page=np.zeros((200,300,3),np.uint8)
        with self.assertRaisesRegex(ValueError,'texture'):register(page,page,(30,40))

    def test_different_page_requires_an_actual_reviewed_family_occurrence(self):
        row={'page_id':'other','interval_id':'later'}
        self.assertFalse(eligible('target',row,[{'page_id':'target','occurrence_ids':['earlier']}]))
        self.assertTrue(eligible('target',row,[{'page_id':'target','occurrence_ids':['earlier','later']}]))


if __name__=='__main__':unittest.main()
