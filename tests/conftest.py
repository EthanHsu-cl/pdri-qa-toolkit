#!/usr/bin/env python3
"""PDR-I Shared Appium + Visual Regression Fixtures"""
import pytest, os
from appium import webdriver as appium_webdriver
from appium.options.ios import XCUITestOptions
from scripts.visual_regression import capture_screenshot, compute_similarity, highlight_differences

APPIUM_URL = os.environ.get("APPIUM_URL", "http://127.0.0.1:4723")
BUNDLE_ID = os.environ.get("BUNDLE_ID", "com.cyberlink.powerdirector")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "iPhone")
BASELINE_DIR = os.environ.get("BASELINE_DIR", "visual_baselines")
RESULT_DIR = os.environ.get("RESULT_DIR", "visual_results")
SIMILARITY_THRESHOLD = float(os.environ.get("VISUAL_THRESHOLD", "0.95"))

@pytest.fixture(scope="session")
def driver():
    opts = XCUITestOptions()
    opts.platform_name = "iOS"
    opts.device_name = DEVICE_NAME
    opts.bundle_id = BUNDLE_ID
    opts.automation_name = "XCUITest"
    opts.no_reset = True
    opts.new_command_timeout = 300
    d = appium_webdriver.Remote(APPIUM_URL, options=opts)
    d.implicitly_wait(10)
    yield d
    d.quit()

@pytest.fixture
def visual_check(driver):
    os.makedirs(BASELINE_DIR, exist_ok=True)
    os.makedirs(RESULT_DIR, exist_ok=True)
    def _check(screen_name, threshold=SIMILARITY_THRESHOLD):
        current = capture_screenshot(driver, screen_name, RESULT_DIR)
        baseline = os.path.join(BASELINE_DIR, f"{screen_name}.png")
        if not os.path.exists(baseline):
            import shutil; shutil.copy(current, baseline)
            pytest.skip(f"Baseline created for {screen_name}")
        sim = compute_similarity(baseline, current)
        if sim < threshold:
            diff_path = os.path.join(RESULT_DIR, f"{screen_name}_diff.png")
            highlight_differences(baseline, current, diff_path)
            pytest.fail(f"Visual regression: {screen_name} sim={sim:.4f} < {threshold}. Diff: {diff_path}")
    return _check
