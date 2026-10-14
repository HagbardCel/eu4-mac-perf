import unittest

from reverse_engineering.corpus import MANDATORY_ANCHOR_SYMBOLS
from reverse_engineering.symbol_resolve import (
    SeedResolutionError,
    build_symbol_index,
    demangle_itanium,
    mach_o_demangle_input,
    normalize_demangled,
    resolve_symbol_to_rva,
)


def _short_inventory() -> dict[str, dict]:
    return {
        "0x00000000015ebc40": {
            "kind": "internal",
            "name_mangled": "SetAll",
            "name_demangled": "SShaderOpenGL::SetAll",
            "namespace": "SShaderOpenGL",
        },
        "0x00000000014c7da4": {
            "kind": "internal",
            "name_mangled": "RenderBuckets",
            "name_demangled": "CPdxMeshObject::RenderBuckets",
            "namespace": "CPdxMeshObject",
        },
        "0x0000000000ed4ac8": {
            "kind": "internal",
            "name_mangled": "Render",
            "name_demangled": "CEU3GraphicalMap::Render",
            "namespace": "CEU3GraphicalMap",
        },
        "0x00000000010cbb00": {
            "kind": "internal",
            "name_mangled": "DrawBorders",
            "name_demangled": "CPdxMapBorderLayer::DrawBorders",
            "namespace": "CPdxMapBorderLayer",
        },
    }


class SymbolResolveTests(unittest.TestCase):
    def test_normalize_demangled_strips_params_and_parens(self) -> None:
        self.assertEqual(
            normalize_demangled("SShaderOpenGL::SetAll()"),
            "SShaderOpenGL::SetAll",
        )
        self.assertEqual(
            normalize_demangled(
                "CPdxMapBorderLayer::DrawBorders(GfxDeferredContextGFX*, CEU3Camera const*)"
            ),
            "CPdxMapBorderLayer::DrawBorders",
        )

    def test_mach_o_demangle_input_strips_leading_underscore(self) -> None:
        sym = "__ZN13SShaderOpenGL6SetAllEv"
        self.assertEqual(mach_o_demangle_input(sym), "_ZN13SShaderOpenGL6SetAllEv")

    def test_mach_o_double_underscore_demangles(self) -> None:
        demangle_itanium.cache_clear()
        out = demangle_itanium("__ZN13SShaderOpenGL6SetAllEv")
        self.assertEqual(normalize_demangled(out), "SShaderOpenGL::SetAll")

    def test_mandatory_anchors_on_short_inventory(self) -> None:
        functions = _short_inventory()
        index = build_symbol_index(functions)
        expected = {
            MANDATORY_ANCHOR_SYMBOLS[0]: "0x00000000015ebc40",
            MANDATORY_ANCHOR_SYMBOLS[1]: "0x00000000014c7da4",
            MANDATORY_ANCHOR_SYMBOLS[2]: "0x0000000000ed4ac8",
        }
        for sym, rva in expected.items():
            self.assertEqual(
                resolve_symbol_to_rva(functions, sym, index=index),
                rva,
                sym,
            )

    def test_border_hook_itanium_resolves(self) -> None:
        functions = _short_inventory()
        index = build_symbol_index(functions)
        border_sym = (
            "_ZN18CPdxMapBorderLayer11DrawBordersEP21GfxDeferredContextGFXPK10CEU3Camera"
            "R18SBorderDrawInfoSet6CArrayIiEiifP19CWrapWorldShadowMapc"
        )
        self.assertEqual(
            resolve_symbol_to_rva(functions, border_sym, index=index),
            "0x00000000010cbb00",
        )

    def test_ambiguous_demangled_raises(self) -> None:
        functions = {
            "0x1000": {
                "kind": "internal",
                "name_mangled": "RenderBuckets",
                "name_demangled": "CAnimatedMapText::RenderBuckets",
                "namespace": "CAnimatedMapText",
            },
            "0x2000": {
                "kind": "internal",
                "name_mangled": "RenderBuckets",
                "name_demangled": "CAnimatedMapText::RenderBuckets",
                "namespace": "CAnimatedMapText",
            },
        }
        index = build_symbol_index(functions)
        with self.assertRaises(SeedResolutionError) as ctx:
            resolve_symbol_to_rva(
                functions,
                "CAnimatedMapText::RenderBuckets",
                index=index,
            )
        self.assertIn("ambiguous", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
