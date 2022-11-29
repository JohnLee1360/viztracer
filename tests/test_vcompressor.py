# Licensed under the Apache License: http://www.apache.org/licenses/LICENSE-2.0
# For details: https://github.com/gaogaotiantian/viztracer/blob/master/NOTICE.txt


import logging
import lzma
import os
import tempfile
import json
import unittest
from collections import namedtuple
from functools import wraps
from shutil import copyfileobj
from typing import Callable, List, Optional, Tuple, overload

from .cmdline_tmpl import CmdlineTmpl
from .test_performance import Timer
from .util import get_tests_data_file_path


class TestVCompressor(CmdlineTmpl):
    def test_basic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cvf_path = os.path.join(tmpdir, "result.cvf")
            dup_json_path = os.path.join(tmpdir, "result.json")
            self.template(
                ["viztracer", "-o", cvf_path, "--compress", get_tests_data_file_path("multithread.json")],
                expected_output_file=cvf_path, cleanup=False
            )

            self.template(
                ["viztracer", "-o", dup_json_path, "--decompress", cvf_path],
                expected_output_file=dup_json_path
            )

    def test_compress_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cvf_path = os.path.join(tmpdir, "result.cvf")
            not_exist_path = os.path.join(tmpdir, "do_not_exist.json")
            result = self.template(
                ["viztracer", "-o", cvf_path, "--compress", not_exist_path],
                expected_output_file=None, success=False
            )
            self.assertIn("Unable to find file", result.stdout.decode("utf8"))

            result = self.template(
                ["viztracer", "-o", cvf_path, "--compress", get_tests_data_file_path("fib.py")],
                expected_output_file=None, success=False
            )
            self.assertIn("Only support compressing json report", result.stdout.decode("utf8"))

    def test_compress_default_outputfile(self):
        default_compress_output = "result.cvf"
        self.template(
            ["viztracer", "--compress", get_tests_data_file_path("multithread.json")],
            expected_output_file=default_compress_output, cleanup=False
        )
        self.assertTrue(os.path.exists(default_compress_output))

        self.template(
            ["viztracer", "-o", "result.json", "--decompress", default_compress_output],
            expected_output_file="result.json"
        )
        self.cleanup(output_file=default_compress_output)

    def test_decompress_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            not_exist_path = os.path.join(tmpdir, "result.cvf")
            dup_json_path = os.path.join(tmpdir, "result.json")
            result = self.template(
                ["viztracer", "-o", dup_json_path, "--decompress", not_exist_path],
                expected_output_file=dup_json_path, success=False
            )
            self.assertIn("Unable to find file", result.stdout.decode("utf8"))

    def test_decompress_default_outputfile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cvf_path = os.path.join(tmpdir, "result.cvf")
            default_decompress_output = "result.json"
            self.template(
                ["viztracer", "-o", cvf_path, "--compress", get_tests_data_file_path("multithread.json")],
                expected_output_file=cvf_path, cleanup=False
            )

            self.template(
                ["viztracer", "--decompress", cvf_path],
                expected_output_file=default_decompress_output, cleanup=False
            )
            self.assertTrue(os.path.exists(default_decompress_output))
            self.cleanup(output_file=default_decompress_output)


