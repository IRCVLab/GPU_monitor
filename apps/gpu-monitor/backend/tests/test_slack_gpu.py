import unittest

from backend.slack_gpu import build_gpu_command_payload


def _server(
    server_id: int,
    name: str,
    *,
    status: str = "online",
    display_order: int = 0,
    gpus: list[dict] | None = None,
) -> dict:
    return {
        "server_id": server_id,
        "server_name": name,
        "status": status,
        "display_order": display_order,
        "network": "internal",
        "host": f"10.0.0.{server_id}",
        "port": 2200 + server_id,
        "last_seen": "2026-08-18T01:02:03+00:00",
        "status_reason": {},
        "gpus": gpus or [],
        "system": {
            "cpu_percent": 25,
            "ram_used": 32768,
            "ram_total": 65536,
        },
    }


def _section_texts(payload: dict) -> list[str]:
    return [
        block["text"]["text"]
        for block in payload["blocks"]
        if block["type"] == "section" and "text" in block
    ]


class SlackGpuPayloadTests(unittest.TestCase):
    def test_overview_preserves_display_order_then_registration_id(self) -> None:
        state = {
            30: _server(30, "third", status="offline", display_order=2),
            20: _server(20, "second", status="online", display_order=1),
            10: _server(10, "first", status="degraded", display_order=1),
        }

        payload = build_gpu_command_payload(state, "all")
        section_texts = _section_texts(payload)

        self.assertEqual(
            [next(name for name in ("first", "second", "third") if name in text) for text in section_texts],
            ["first", "second", "third"],
        )

    def test_overview_prioritizes_gpu_availability_and_omits_system_metadata(self) -> None:
        state = {
            1: _server(
                1,
                "hinton",
                gpus=[
                    {
                        "index": 0,
                        "utilization": 1,
                        "memory_used": 1,
                        "memory_total": 81920,
                        "power_draw": 20,
                        "users": ["alice"],
                    },
                    {
                        "index": 1,
                        "utilization": 0,
                        "memory_used": 0,
                        "memory_total": 81920,
                        "power_draw": 20,
                        "users": [],
                    },
                ],
            )
        }

        payload = build_gpu_command_payload(state, "internal")
        rendered = "\n".join(_section_texts(payload))
        context = " ".join(
            element["text"]
            for block in payload["blocks"]
            if block["type"] == "context"
            for element in block["elements"]
        )

        self.assertIn("1 / 2 available", rendered + context)
        self.assertIn("G0", rendered)
        self.assertIn("alice", rendered)
        self.assertIn("● G0 alice", rendered)
        self.assertIn("G1", rendered)
        self.assertIn("free", rendered.lower())
        self.assertNotIn("10.0.0.1", rendered)
        self.assertNotIn("CPU", rendered)
        self.assertNotIn("RAM", rendered)
        self.assertNotIn("01:02:03", rendered + context)

    def test_specific_server_query_keeps_diagnostic_details(self) -> None:
        state = {1: _server(1, "hinton")}

        payload = build_gpu_command_payload(state, "hinton")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("10.0.0.1:2201", rendered)
        self.assertIn("CPU", rendered)
        self.assertIn("RAM", rendered)

    def test_unhealthy_server_stale_gpu_data_is_not_presented_as_free(self) -> None:
        for status in ("offline", "degraded", "unknown"):
            with self.subTest(status=status):
                server = _server(
                    1,
                    f"{status}-server",
                    status=status,
                    gpus=[
                        {
                            "index": 0,
                            "utilization": 0,
                            "memory_used": 0,
                            "memory_total": 81920,
                            "power_draw": 0,
                            "users": [],
                        }
                    ],
                )
                server["status_reason"] = {"message": "snapshot unavailable"}

                payload = build_gpu_command_payload({1: server}, "internal")
                rendered = "\n".join(_section_texts(payload))
                context = " ".join(
                    element["text"]
                    for block in payload["blocks"]
                    if block["type"] == "context"
                    for element in block["elements"]
                )
                all_text = rendered + context + payload["text"]

                self.assertIn("0 / 1 available", rendered + context)
                self.assertNotIn("1 / 1 available", all_text)
                self.assertNotIn("G0 FREE", all_text)

    def test_active_gpu_without_detected_user_is_labeled_busy(self) -> None:
        state = {
            1: _server(
                1,
                "hinton",
                gpus=[
                    {
                        "index": 0,
                        "utilization": 90,
                        "memory_used": 4096,
                        "memory_total": 81920,
                        "power_draw": 200,
                        "users": [],
                    }
                ],
            )
        }

        payload = build_gpu_command_payload(state, "internal")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("● G0 BUSY", rendered)
        self.assertNotIn("● G0 idle", rendered)
        self.assertIn("G0 BUSY", payload["text"])
        self.assertNotIn("G0 FREE", payload["text"])


if __name__ == "__main__":
    unittest.main()
