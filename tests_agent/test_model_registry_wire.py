import unittest

from reverse_engineering.graphics_agent.model_registry import (
    model_settings_for_profile,
    model_string,
    profile_for_mode,
    resolve_effective_profile,
)


class ModelRegistryWireTests(unittest.TestCase):
    def test_discovery_alias_maps_to_flash(self) -> None:
        profile = profile_for_mode("discovery")
        self.assertEqual(model_string(profile), "deepseek:deepseek-flash")
        effective = resolve_effective_profile(profile)
        self.assertTrue(effective.thinking)
        settings = model_settings_for_profile(profile)
        self.assertTrue(settings.get("thinking"))

    def test_verification_thinking_disabled(self) -> None:
        profile = profile_for_mode("verify")
        settings = model_settings_for_profile(profile)
        self.assertFalse(settings.get("thinking"))


if __name__ == "__main__":
    unittest.main()