class TestVCompressorPerformance(CmdlineTmpl):

    BenchmarkResult = namedtuple("BenchmarkResult", ["file_size", "elapsed_time"])  # unit: byte, second

    @overload
    def _benchmark(benchmark_process: Callable[..., None]):
        ...

    @overload
    def _benchmark(repeat: int):
        ...

    def _benchmark(*args, **kargs):
        def _decorator(benchmark_process: Callable) -> Callable:
            @wraps(benchmark_process)
            def _wrapper(self, uncompressed_file_path: str) -> "TestVCompressorPerformance.BenchmarkResult":
                compression_time_total = 0.
                with tempfile.TemporaryDirectory() as tmpdir:
                    compressed_file_path = os.path.join(tmpdir, "result.compressed")
                    # pre-warm
                    benchmark_process(self, uncompressed_file_path, compressed_file_path)
                    os.remove(compressed_file_path)
                    # real benchmark
                    for _ in range(loop_time):
                        with Timer() as t:
                            benchmark_process(self, uncompressed_file_path, compressed_file_path)
                            compression_time_total += t.get_time()
                        compressed_file_size = os.path.getsize(compressed_file_path)
                        os.remove(compressed_file_path)
                return TestVCompressorPerformance.BenchmarkResult(compressed_file_size, compression_time_total / loop_time)
            return _wrapper

        if len(args) == 0 and len(kargs) == 0:
            raise TypeError("_benchmark must decorate a function.")

        # used as @_benchmark
        if len(args) == 1 and len(kargs) == 0 and callable(args[0]):
            loop_time = 3
            return _decorator(args[0])

        # used as @_benchmark(...)
        loop_time = kargs["repeat"] if "repeat" in kargs else args[0]
        return _decorator

    @staticmethod
    def _human_readable_filesize(filesize: int) -> str:  # filesize in bytes
        units = [("PB", 1 << 50), ("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]
        for unit_name, unit_base in units:
            norm_size = filesize / unit_base
            if norm_size >= 0.8:
                return f"{norm_size:8.2f}{unit_name}"
        return f"{filesize:8.2f}B"

    @classmethod
    def _print_result(
        cls,
        filename: str,
        original_size: int,
        vcompress_result: BenchmarkResult,
        other_results: List[Tuple[str, BenchmarkResult]],  # [(compressor_name, BenchmarkResult)]
        subtest_idx: Optional[int] = None
    ):
        if subtest_idx is None:
            logging.info(f"On file \"{filename}\":")
        else:
            logging.info(f"{subtest_idx}. On file \"{filename}\":")

        # Space-wise Info
        logging.info("    [Space]")
        logging.info("      Uncompressed:   {}".format(
            cls._human_readable_filesize(original_size)),
        )
        logging.info("      VCompressor:    {}(1.000) [CR:{:6.2f}%]".format(  # CR stands for compress ratio
            cls._human_readable_filesize(vcompress_result.file_size),
            vcompress_result.file_size / original_size * 100,
        ))
        for name, result in other_results:
            logging.info("      {}{}({:.3f}) [CR:{:6.2f}%]".format(
                name + ":" + " " * max(15 - len(name), 0),
                cls._human_readable_filesize(result.file_size),
                result.file_size / vcompress_result.file_size,
                result.file_size / original_size * 100,
            ))

        # Time-wise Info
        logging.info("    [Time]")
        logging.info("      VCompressor:    {:9.3f}s(1.000)".format(
            vcompress_result.elapsed_time,
        ))
        for name, result in other_results:
            logging.info("      {}{:9.3f}s({:.3f})".format(
                name + ":" + " " * max(15 - len(name), 0),
                result.elapsed_time,
                result.elapsed_time / vcompress_result.elapsed_time
            ))

    @_benchmark
    def _benchmark_vcompressor(self, uncompressed_file_path: str, compressed_file_path: str) -> None:
        self.template(
            ["viztracer", "-o", compressed_file_path, "--compress", uncompressed_file_path],
            expected_output_file=compressed_file_path, script=None, cleanup=False
        )

    @_benchmark
    def _benchmark_lzma(self, uncompressed_file_path: str, compressed_file_path: str) -> None:
        with open(uncompressed_file_path, "rb") as original_file:
            with lzma.open(compressed_file_path, "wb", preset=lzma.PRESET_DEFAULT) as compressed_file:
                copyfileobj(original_file, compressed_file)

    def test_benchmark_basic(self):
        # More testcases can be added here
        testcases_filename = ["vdb_basic.json", "multithread.json"]

        for subtest_idx, filename in enumerate(testcases_filename, start=1):
            path = get_tests_data_file_path(filename)
            original_size = os.path.getsize(path)
            # More compressors can be added here
            other_results = [
                ("LZMA", self._benchmark_lzma(path)),
            ]
            with self.subTest(testcase=filename):
                vcompress_result = self._benchmark_vcompressor(path)
                self._print_result(filename, original_size,
                                   vcompress_result, other_results, subtest_idx=subtest_idx)


class VCompressorCompare(unittest.TestCase):
    def assertCounterEventsEqual(self, first: list, second: list, ts_margin: float):
        """
        This method is used to assert if two lists of counter events are equal,
        first and second are the two lists that we compare,
        ts_margin is the max timestamps diff that we tolerate.
        The timestamps may changed before/after the compression for more effective compression
        """
        self.assertEqual(len(first), len(second),
                         f"list length not equal, first is {len(first)} \n second is {len(second)}")
        first.sort(key=lambda i: i["ts"])
        second.sort(key=lambda i: i["ts"])
        for i in range(len(first)):
            self.assertEventEqual(first[i], second[i], ts_margin)

    def assertEventEqual(self, first: dict, second: dict, ts_margin: float):
        """
        This method is used to assert if two events are equal,
        first and second are the two events that we compare,
        ts_margin is the max timestamps diff that we tolerate.
        The timestamps may changed before/after the compression for more effective compression
        """
        self.assertEqual(len(first), len(second),
                         f"event length not equal, first is: \n {str(first)} \n second is: \n {str(second)}")
        for key, value in first.items():
            if key in ["ts", "dur"]:
                self.assertGreaterEqual(ts_margin, abs(value - second[key]),
                                        f"{key} diff is greater than margin")
            else:
                self.assertEqual(value, second[key], f"{key} is not equal")


test_counter_events = """
import threading
import time
import sys
from viztracer import VizTracer, VizCounter

tracer = VizTracer()
tracer.start()

class MyThreadSparse(threading.Thread):
    def run(self):
        counter = VizCounter(tracer, 'thread counter ' + str(self.ident))
        counter.a = sys.maxsize - 1
        time.sleep(0.01)
        counter.a = sys.maxsize * 2
        time.sleep(0.01)
        counter.a = -sys.maxsize + 2
        time.sleep(0.01)
        counter.a = -sys.maxsize * 2

main_counter = VizCounter(tracer, 'main counter')
thread1 = MyThreadSparse()
thread2 = MyThreadSparse()
main_counter.arg1 = 100.01
main_counter.arg2 = -100.01
main_counter.arg3 = 0.0
delattr(main_counter, \"arg3\")

thread1.start()
thread2.start()

threads = [thread1, thread2]

for thread in threads:
    thread.join()

main_counter.arg1 = 200.01
main_counter.arg2 = -200.01

tracer.stop()
tracer.save(output_file='%s')
"""


class TestVCompressorCorrectness(CmdlineTmpl, VCompressorCompare):
    def test_file_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cvf_path = os.path.join(tmpdir, "result.cvf")
            dup_json_path = os.path.join(tmpdir, "result.json")
            self.template(
                ["viztracer", "-o", cvf_path, "--compress", get_tests_data_file_path("multithread.json")],
                expected_output_file=cvf_path, cleanup=False
            )
            self.template(
                ["viztracer", "-o", dup_json_path, "--decompress", cvf_path],
                expected_output_file=dup_json_path, cleanup=False
            )

            with open(get_tests_data_file_path("multithread.json"), "r") as f:
                origin_json_data = json.load(f)
            with open(dup_json_path, "r") as f:
                dup_json_data = json.load(f)

            self.assertEqual(origin_json_data["file_info"], dup_json_data["file_info"])

    def test_counter_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            origin_json_path = os.path.join(tmpdir, "result.json")
            cvf_path = os.path.join(tmpdir, "result.cvf")
            dup_json_path = os.path.join(tmpdir, "recovery.json")
            run_script = test_counter_events % (origin_json_path.replace("\\", "/"))
            self.template(
                ["python", "cmdline_test.py"], script=run_script, cleanup=False,
                expected_output_file=origin_json_path
            )
            self.template(
                ["viztracer", "-o", cvf_path, "--compress", origin_json_path],
                expected_output_file=cvf_path, cleanup=False
            )
            self.template(
                ["viztracer", "-o", dup_json_path, "--decompress", cvf_path],
                expected_output_file=dup_json_path, cleanup=False
            )

            with open(origin_json_path, "r") as f:
                origin_json_data = json.load(f)
            with open(dup_json_path, "r") as f:
                dup_json_data = json.load(f)

            origin_counter_events = [i for i in origin_json_data["traceEvents"] if i["ph"] == "C"]
            dup_counter_events = [i for i in dup_json_data["traceEvents"] if i["ph"] == "C"]

            self.assertCounterEventsEqual(origin_counter_events, dup_counter_events, 0.01)
