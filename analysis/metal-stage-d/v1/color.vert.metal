#pragma clang diagnostic ignored "-Wmissing-prototypes"
#pragma clang diagnostic ignored "-Wmissing-braces"

#include <metal_stdlib>
#include <simd/simd.h>

using namespace metal;

template<typename T, size_t Num>
struct spvUnsafeArray
{
    T elements[Num ? Num : 1];

    thread T& operator [] (size_t pos) thread
    {
        return elements[pos];
    }
    constexpr const thread T& operator [] (size_t pos) const thread
    {
        return elements[pos];
    }

    device T& operator [] (size_t pos) device
    {
        return elements[pos];
    }
    constexpr const device T& operator [] (size_t pos) const device
    {
        return elements[pos];
    }

    constexpr const constant T& operator [] (size_t pos) const constant
    {
        return elements[pos];
    }

    threadgroup T& operator [] (size_t pos) threadgroup
    {
        return elements[pos];
    }
    constexpr const threadgroup T& operator [] (size_t pos) const threadgroup
    {
        return elements[pos];
    }
};

struct VS_INPUT
{
    float2 vPosition;
    float4 vColor;
};

struct VS_OUTPUT
{
    float4 vPosition;
    float4 vColor;
};

struct main0_out
{
    float4 v_TEXCOORD1 [[user(locn0)]];
    float4 gl_Position [[position]];
};

struct main0_in
{
    float2 in_POSITION [[attribute(0)]];
    float4 in_PDX_COLOR [[attribute(1)]];
};

static inline __attribute__((always_inline))
float4 mul(thread const float4x4& X, thread const float4& Y)
{
    return X * Y;
}

static inline __attribute__((always_inline))
VS_OUTPUT glsl_main(VS_INPUT v, constant spvUnsafeArray<float4, 4>& UNIFORM0)
{
    float4 vPosition = float4(v.vPosition.x, v.vPosition.y, 0.0, 1.0);
    float4x4 param = float4x4(float4(UNIFORM0[0]), float4(UNIFORM0[1]), float4(UNIFORM0[2]), float4(UNIFORM0[3]));
    float4 param_1 = vPosition;
    VS_OUTPUT Out;
    Out.vPosition = mul(param, param_1);
    Out.vColor = v.vColor;
    return Out;
}

vertex main0_out main0(main0_in in [[stage_in]], constant spvUnsafeArray<float4, 4>& UNIFORM0 [[buffer(0)]])
{
    main0_out out = {};
    VS_INPUT glsl_vertex;
    glsl_vertex.vPosition = in.in_POSITION;
    glsl_vertex.vColor = in.in_PDX_COLOR;
    VS_OUTPUT out_glsl_vertex = glsl_main(glsl_vertex, UNIFORM0);
    out.gl_Position = out_glsl_vertex.vPosition;
    out.v_TEXCOORD1 = out_glsl_vertex.vColor;
    return out;
}
