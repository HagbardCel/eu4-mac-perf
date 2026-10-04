import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"


class SubrecordEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib_path = Path(tempfile.gettempdir()) / "libsubrecord_equivalence_test.so"
        subprocess.run(
            [
                "cc",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-shared",
                "-fPIC",
                "-I",
                str(BENCH),
                "-o",
                str(cls.lib_path),
                str(BENCH / "subrecord_equivalence.c"),
            ],
            check=True,
        )
        cls.lib = ctypes.CDLL(str(cls.lib_path))

        class Context(ctypes.Structure):
            _fields_ = [
                ("parent_sflushdata_id", ctypes.c_uint64),
                ("layer_index", ctypes.c_uint32),
                ("flush_array_kind", ctypes.c_uint32),
                ("same_parent_boundary", ctypes.c_bool),
                ("chain_broken", ctypes.c_bool),
                ("has_immediate_predecessor", ctypes.c_bool),
            ]

        cls.Context = Context
        pred_args = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(Context),
            ctypes.POINTER(Context),
            ctypes.POINTER(ctypes.c_int),
        ]
        for name in (
            "eu4_submission_state_equivalent",
            "eu4_setup_elision_eligible",
            "eu4_buffer_bind_elision_eligible",
            "eu4_draw_batch_eligible",
        ):
            fn = getattr(cls.lib, name)
            fn.argtypes = pred_args
            fn.restype = ctypes.c_bool

    def _ctx(self, parent_id=1, layer=0, flush=0, same_parent=True, broken=False, has_prev=True):
        return self.Context(parent_id, layer, flush, same_parent, broken, has_prev)

    def test_chain_reset_blocks_comparison(self):
        parent = (ctypes.c_uint8 * 80)(*([0] * 80))
        sub_a = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_b = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_d = (ctypes.c_uint8 * 232)(*([0] * 232))
        reason = ctypes.c_int()
        ctx_a = self._ctx()
        ctx_b = self._ctx()
        ctx_c = self._ctx(broken=True)
        ctx_d = self._ctx(has_prev=False)
        self.assertTrue(
            self.lib.eu4_submission_state_equivalent(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )
        self.assertFalse(
            self.lib.eu4_submission_state_equivalent(
                parent, parent, sub_b, sub_d, ctypes.byref(ctx_b), ctypes.byref(ctx_c), ctypes.byref(reason)
            )
        )
        self.assertFalse(
            self.lib.eu4_submission_state_equivalent(
                parent, parent, sub_b, sub_d, ctypes.byref(ctx_b), ctypes.byref(ctx_d), ctypes.byref(reason)
            )
        )

    def test_parent_boundary_blocks(self):
        parent = (ctypes.c_uint8 * 80)(*([0] * 80))
        sub_a = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_b = (ctypes.c_uint8 * 232)(*([0] * 232))
        reason = ctypes.c_int()
        ctx_a = self._ctx(parent_id=1, same_parent=True)
        ctx_b = self._ctx(parent_id=2, same_parent=True)
        self.assertFalse(
            self.lib.eu4_submission_state_equivalent(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )

    def test_unknown_barrier_bytes_identical_still_fail_setup_elision(self):
        parent = (ctypes.c_uint8 * 80)(*([0] * 80))
        sub_a = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_b = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_a[0] = 1
        sub_b[0] = 2
        reason = ctypes.c_int()
        ctx_a = self._ctx()
        ctx_b = self._ctx()
        self.assertFalse(
            self.lib.eu4_setup_elision_eligible(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )
        sub_b[0] = 1
        self.assertFalse(
            self.lib.eu4_setup_elision_eligible(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )

    def test_buffer_bind_elision_uses_only_proven_fields(self):
        parent = (ctypes.c_uint8 * 80)(*([0] * 80))
        sub_a = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_b = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_a[0] = 9
        sub_b[0] = 3
        reason = ctypes.c_int()
        ctx_a = self._ctx()
        ctx_b = self._ctx()
        self.assertTrue(
            self.lib.eu4_buffer_bind_elision_eligible(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )

    def test_draw_batch_not_proven(self):
        parent = (ctypes.c_uint8 * 80)(*([0] * 80))
        sub_a = (ctypes.c_uint8 * 232)(*([0] * 232))
        sub_b = (ctypes.c_uint8 * 232)(*([0] * 232))
        reason = ctypes.c_int()
        ctx_a = self._ctx()
        ctx_b = self._ctx()
        self.assertFalse(
            self.lib.eu4_draw_batch_eligible(
                parent, parent, sub_a, sub_b, ctypes.byref(ctx_a), ctypes.byref(ctx_b), ctypes.byref(reason)
            )
        )


if __name__ == "__main__":
    unittest.main()
