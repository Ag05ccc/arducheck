import unittest

from app.core import preflight


class FakeMav:
    sysid = 7
    autopilot_version = None
    param_fetch_done = True

    def __init__(self):
        self.params = {
            "SERVO1_FUNCTION": 4,
            "SERVO1_TRIM": 1500,
            "SERVO1_MIN": 1000,
            "SERVO1_MAX": 2000,
            "SERVO1_REVERSED": 0,
        }
        self._messages = [
            (1.0, 4, "old", 1),
            (2.0, 3, "new", 2),
        ]

    def link_stats(self):
        return {"connected": True}

    def param_progress(self):
        return {"got": len(self.params), "total": len(self.params), "done": True}

    def recent_statustexts(self, since=None):
        if since is None:
            return list(self._messages)
        return [m for m in self._messages if m[0] >= since]

    def get_msg(self, *_args, **_kwargs):
        return None

    def get_param(self, name, default=None):
        return self.params.get(name, default)


class PreflightCoreTests(unittest.TestCase):
    def test_operation_state_busy_contract(self):
        op = preflight.OperationState()

        self.assertTrue(op.set_busy("connect", "Bağlanıyor"))
        self.assertFalse(op.set_busy("checks", "Kontroller"))
        self.assertEqual(op.snapshot(), {"busy": "connect", "progress": "Bağlanıyor"})

        op.clear_if("checks")
        self.assertEqual(op.snapshot()["busy"], "connect")
        op.clear_if("connect")
        self.assertEqual(op.snapshot(), {"busy": None, "progress": ""})

    def test_operation_state_can_refresh_progress_for_current_busy_name(self):
        op = preflight.OperationState()

        self.assertTrue(op.set_busy("servo", "Yüzey testi: roll"))
        self.assertFalse(op.set_busy("servo", "Yüzey testi: pitch"))
        self.assertTrue(op.set_progress_if_busy("servo", "Yüzey testi: pitch"))
        self.assertFalse(op.set_progress_if_busy("checks", "Kontroller"))
        self.assertEqual(op.snapshot(), {"busy": "servo", "progress": "Yüzey testi: pitch"})

    def test_state_payload_filters_messages_and_keeps_common_shape(self):
        payload = preflight.build_state_payload(
            FakeMav(), preflight.OperationState(), calibration=FakeCalibration(),
            connect_info={"ok": True}, results={"time": 123.0},
            servo_test={"axis": "roll", "dir": "plus"},
            msgs_since=1.5, boot=42.0, embedded=True)

        self.assertTrue(payload["connected"])
        self.assertEqual(payload["boot"], 42.0)
        self.assertEqual(payload["results_time"], 123.0)
        self.assertEqual(payload["servo_test"]["axis"], "roll")
        self.assertEqual([m["text"] for m in payload["messages"]], ["new"])

    def test_list_servos_uses_shared_surface_mapping(self):
        servos, err = preflight.list_servos(FakeMav())

        self.assertIsNone(err)
        self.assertEqual(len(servos), 1)
        self.assertEqual(servos[0]["n"], 1)
        self.assertIn("Kanatçık", servos[0]["function"])


class FakeCalibration:
    def snapshot(self):
        return {"phase": "idle"}


if __name__ == "__main__":
    unittest.main()
