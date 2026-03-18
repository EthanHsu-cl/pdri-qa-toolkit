#!/usr/bin/env python3
"""Auto-generated test for: sign in your account
Category: Mixpanel | Quadrant: Q4 - Test First | Risk: 100 (I:5 x P:4 x D:5)
TODO: Replace placeholder selectors."""
import pytest
import allure

@allure.suite("Mixpanel")
@allure.sub_suite("sign in your account")
@allure.tag("Q4")
@pytest.mark.q4
class TestSignInYourAccount:
    """Q4 - Test First tests for sign in your account."""

    @allure.title("sign in your account - Screen Launch")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_screen_launches(self, driver):
        pass

    @allure.title("sign in your account - Basic Functionality")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_basic_functionality(self, driver):
        pass

    @allure.title("sign in your account - Visual Regression")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.visual
    def test_visual_regression(self, driver, visual_check):
        # TODO: Navigate then call visual_check("sign_in_your_account")
        pass
