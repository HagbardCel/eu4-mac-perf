#version 120
uniform sampler2D SimpleTexture;
varying vec2 v_TEXCOORD0;
varying vec4 v_TEXCOORD1;
void main() {
    vec4 OutColor = texture2D(SimpleTexture, v_TEXCOORD0);
    OutColor = OutColor * v_TEXCOORD1;
    gl_FragColor = OutColor;
}
