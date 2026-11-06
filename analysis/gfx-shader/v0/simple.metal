#include <metal_stdlib>
using namespace metal;

struct VSIn {
    float4 position [[attribute(0)]];
    float2 texcoord [[attribute(1)]];
    float4 color    [[attribute(2)]];
};

struct VSOut {
    float4 position [[position]];
    float2 texcoord;
    float4 color;
};

struct Uniforms {
    float4 UNIFORM0[4];
};

vertex VSOut simple_vs(VSIn in [[stage_in]],
                       constant Uniforms &u [[buffer(1)]]) {
    float4x4 Matrix = float4x4(u.UNIFORM0[0], u.UNIFORM0[1],
                               u.UNIFORM0[2], u.UNIFORM0[3]);
    VSOut out;
    out.position = Matrix * in.position;
    out.texcoord = in.texcoord;
    out.color = in.color;
    return out;
}

fragment float4 simple_fs(VSOut in [[stage_in]],
                          texture2d<float> SimpleTexture [[texture(0)]],
                          sampler SimpleSampler [[sampler(0)]]) {
    float4 tex = SimpleTexture.sample(SimpleSampler, in.texcoord);
    return tex * in.color;
}
