from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext


class MqttEnvDefaultsTests(unittest.TestCase):
    def test_mqtt_env_defaults_appends_configured_suffix_to_short_host(self) -> None:
        context = AddonContext()
        context._config_cache = {"mqtt_host_suffix": ".example.internal."}

        with (
            patch.object(
                AddonContext,
                "service_info",
                return_value={"port": 1883, "username": "user", "password": "pass"},
            ),
            patch.object(AddonContext, "host_hostname", return_value="homeassistant"),
        ):
            env = context.mqtt_env_defaults()

        self.assertEqual(env["MQTT_HOST"], "homeassistant.example.internal")
        self.assertEqual(env["MQTT_BROKER"], "homeassistant.example.internal")

    def test_mqtt_env_defaults_keeps_fqdn_host_unchanged(self) -> None:
        context = AddonContext()
        context._config_cache = {"mqtt_host_suffix": "example.internal"}

        with (
            patch.object(
                AddonContext,
                "service_info",
                return_value={"port": 1883, "username": "user", "password": "pass"},
            ),
            patch.object(AddonContext, "host_hostname", return_value="homeassistant.example.internal"),
        ):
            env = context.mqtt_env_defaults()

        self.assertEqual(env["MQTT_HOST"], "homeassistant.example.internal")
        self.assertEqual(env["MQTT_BROKER"], "homeassistant.example.internal")

    def test_mqtt_env_defaults_keeps_short_host_when_suffix_is_not_configured(self) -> None:
        context = AddonContext()
        context._config_cache = {}

        with (
            patch.object(
                AddonContext,
                "service_info",
                return_value={"port": 1883, "username": "user", "password": "pass"},
            ),
            patch.object(AddonContext, "host_hostname", return_value="homeassistant"),
        ):
            env = context.mqtt_env_defaults()

        self.assertEqual(env["MQTT_HOST"], "homeassistant")
        self.assertEqual(env["MQTT_BROKER"], "homeassistant")
