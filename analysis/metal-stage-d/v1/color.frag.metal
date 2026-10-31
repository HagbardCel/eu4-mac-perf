#pragma clang diagnostic ignored "-Wmissing-prototypes"

#include <metal_stdlib>
#include <simd/simd.h>

using namespace metal;

struct VS_OUTPUT
{
    float4 vPosition;
    float4 vColor;
};

struct main0_out
{
    float4 frag_color [[color(0)]];
};

struct main0_in
{
    float4 v_TEXCOORD1 [[user(locn0)]];
};

static inline __attribute__((always_inline))
float4 glsl_main(thread const VS_OUTPUT& v)
{
    return v.vColor;
}

fragment main0_out main0(main0_in in [[stage_in]])
{
    main0_out out = {};
    VS_OUTPUT glsl_vertex;
    glsl_vertex.vColor = in.v_TEXCOORD1;
    VS_OUTPUT param = glsl_vertex;
    out.frag_color = glsl_main(param);
    return out;
}
