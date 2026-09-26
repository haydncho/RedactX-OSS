"""整页方向检测的回归测试：文字稀少的横放扫描页不能判反 180°。页面内容全部为虚构的英文与数字。"""

import cv2
import numpy as np

from redactx import ocr


def _sparse_page() -> np.ndarray:
    """A4 竖版、200 DPI，只有十几行短文字，模拟病程记录这类文字稀少的页面。"""
    img = np.full((2339, 1654, 3), 255, np.uint8)
    lines = ["Patient record page 3", "Admission 2025-10-01 10:30", "Temperature 36.5 C  Pulse 78",
             "Blood pressure 132/84 mmHg", "Plan: routine blood tests", "Discharge 2025-10-15",
             "Follow up in clinic", "Ward 12  Bed 23", "Record number 619896", "Signed by attending",
             "Diagnosis code K80.100", "Page 3 of 4"]
    for i, t in enumerate(lines):
        cv2.putText(img, t, (160, 260 + i * 110), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    return img


def test_sideways_page_orientation():
    page = _sparse_page()
    # 内容逆时针转 90° 的横放扫描，需要顺时针转 90° 纠正；反方向同理
    assert ocr.detect_orientation(np.ascontiguousarray(np.rot90(page, 1))) == 90
    assert ocr.detect_orientation(np.ascontiguousarray(np.rot90(page, -1))) == 270


def test_upright_page_not_rotated():
    assert ocr.detect_orientation(_sparse_page()) == 0
