#version 120
uniform vec4 UNIFORM0[4];
#define Matrix mat4(UNIFORM0[0], UNIFORM0[1], UNIFORM0[2], UNIFORM0[3])
attribute vec4 in_POSITION;
attribute vec2 in_TEXCOORD0;
attribute vec4 in_PDX_COLOR;
varying vec2 v_TEXCOORD0;
varying vec4 v_TEXCOORD1;
void main() {
    gl_Position = Matrix * in_POSITION;
    v_TEXCOORD0 = in_TEXCOORD0;
    v_TEXCOORD1 = in_PDX_COLOR;
}
