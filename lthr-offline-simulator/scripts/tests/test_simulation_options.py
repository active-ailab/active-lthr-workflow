from __future__ import annotations

import contextlib
import io
import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_device_multi_sim
import run_lthr_offline_sim
from shared import offline_sim_contract as contract
from shared import offline_sim_runner as runner
from shared.data_sample_ops import deploy_data_sample_tree
from shared.batch_runner import build_issue_command


OPTIONS = {
    "start_time": 1793577600,
    "interval_seconds": 43200,
    "clear_history_on_start": 0,
    "clear_history_on_end": 0,
}


def result_payload(**overrides):
    result = {
        "version": "1", "run_id": "sim_test", "mode": "single_sample",
        "rounds": "2", "sample_count": "1", "overall_status": "success",
        "start_time": "1793577600", "interval_seconds": "43200",
        "clear_history_on_start": "0", "clear_history_on_end": "0",
        "samples": [{"path": "data_sample/gomore_data_a.csv", "status": "success", "rounds_completed": "2"}],
    }
    result.update(overrides)
    return result


class SimulationContractTests(unittest.TestCase):
    def render(self, **kwargs):
        base = dict(run_id="sim_test", mode="single_sample", rounds=2,
                    sample_paths=["data_sample/gomore_data_a.csv"])
        base.update(kwargs)
        return contract.render_config(**base)

    def test_omitted_options_leave_defaults_to_device(self):
        text = self.render()
        for key in OPTIONS:
            self.assertNotIn(key + "=", text)
        self.assertIn("sample.0=data_sample/gomore_data_a.csv", text)

    def test_all_history_switch_combinations_render_independently(self):
        for start, end in itertools.product((0, 1), repeat=2):
            with self.subTest(start=start, end=end):
                text = self.render(**dict(OPTIONS, clear_history_on_start=start, clear_history_on_end=end))
                self.assertIn("start_time=1793577600\n", text)
                self.assertIn("interval_seconds=43200\n", text)
                self.assertIn("clear_history_on_start={}\n".format(start), text)
                self.assertIn("clear_history_on_end={}\n".format(end), text)

    def test_invalid_values_are_rejected(self):
        for key in ("start_time", "interval_seconds"):
            for value in (0, -1, contract.UINT32_MAX + 1, True, 1.5, "123"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.render(**{key: value})
        for key in ("clear_history_on_start", "clear_history_on_end"):
            for value in (-1, 2, True, "0"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.render(**{key: value})

    def test_multi_sample_modes_accept_time_but_reject_either_history_switch(self):
        for mode in ("single_person_multi_sample", "multi_person_multi_sample"):
            base = dict(mode=mode, sample_paths=["data_sample/a.csv", "data_sample/b.csv"])
            self.assertIn("interval_seconds=3600", self.render(**base, start_time=100000, interval_seconds=3600))
            for key in ("clear_history_on_start", "clear_history_on_end"):
                for value in (0, 1):
                    with self.subTest(mode=mode, key=key, value=value), self.assertRaisesRegex(ValueError, "single_sample"):
                        self.render(**base, **{key: value})

    def test_time_axis_boundaries_follow_each_c_mode(self):
        limit = contract.UINT32_MAX - contract.MAX_CSV_ROWS - 1
        for mode, paths, last_index in (
            ("single_sample", ["data_sample/a.csv"], 1),
            ("single_person_multi_sample", ["data_sample/a.csv", "data_sample/b.csv"], 3),
            ("multi_person_multi_sample", ["data_sample/a.csv", "data_sample/b.csv"], 1),
        ):
            with self.subTest(mode=mode):
                args = dict(mode=mode, sample_paths=paths, interval_seconds=10)
                self.render(**args, start_time=limit - last_index * 10)
                with self.assertRaisesRegex(ValueError, "configured_time_axis_overflow"):
                    self.render(**args, start_time=limit - last_index * 10 + 1)
        with self.assertRaises(ValueError):
            self.render(rounds=contract.UINT32_MAX, start_time=1, interval_seconds=contract.UINT32_MAX)

    def test_invalid_config_does_not_truncate_existing_ini(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / contract.CONFIG_NAME
            ini.write_text("previous config")
            with self.assertRaises(ValueError):
                contract.write_config(Path(tmp), run_id="sim_test", mode="single_sample", rounds=2,
                                      sample_paths=["data_sample/a.csv"], interval_seconds=0)
            self.assertEqual(ini.read_text(), "previous config")

    def test_result_requires_every_explicit_option_to_match_including_zero(self):
        expected = {"expected_" + key: value for key, value in OPTIONS.items()}
        contract.validate_result(result_payload(), "sim_test", **expected)
        for key, value in OPTIONS.items():
            for missing in (False, True):
                result = result_payload()
                if missing:
                    del result[key]
                else:
                    result[key] = str(value + 1)
                with self.subTest(key=key, missing=missing), self.assertRaisesRegex(RuntimeError, key):
                    contract.validate_result(result, "sim_test", **expected)

    def test_legacy_results_work_only_with_omitted_options(self):
        result = result_payload()
        for key in OPTIONS:
            del result[key]
        contract.validate_result(result, "sim_test")
        with self.assertRaisesRegex(RuntimeError, "interval_seconds"):
            contract.validate_result(result, "sim_test", expected_interval_seconds=86400)
        defaults = result_payload(interval_seconds="86400", clear_history_on_start="1", clear_history_on_end="1")
        contract.validate_result(defaults, "sim_test")
        for key in ("interval_seconds", "clear_history_on_start", "clear_history_on_end"):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, key):
                contract.validate_result(dict(defaults, **{key: "0"}), "sim_test")


class ConfiguredFlowTests(unittest.TestCase):
    def test_flow_checks_echo_and_extracts_both_archives_without_deleting_active_history(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                staging = root / "staging"
                staging.mkdir()
                (staging / "gomore_data_a.csv").write_text("input")
                disk = root / "disk"
                device = disk / "sport/gomore/data_sample"
                output = root / "output"

                def deploy(**kwargs):
                    kwargs["deploy_hook"](str(disk))
                    return str(disk), "DEPLOY\n"

                def utest(**kwargs):
                    ini = (device / contract.CONFIG_NAME).read_text()
                    for key, value in OPTIONS.items():
                        self.assertIn("{}={}\n".format(key, value), ini)
                    result = result_payload()
                    samples = result.pop("samples")
                    if mismatch:
                        result["clear_history_on_end"] = "1"
                    result["history_file"] = "/storage/sport/gomore/data_sample/gomore_his_sim_test.data"
                    result["original_history_file"] = "/storage/sport/gomore/data_sample/gomore_his_sim_test_original.data"
                    text = "".join("{}={}\n".format(key, value) for key, value in result.items())
                    text += "".join("sample.0.{}={}\n".format(key, value) for key, value in samples[0].items())
                    (device / contract.RESULT_NAME).write_text(text)
                    (device / "gomore_his_sim_test.data").write_bytes(b"SIMULATED")
                    (device / "gomore_his_sim_test_original.data").write_bytes(b"ORIGINAL")
                    (device.parent / "gomore_his.data").write_bytes(b"SIMULATED")
                    return {"ended": True, "started": True, "status": "completed"}, (
                        "Sample 1/1, round 1/2, start=1793577600\n"
                        "Sample 1/1, round 2/2, start=1793620800\n"
                    )

                with patch.object(runner, "deploy_data_to_device", side_effect=deploy), \
                     patch.object(runner, "ensure_device_ready_sequence"), \
                     patch.object(runner, "execute_simulation_utest", side_effect=utest):
                    summary, _ = runner.run_sample_flow(
                        wlctl_path=Path("/fake/wlctl"), data_sample_dir=staging,
                        sample_paths=["data_sample/gomore_data_a.csv"], mode="single_sample", rounds=2,
                        run_id="sim_test", output_dir=output, disk_root=str(disk), mgr_port="", uart_port="",
                        boot_timeout_sec=1, mount_wait_sec=1, run_timeout_sec=1, listen_chunk_sec=1,
                        serial_settle_sec=0, deploy_attempts=1, deploy_only=False,
                        deploy_hook=lambda mount: deploy_data_sample_tree(mount, staging), **OPTIONS,
                    )
                self.assertEqual(summary["status"], "failed" if mismatch else "success")
                self.assertEqual(summary["simulation_config"], OPTIONS)
                self.assertEqual(summary["data_sample_cleared"], not mismatch)
                self.assertEqual(len(summary["history_paths"]), 2)
                self.assertEqual((output / "gomore_his_sim_test.data").read_bytes(), b"SIMULATED")
                self.assertEqual((output / "gomore_his_sim_test_original.data").read_bytes(), b"ORIGINAL")
                self.assertEqual((device.parent / "gomore_his.data").read_bytes(), b"SIMULATED")
                self.assertEqual((device / contract.CONFIG_NAME).exists(), mismatch)
                self.assertEqual((device / "gomore_data_a.csv").exists(), mismatch)
                if mismatch:
                    self.assertIn("clear_history_on_end", summary["error"])


class EntrypointOptionTests(unittest.TestCase):
    def test_batch_forwards_the_same_values_to_each_child(self):
        flags = []
        for key, value in OPTIONS.items():
            flags.extend(["--" + key.replace("_", "-"), str(value)])
        for issue in ("ITS-1", "ITS-2"):
            cmd = build_issue_command(
                python_exe="python3", child_script=SCRIPTS_DIR / "run_lthr_offline_sim.py",
                issue=issue, child_argv=flags, issue_dir=Path("/tmp/batch") / issue,
            )
            self.assertEqual(cmd[3:-2], flags)
            self.assertEqual(cmd[2], issue)

    def patches(self, stack, module, root, flows):
        wlctl = root / "wlctl"
        wlctl.touch()
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(module, "housekeep_output"))
        stack.enter_context(patch.object(module, "_safe_reconfigure_stdio"))
        stack.enter_context(patch.object(module, "_is_wsl_environment", return_value=False))
        stack.enter_context(patch.object(module, "build_wlctl_path", return_value=wlctl))
        stack.enter_context(patch.object(module, "format_timestamp", return_value="20261102_120000"))
        stack.enter_context(patch.object(module, "write_artifacts", return_value={}))
        stack.enter_context(patch.object(module, "print_artifact_paths"))

        def flow(**kwargs):
            flows.append(kwargs)
            options = {key: kwargs[key] for key in OPTIONS}
            contract.write_config(kwargs["data_sample_dir"], run_id=kwargs["run_id"], mode=kwargs["mode"],
                                  rounds=kwargs["rounds"], sample_paths=kwargs["sample_paths"], **options)
            return {"status": "success", "simulation_config": options, "result": {"start_time": "1793577600"},
                    "samples": [{"device_status": "success", "results": []} for _ in kwargs["sample_paths"]]}, "LOG\n"

        stack.enter_context(patch.object(module, "run_sample_flow", side_effect=flow))

    def test_local_cli_forwards_options_for_all_modes(self):
        for mode in sorted(contract.MODES):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                root = Path(tmp)
                inputs = root / "data_sample"
                inputs.mkdir()
                (inputs / "gomore_data_a.csv").write_text("a")
                if mode != "single_sample":
                    (inputs / "gomore_data_b.csv").write_text("b")
                flows = []
                self.patches(stack, run_device_multi_sim, root, flows)
                options = OPTIONS if mode == "single_sample" else {key: value for key, value in OPTIONS.items() if key in ("start_time", "interval_seconds")}
                argv = ["--data-dir", str(inputs), "--mode", mode, "--rounds", "2", "--output-dir", str(root / "output")]
                for key, value in options.items():
                    argv.extend(["--" + key.replace("_", "-"), str(value)])
                self.assertEqual(run_device_multi_sim.main(argv), 0)
                self.assertEqual(len(flows), 1)
                for key in OPTIONS:
                    self.assertEqual(flows[0][key], options.get(key))

    def test_jira_cli_forwards_options_to_each_sequential_or_combined_group(self):
        for mode in ("sequential", "single_person_multi_sample"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                root = Path(tmp)
                samples = [root / "gomore_data_a.csv", root / "gomore_data_b.csv"]
                for sample in samples:
                    sample.write_text("input")
                flows = []
                self.patches(stack, run_lthr_offline_sim, root, flows)
                stack.enter_context(patch.object(run_lthr_offline_sim, "resolve_issue_sample_inputs", return_value=({}, samples, None)))
                options = OPTIONS if mode == "sequential" else {key: value for key, value in OPTIONS.items() if key in ("start_time", "interval_seconds")}
                argv = ["sim", "ITS-1", "--skip-jira", "--all-csvs", "--multi-attachment-mode", mode,
                        "--rounds", "2", "--work-dir", str(root / "output")]
                for key, value in options.items():
                    argv.extend(["--" + key.replace("_", "-"), str(value)])
                stack.enter_context(patch.object(sys, "argv", argv))
                self.assertEqual(run_lthr_offline_sim.main(), 0)
                self.assertEqual(len(flows), 2 if mode == "sequential" else 1)
                for flow in flows:
                    for key in OPTIONS:
                        self.assertEqual(flow[key], options.get(key))

    def test_invalid_options_stop_before_device_access(self):
        cases = [
            (run_device_multi_sim, ["--mode", "single_sample", "--rounds", "2", "--start-time", "0"]),
            (run_device_multi_sim, ["--mode", "multi_person_multi_sample", "--rounds", "2", "--clear-history-on-start", "1"]),
            (run_device_multi_sim, ["--img-only", "--clear-history-on-end", "0"]),
            (run_lthr_offline_sim, ["ITS-1", "--interval-seconds", "0"]),
            (run_lthr_offline_sim, ["ITS-1", "--multi-attachment-mode", "single_person_multi_sample", "--clear-history-on-end", "0"]),
        ]
        for module, argv in cases:
            with self.subTest(module=module.__name__, argv=argv), patch.object(module, "housekeep_output"), \
                 patch.object(module, "build_wlctl_path") as device, contextlib.redirect_stderr(io.StringIO()), \
                 patch.object(sys, "argv", ["sim", *argv]):
                with self.assertRaises(SystemExit) as exc:
                    module.main()
                self.assertEqual(exc.exception.code, 2)
                device.assert_not_called()
