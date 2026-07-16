import json
import unittest

from backend.collectors.gpu import parse_gpustat, parse_nvidia_smi


class GpuUserParsingTests(unittest.TestCase):
    def test_parse_gpustat_returns_sorted_unique_users(self) -> None:
        payload = {
            "gpus": [
                {
                    "index": 0,
                    "name": "NVIDIA A100",
                    "utilization.gpu": 55,
                    "memory.used": 1234,
                    "memory.total": 40960,
                    "temperature.gpu": 41,
                    "power.draw": 211.2,
                    "processes": [
                        {"username": "alice"},
                        {"username": "carol"},
                        {"username": "bob"},
                        {"username": "alice"},
                        {},
                    ],
                }
            ]
        }

        data = parse_gpustat(json.dumps(payload))

        self.assertEqual(data.gpus[0].users, ["alice", "bob", "carol"])

    def test_parse_nvidia_smi_returns_sorted_unique_users(self) -> None:
        output = "\n".join(
            [
                "0, NVIDIA A100, 88, 10240, 40960, 50, 250.6, GPU-0",
                "##PROCS##",
                "GPU-0, 101",
                "GPU-0, 102",
                "GPU-0, 103",
                "GPU-0, 104",
                "##PS##",
                "101 carol",
                "102 alice",
                "103 bob",
                "104 alice",
            ]
        )

        data = parse_nvidia_smi(output)

        self.assertEqual(data.gpus[0].users, ["alice", "bob", "carol"])


if __name__ == "__main__":
    unittest.main()
